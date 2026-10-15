"""Hermetic Bash regressions for install.sh, without installing anything.

The real shell script runs against a private PATH of fake tools. Release checksums
are computed and verified by real Python; portable archive tools are deterministic
fakes so no network, package manager, root access, or host configuration is used.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "install.sh"
BASH = shutil.which("bash")
INSTALLER = """import json, os, pathlib, signal, sys
with pathlib.Path(os.environ['TEST_LOG']).open('a') as log:
    log.write(json.dumps({'tool':'installer', 'args':sys.argv[1:], 'file':__file__,
                          'pythonpath':os.environ.get('PYTHONPATH')}) + '\\n')
if os.environ.get('TEST_SIGNAL_STAGE') == 'installer':
    owner = int(os.environ['TEST_INSTALL_SH_PID'])
    signum = getattr(signal, 'SIG' + os.environ['TEST_SIGNAL'])
    if os.environ.get('TEST_SIGNAL_GROUP'):
        if os.getpgrp() != owner:
            raise SystemExit('Refusing to signal a non-isolated test process group')
        os.killpg(owner, signum)
    else:
        os.kill(owner, signum)
raise SystemExit(int(os.environ.get('TEST_INSTALLER_STATUS', '0')))
"""

PORTABLE_PINS = {
    "x86_64-gnu": "72748da13197c1fb161e3afeef20a6a385ff24f2165e6e2758e47008e7faba4c",
    "x86_64-musl": "1f37044c8cdbd74d5ee112a753c65ef209fedd169c98f3e4e748a93e27eb27a4",
    "aarch64-gnu": "577b4bec0793ad1ff0cbff9adbd0df078eddde38a4c41bf5d83ad381a85ee39d",
    "aarch64-musl": "a0ad6f01b9204eba573a08927097b78143c564f98bea68a41ea1e172f041da3a",
}

# Every executable on the private PATH is this dispatcher. Unknown commands fail
# instead of falling back to a real host command.
FAKE_TOOL = r'''
import hashlib, json, os, pathlib, shutil, signal, sys, tempfile
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['TEST_ROOT']).resolve()
log_path = pathlib.Path(os.environ['TEST_LOG'])
def log(**extra):
    with log_path.open('a') as stream:
        stream.write(json.dumps(dict(tool=name, args=args, **extra)) + '\n')
def owned(value):
    path = pathlib.Path(value).resolve()
    if root not in path.parents:
        raise SystemExit('Refusing non-test path: ' + str(path))
    return path
def trigger(stage):
    if os.environ.get('TEST_SIGNAL_STAGE') != stage:
        return
    if stage == 'mktemp':
        count = sum('created' in json.loads(line) for line in log_path.read_text().splitlines())
        if count != int(os.environ.get('TEST_SIGNAL_ALLOCATION', '1')):
            return
    log(signal_sent=os.environ['TEST_SIGNAL'])
    owner = int(os.environ['TEST_INSTALL_SH_PID'])
    signum = getattr(signal, 'SIG' + os.environ['TEST_SIGNAL'])
    if os.environ.get('TEST_SIGNAL_GROUP'):
        if os.getpgrp() != owner:
            raise SystemExit('Refusing to signal a non-isolated test process group')
        os.killpg(owner, signum)
    else:
        os.kill(owner, signum)
log()
if name in ('python3', 'python'):
    if args[:1] == ['-c']:
        raise SystemExit(int(os.environ.get('TEST_PYTHON_PROBE_STATUS', '0')))
    os.execv(sys.executable, [sys.executable, *args])
elif name == 'dirname':
    print(str(pathlib.Path(args[-1]).parent))
elif name == 'uname':
    print(os.environ.get('TEST_ARCH', 'x86_64'))
elif name == 'ls':
    raise SystemExit(1)
elif name == 'ldd':
    print(os.environ.get('TEST_LIBC', 'gnu'))
elif name == 'grep':
    raise SystemExit(0 if 'musl' in sys.stdin.read().lower() else 1)
elif name == 'mktemp':
    if os.environ.get('TEST_MKTEMP_FAIL'):
        raise SystemExit(9)
    if os.environ.get('TEST_MKTEMP_EMPTY_SUCCESS'):
        raise SystemExit(0)
    path = pathlib.Path(tempfile.mkdtemp(prefix='owned-', dir=root / 'temporary'))
    log(created=str(path))
    print(path, flush=True)
    trigger('mktemp')
    if os.environ.get('TEST_MKTEMP_PRINT_FAIL'):
        raise SystemExit(9)
elif name == 'rm':
    for value in args:
        if value.startswith('-'):
            continue
        path = owned(value)
        if os.environ.get('TEST_RM_FAIL'):
            raise SystemExit(19)
        shutil.rmtree(path)
elif name == 'cat':
    for value in args:
        sys.stdout.write(owned(value).read_text())
elif name == 'curl':
    url = next(value for value in args if value.startswith('https://'))
    destination = owned(args[args.index('-o') + 1])
    if 'python-build-standalone' in url:
        destination.write_bytes(b'fake portable archive')
        trigger('portable-curl')
    else:
        source = root / 'release' / url.rsplit('/', 1)[-1]
        destination.write_bytes(source.read_bytes())
        trigger('release-curl')
    if os.environ.get('TEST_CURL_FAIL'):
        raise SystemExit(22)
elif name == 'sha256sum':
    value = sys.stdin.read()
    log(checksum_input=value)
    raise SystemExit(1 if os.environ.get('TEST_CHECKSUM_FAIL') else 0)
elif name == 'tar':
    destination = owned(args[args.index('-C') + 1])
    if not os.environ.get('TEST_MISSING_INTERPRETER'):
        interpreter = destination / 'python' / 'bin' / 'python3'
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(pathlib.Path(__file__).read_text())
        interpreter.chmod(0o700)
    trigger('tar')
    if os.environ.get('TEST_TAR_FAIL'):
        raise SystemExit(2)
else:
    raise SystemExit('Unexpected test executable: ' + name)
'''


@unittest.skipUnless(BASH, "Bash is required for installer shell regression tests")
class InstallerShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vui-shell-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.root / "temporary").mkdir()
        self.release = self.root / "release"
        self.release.mkdir()
        self.log_path = self.root / "commands.jsonl"
        self.script = self.root / "kit with spaces" / "install.sh"
        self.script.parent.mkdir()
        self.source = SOURCE.read_text()
        self.script.write_text(self.source)
        startup = self.root / "bash-env"
        startup.write_text('export TEST_INSTALL_SH_PID="$BASHPID"\n')
        self.env = {
            "PATH": str(self.bin),
            "HOME": str(self.root),
            "LC_ALL": "C",
            "TEST_ROOT": str(self.root),
            "TEST_LOG": str(self.log_path),
            "BASH_ENV": str(startup),
        }
        for name in ("python3", "python", "dirname", "uname", "ls", "ldd",
                     "grep", "mktemp", "rm", "cat", "curl", "sha256sum", "tar"):
            tool = self.bin / name
            tool.write_text("#!" + sys.executable + "\n" + FAKE_TOOL)
            tool.chmod(0o700)
        for name in ("install_system.py", "platform_support.py", "firewall_support.py", "service_support.py"):
            (self.release / name).write_text(INSTALLER if name == "install_system.py" else "# fake support module\n")
        for target in ("x86_64-gnu", "x86_64-musl", "aarch64-gnu", "aarch64-musl"):
            (self.release / ("vui-linux-" + target + ".zip")).write_bytes(b"fake bundle")
        self.write_checksums()

    def write_checksums(self):
        (self.release / "SHA256SUMS").write_text("".join(
            hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
            for path in sorted(self.release.iterdir()) if path.name != "SHA256SUMS"
        ))

    def local_installer(self, nested=True):
        directory = self.script.parent / "scripts" if nested else self.script.parent
        directory.mkdir(exist_ok=True)
        (directory / "install_system.py").write_text(INSTALLER)

    def run_script(self, *args, status=0, script=None, **extra_env):
        return subprocess.run(
            [BASH, str(script or self.script), *args],
            env=dict(self.env, TEST_INSTALLER_STATUS=str(status), **extra_env),
            cwd=self.root, text=True, capture_output=True, timeout=10,
            start_new_session=True,
        )

    def portable_script(self):
        """Exercise real bootstrap and main tail without root/package managers.

        Only the system-Python/prerequisite selector is omitted. The portable
        function is deliberately called in a conditional, where Bash disables
        implicit errexit, so each verification step must fail closed itself.
        No production switch or implementation replacement is involved.
        """
        prefix, separator, _ = self.source.partition('\nPYTHON="$(find_python || true)"')
        self.assertTrue(separator, "Cannot locate the prerequisite-selection boundary")
        _, separator, tail = self.source.partition('\nSCRIPT_DIR=')
        self.assertTrue(separator, "Cannot locate the installer dispatch boundary")
        probe = self.script.parent / "portable-probe.sh"
        probe.write_text(prefix + '\nif bootstrap_portable_python; then\n'
                         '  printf "%s\\n%s\\n" "$BOOTSTRAP_RUNTIME_DIR" "$PYTHON" > "$TEST_ROOT/owner"\n'
                         'else\n  exit 1\nfi\nSCRIPT_DIR=' + tail)
        return probe

    def records(self, tool=None):
        records = [json.loads(line) for line in self.log_path.read_text().splitlines()] if self.log_path.exists() else []
        return [record for record in records if tool is None or record["tool"] == tool]

    def assert_clean(self):
        self.assertEqual(list((self.root / "temporary").iterdir()), [])

    def test_online_success_preserves_zero(self):
        result = self.run_script("--version", "v0.4.7")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.records("installer")), 1)
        self.assert_clean()

    def test_online_failure_preserves_installer_status(self):
        result = self.run_script("--version", "v0.4.7", status=7)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(len(self.records("installer")), 1)
        self.assert_clean()

    def test_local_source_kit_preserves_status_and_arguments(self):
        self.local_installer()
        arguments = ["--bundle", "bundle with spaces.zip", "", "*?[literal]", "two\nlines", "--node-udp-port", "4242"]
        result = self.run_script(*arguments, status=7)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(self.records("installer")[0]["args"], arguments)
        self.assertEqual(self.records("curl"), [])
        self.assert_clean()

    def test_local_flat_kit_succeeds(self):
        self.local_installer(nested=False)
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.records("installer")[0]["args"], ["--help"])
        self.assert_clean()

    def test_online_cleanup_failure_cannot_replace_installer_failure(self):
        result = self.run_script("--version", "v0.4.7", status=7, TEST_RM_FAIL="1")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(len(self.records("installer")), 1)
        self.assertTrue(self.records("rm"))

    def test_online_cleanup_failure_cannot_replace_success(self):
        result = self.run_script("--version", "v0.4.7", TEST_RM_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.records("installer")), 1)
        self.assertTrue(self.records("rm"))

    def test_online_preserves_argument_boundaries(self):
        arguments = ["--name", "with spaces", "", "*?[literal]", "two\nlines", "$(not-a-command)"]
        result = self.run_script("--version", "v0.4.7", *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.records("installer")[0]
        self.assertEqual(record["args"][4:], arguments)
        self.assertEqual(record["args"][:1], ["--bundle"])
        bundle = Path(record["args"][1])
        self.assertEqual(bundle.name, "vui-linux-x86_64-gnu.zip")
        self.assertEqual(record["args"][2:4], ["--sha256", hashlib.sha256(b"fake bundle").hexdigest()])
        self.assertEqual(record["pythonpath"], str(bundle.parent))
        self.assert_clean()

    def test_online_downloads_use_fixed_names_and_https(self):
        result = self.run_script("--version", "v0.4.7")
        self.assertEqual(result.returncode, 0, result.stderr)
        requests = self.records("curl")
        self.assertEqual(len(requests), 6)
        self.assertEqual([record["args"][-3] for record in requests], [
            "https://github.com/ForceMind/V-UI/releases/download/v0.4.7/" + name
            for name in ("SHA256SUMS", "install_system.py", "platform_support.py",
                         "firewall_support.py", "service_support.py", "vui-linux-x86_64-gnu.zip")
        ])
        for record in requests:
            self.assertEqual(record["args"][:5], ["--proto", "=https", "--proto-redir", "=https", "--tlsv1.2"])
            self.assertNotIn("--insecure", record["args"])
            self.assertNotIn("-k", record["args"])

    def test_missing_local_installer_fails(self):
        result = self.run_script("--help")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Local installer is missing", result.stderr)
        self.assertEqual(self.records("installer"), [])
        self.assertEqual(self.records("curl"), [])
        self.assert_clean()

    def test_version_requires_explicit_stable_version(self):
        for version in ("", "latest", "v0.4", "0.4.7", "v0.4.7-rc1", "v0.4.7/../bad"):
            with self.subTest(version=version):
                result = self.run_script("--version", version)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("explicit stable", result.stderr)
        self.assertEqual(self.records("curl"), [])
        self.assert_clean()

    def test_online_download_failure_stops_before_installer(self):
        result = self.run_script("--version", "v0.4.7", TEST_CURL_FAIL="1")
        self.assertEqual(result.returncode, 22, result.stderr)
        self.assertEqual(len(self.records("curl")), 1)
        self.assertEqual(self.records("installer"), [])
        self.assert_clean()

    def test_online_checksum_mismatch_stops_before_installer(self):
        (self.release / "install_system.py").write_text(INSTALLER + "# tampered after checksumming\n")
        result = self.run_script("--version", "v0.4.7")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Release asset checksum mismatch", result.stderr)
        self.assertEqual(self.records("installer"), [])
        self.assert_clean()

    def test_online_duplicate_checksum_entry_is_rejected(self):
        checksums = self.release / "SHA256SUMS"
        checksums.write_text(checksums.read_text() + checksums.read_text().splitlines()[0] + "\n")
        result = self.run_script("--version", "v0.4.7")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid checksum list", result.stderr)
        self.assertEqual(self.records("installer"), [])
        self.assert_clean()

    def test_online_checksum_path_is_never_executed(self):
        checksums = self.release / "SHA256SUMS"
        checksums.write_text(checksums.read_text() + "0" * 64 + "  ../attacker.py\n")
        result = self.run_script("--version", "v0.4.7")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid checksum list", result.stderr)
        self.assertEqual(self.records("installer"), [])
        self.assert_clean()

    def test_online_mktemp_failure_stops_before_download(self):
        result = self.run_script("--version", "v0.4.7", TEST_MKTEMP_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.records("curl"), [])
        self.assertEqual(self.records("installer"), [])
        self.assert_clean()

    def test_portable_local_owns_runtime_and_preserves_arguments(self):
        self.local_installer()
        arguments = ["--bundle", "file with spaces", "", "*", "two\nlines"]
        result = self.run_script(*arguments, script=self.portable_script())
        self.assertEqual(result.returncode, 0, result.stderr)
        runtime, interpreter = (self.root / "owner").read_text().splitlines()
        self.assertEqual(interpreter, str(Path(runtime) / "python/bin/python3"))
        self.assertEqual(self.records("installer")[0]["args"], arguments)
        self.assert_clean()

    def test_portable_local_failure_preserves_installer_status(self):
        self.local_installer(nested=False)
        result = self.run_script("--help", status=7, script=self.portable_script())
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(len(self.records("installer")), 1)
        self.assert_clean()

    def test_portable_online_cleans_both_owned_directories(self):
        result = self.run_script("--version", "v0.4.7", script=self.portable_script())
        self.assertEqual(result.returncode, 0, result.stderr)
        created = [record["created"] for record in self.records("mktemp") if "created" in record]
        self.assertEqual(len(created), 2)
        self.assertEqual({record["args"][-1] for record in self.records("rm")}, set(created))
        self.assertEqual(len(self.records("installer")), 1)
        self.assert_clean()

    def test_portable_online_failure_cleans_both_owned_directories(self):
        result = self.run_script("--version", "v0.4.7", status=7, script=self.portable_script())
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(len(self.records("installer")), 1)
        self.assert_clean()

    def test_portable_cleanup_failure_still_attempts_both_directories(self):
        result = self.run_script("--version", "v0.4.7", status=7,
                                 script=self.portable_script(), TEST_RM_FAIL="1")
        self.assertEqual(result.returncode, 7, result.stderr)
        created = {record["created"] for record in self.records("mktemp") if "created" in record}
        self.assertEqual(len(created), 2)
        self.assertEqual({record["args"][-1] for record in self.records("rm")}, created)

    def test_portable_download_failure_cannot_reach_hash_or_extract(self):
        result = self.run_script(script=self.portable_script(), TEST_CURL_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.records("sha256sum"), [])
        self.assertEqual(self.records("tar"), [])
        self.assertEqual(self.records("installer"), [])
        self.assert_clean()

    def test_portable_checksum_failure_cannot_reach_extract(self):
        result = self.run_script(script=self.portable_script(), TEST_CHECKSUM_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.records("sha256sum"))
        self.assertEqual(self.records("tar"), [])
        self.assertEqual(self.records("installer"), [])
        self.assert_clean()

    def test_portable_partial_tar_failure_cannot_run_interpreter(self):
        # The fake tar deliberately writes an executable before failing.
        self.local_installer()
        result = self.run_script(script=self.portable_script(), TEST_TAR_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.records("tar"))
        self.assertEqual(self.records("python3"), [])
        self.assertEqual(self.records("installer"), [])
        self.assert_clean()

    def test_portable_missing_interpreter_fails_closed(self):
        result = self.run_script(script=self.portable_script(), TEST_MISSING_INTERPRETER="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.records("tar"))
        self.assertEqual(self.records("installer"), [])
        self.assert_clean()

    def test_portable_mktemp_failure_stops_before_download(self):
        result = self.run_script(script=self.portable_script(), TEST_MKTEMP_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.records("curl"), [])
        self.assertEqual(self.records("installer"), [])
        self.assert_clean()

    def test_portable_selection_does_not_lose_ownership_in_subshell(self):
        selection = self.source.split('\nPYTHON="$(find_python || true)"', 1)[1].split('\nSCRIPT_DIR=', 1)[0]
        self.assertIn("bootstrap_portable_python", selection)
        self.assertNotRegex(selection, r'\$\([^)]*bootstrap_portable_python')

    def test_portable_four_target_pins_and_https_are_unchanged(self):
        self.local_installer()
        for target, digest in PORTABLE_PINS.items():
            with self.subTest(target=target):
                if self.log_path.exists():
                    self.log_path.unlink()
                arch, libc = target.split("-")
                result = self.run_script(script=self.portable_script(), TEST_ARCH=arch, TEST_LIBC=libc)
                self.assertEqual(result.returncode, 0, result.stderr)
                request = self.records("curl")[0]["args"]
                self.assertEqual(request[:5], ["--proto", "=https", "--proto-redir", "=https", "--tlsv1.2"])
                self.assertEqual(request[-3],
                    "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/"
                    "cpython-3.12.14%2B20260901-" + arch + "-unknown-linux-" + libc + "-install_only_stripped.tar.gz")
                checksum = next(record["checksum_input"] for record in self.records("sha256sum") if "checksum_input" in record)
                self.assertEqual(checksum, digest + "  " + request[-1] + "\n")
                self.assert_clean()

    def test_online_signals_during_installer_clean_work(self):
        for sig, status in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=sig):
                result = self.run_script("--version", "v0.4.7", TEST_SIGNAL_STAGE="installer", TEST_SIGNAL=sig)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assert_clean()

    def test_online_signals_during_download_clean_work(self):
        for sig, status in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=sig):
                result = self.run_script("--version", "v0.4.7", TEST_SIGNAL_STAGE="release-curl", TEST_SIGNAL=sig)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(self.records("installer"), [])
                self.assert_clean()

    def test_online_signals_during_mktemp_register_ownership(self):
        for sig, status in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=sig):
                if self.log_path.exists():
                    self.log_path.unlink()
                result = self.run_script("--version", "v0.4.7", TEST_SIGNAL_STAGE="mktemp", TEST_SIGNAL=sig)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(self.records("curl"), [])
                self.assertTrue(any("created" in record for record in self.records("mktemp")))
                self.assert_clean()

    def test_portable_signals_during_setup_clean_runtime(self):
        self.local_installer()
        for stage in ("mktemp", "portable-curl", "tar"):
            for sig, status in (("INT", 130), ("TERM", 143)):
                with self.subTest(stage=stage, signal=sig):
                    if self.log_path.exists():
                        self.log_path.unlink()
                    result = self.run_script(script=self.portable_script(), TEST_SIGNAL_STAGE=stage, TEST_SIGNAL=sig)
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertEqual(self.records("installer"), [])
                    self.assertTrue(any("created" in record for record in self.records("mktemp")))
                    self.assert_clean()

    def test_portable_online_signals_during_second_allocation_clean_both(self):
        for sig, status in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=sig):
                if self.log_path.exists():
                    self.log_path.unlink()
                result = self.run_script("--version", "v0.4.7", script=self.portable_script(),
                    TEST_SIGNAL_STAGE="mktemp", TEST_SIGNAL=sig, TEST_SIGNAL_ALLOCATION="2")
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(len([r for r in self.records("mktemp") if "created" in r]), 2)
                self.assertEqual(self.records("installer"), [])
                self.assert_clean()

    def test_portable_local_signals_during_installer_clean_runtime(self):
        self.local_installer()
        for sig, status in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=sig):
                result = self.run_script(script=self.portable_script(), TEST_SIGNAL_STAGE="installer", TEST_SIGNAL=sig)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assert_clean()

    def test_online_mktemp_path_followed_by_failure_is_cleaned(self):
        result = self.run_script("--version", "v0.4.7", TEST_MKTEMP_PRINT_FAIL="1")
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertEqual(self.records("curl"), [])
        self.assertTrue(any("created" in record for record in self.records("mktemp")))
        self.assert_clean()

    def test_portable_mktemp_path_followed_by_failure_is_cleaned(self):
        result = self.run_script(script=self.portable_script(), TEST_MKTEMP_PRINT_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.records("curl"), [])
        self.assertTrue(any("created" in record for record in self.records("mktemp")))
        self.assert_clean()

    def test_online_mktemp_success_without_path_fails_closed(self):
        result = self.run_script("--version", "v0.4.7", TEST_MKTEMP_EMPTY_SUCCESS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.records("curl"), [])
        self.assert_clean()

    def test_portable_mktemp_success_without_path_fails_closed(self):
        result = self.run_script(script=self.portable_script(), TEST_MKTEMP_EMPTY_SUCCESS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.records("curl"), [])
        self.assert_clean()

    def test_online_group_signals_during_mktemp_clean_printed_path(self):
        for sig, status in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=sig):
                if self.log_path.exists():
                    self.log_path.unlink()
                result = self.run_script("--version", "v0.4.7", TEST_SIGNAL_STAGE="mktemp",
                    TEST_SIGNAL=sig, TEST_SIGNAL_GROUP="1")
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(self.records("curl"), [])
                self.assertTrue(any("created" in record for record in self.records("mktemp")))
                self.assert_clean()

    def test_portable_group_signals_during_mktemp_clean_printed_path(self):
        for sig, status in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=sig):
                if self.log_path.exists():
                    self.log_path.unlink()
                result = self.run_script(script=self.portable_script(), TEST_SIGNAL_STAGE="mktemp",
                    TEST_SIGNAL=sig, TEST_SIGNAL_GROUP="1")
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(self.records("curl"), [])
                self.assertTrue(any("created" in record for record in self.records("mktemp")))
                self.assert_clean()


if __name__ == "__main__":
    unittest.main()
