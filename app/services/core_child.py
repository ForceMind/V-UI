"""Single-threaded Linux watchdog. Watches the panel process, not a worker thread."""
import ctypes
import os
import select
import signal
import subprocess
import sys


def main():
    if not sys.platform.startswith("linux") or not hasattr(os, "pidfd_open"):
        return 70
    parent = int(sys.argv[1])
    try:
        parent_fd = os.pidfd_open(parent)
    except ProcessLookupError:
        return 70
    if os.getppid() != parent:
        os.close(parent_fd)
        return 70

    def parent_death():
        # Safe here: this watchdog has one thread; no threaded FastAPI preexec.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            os._exit(70)

    child = subprocess.Popen(sys.argv[2:], preexec_fn=parent_death)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    poller = select.poll()
    poller.register(parent_fd, select.POLLIN)
    try:
        while not stopping and child.poll() is None:
            if poller.poll(100):
                stopping = True
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        return child.returncode if child.returncode >= 0 else 0
    finally:
        os.close(parent_fd)


if __name__ == "__main__":
    raise SystemExit(main())
