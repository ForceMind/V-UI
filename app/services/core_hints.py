"""Hard-lifetime broker for fixed non-Root, metadata-only discovery.

The trusted worker does not fork or execute other programs. Cleanup kills its
owned process group on failure and reaps the direct worker before releasing a
slot. This does not claim supervision of arbitrary executables or escape groups.
"""
from __future__ import annotations
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time

from app.core_updates.discovery_worker import CORES, MAX_OUTPUT, validate_result

WORKER = Path(__file__).resolve().parents[1]/'core_updates/discovery_worker.py'
DEADLINE_SECONDS = 12
SLOTS = threading.BoundedSemaphore(2)


class CoreHintsUnavailable(RuntimeError):
    def __init__(self, code):
        self.code=code
        super().__init__('官方核心候选信息暂不可用')


def discover(core):
    if type(core) is not str or core not in CORES:
        raise CoreHintsUnavailable('CORE')
    if not SLOTS.acquire(blocking=False):
        raise CoreHintsUnavailable('BUSY')
    child = None
    try:
        deadline = time.monotonic()+DEADLINE_SECONDS
        child = subprocess.Popen([sys.executable,'-B','-I','-S',str(WORKER),core],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},cwd='/',start_new_session=True)
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            os.set_blocking(child.stdout.fileno(),False)
            selector.register(child.stdout,selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise CoreHintsUnavailable('DEADLINE')
                for key,_ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(),min(8192,MAX_OUTPUT+1-len(output)))
                    if not chunk:
                        selector.unregister(key.fileobj);continue
                    output.extend(chunk)
                    if len(output)>MAX_OUTPUT:
                        raise CoreHintsUnavailable('OUTPUT')
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise CoreHintsUnavailable('DEADLINE')
        if child.wait(timeout=remaining) != 0:
            raise CoreHintsUnavailable('UNAVAILABLE')
        if time.monotonic() >= deadline:
            raise CoreHintsUnavailable('DEADLINE')
        try:
            value = validate_result(bytes(output),core)
            if time.monotonic() >= deadline:
                raise CoreHintsUnavailable('DEADLINE')
            return value
        except (ValueError,TypeError,KeyError,UnicodeError):
            raise CoreHintsUnavailable('RESULT') from None
    except subprocess.TimeoutExpired:
        raise CoreHintsUnavailable('DEADLINE') from None
    except OSError:
        raise CoreHintsUnavailable('UNAVAILABLE') from None
    finally:
        release_slot = child is None
        cleanup_failed = False
        if child is not None:
            try:
                if child.returncode is None:
                    try:
                        os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        cleanup_failed = True
                    try:
                        child.wait(timeout=2)
                        release_slot = True
                    except (OSError,subprocess.SubprocessError):
                        cleanup_failed = True
                else:
                    release_slot = True  # A preceding wait confirmed reaping.
            finally:
                try:
                    if child.stdout is not None:child.stdout.close()
                except OSError:
                    cleanup_failed = True
        # An uncertain cleanup keeps its slot occupied until service restart;
        # repeated requests cannot create an unbounded unreaped-worker set.
        if release_slot and not cleanup_failed:
            SLOTS.release()
        if cleanup_failed:
            raise CoreHintsUnavailable('CLEANUP') from None
