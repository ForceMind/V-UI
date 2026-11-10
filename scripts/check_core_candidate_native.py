"""Execute same-run component candidates in an isolated native Linux CI job."""
import argparse
from contextlib import ExitStack
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import zipfile

from app import release_tools
from app.core_updates import artifacts
from scripts import build_core_component as producer


CORES = ('sing-box', 'xray')


class Target(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        if self.path != self.server.expected_path:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(self.server.payload)))
        self.end_headers()
        self.wfile.write(self.server.payload)

    def log_message(self, *_):
        pass


def native_binary(root, core, source, target, context, destination):
    expected_raw = (root / 'inputs/expected.json').read_bytes()
    expected = producer._metadata(expected_raw)
    receipt = artifacts._json((root / 'production-receipt.json').read_bytes())
    assert expected['core'] == core and expected['builder_source'] == source
    assert expected['compatibility']['app_sources'] == [source]
    assert receipt['kind'] == 'core-candidate-production-v1' and receipt['schema'] == 1
    assert receipt['context'] == context and receipt['builder_source'] == source and receipt['core'] == core
    assert receipt['qualified'] is False and receipt['published'] is False
    assert receipt['expected_sha256'] == hashlib.sha256(expected_raw).hexdigest()
    name = artifacts._pack_name(core, expected['version'], target)
    pack = root / 'candidate' / name
    with artifacts._opened(pack) as (fd, size, stable):
        row, (digest, binary_size) = producer._candidate(fd, size, expected, target)
        aggregate = receipt['aggregate']
        release = (root / 'candidate/CORE-RELEASE.json').read_bytes()
        checksums = (root / 'candidate/SHA256SUMS').read_bytes()
        assert hashlib.sha256(release).hexdigest() == aggregate['manifest_sha256']
        assert hashlib.sha256(checksums).hexdigest() == aggregate['checksums_sha256']
        assert row in artifacts._json(release)['packs'] and row in aggregate['packs']
        assert f"{row['sha256']}  {name}" in checksums.decode('ascii').splitlines()
        assert any(all(record[key] == value for key, value in row.items())
                   and (record['binary_sha256'], record['binary_size']) == (digest, binary_size)
                   for record in receipt['targets'])
        binary = destination / core
        with zipfile.ZipFile(artifacts._Reader(fd, size)) as archive, archive.open('bin/' + core) as content:
            with binary.open('xb') as output:
                shutil.copyfileobj(content, output)
        assert binary.stat().st_size == binary_size
        with binary.open('rb') as content:
            assert hashlib.file_digest(content, 'sha256').hexdigest() == digest
        assert artifacts._digest_fd(fd, size) == row['sha256']
        stable()
    binary.chmod(stat.S_IRUSR | stat.S_IXUSR)
    return binary, expected['version'], digest, row['sha256']


def owns_listener(pid, port):
    address = f'0100007F:{port:04X}'
    listeners = {fields[9] for line in Path('/proc/net/tcp').read_text().splitlines()[1:]
                 if (fields := line.split())[1] == address and fields[3] == '0A'}
    for descriptor in (Path('/proc') / str(pid) / 'fd').iterdir():
        try:
            target = os.readlink(descriptor)
        except FileNotFoundError:
            continue
        if target.startswith('socket:[') and target[8:-1] in listeners:
            return True
    return False


def verify_runtime(core, binary, version, directory):
    env = dict(PATH='/usr/bin:/bin', HOME=str(directory), TMPDIR=str(directory))
    with ExitStack() as stack:
        target = stack.enter_context(ThreadingHTTPServer(('127.0.0.1', 0), Target))
        target.requests = []
        target.expected_path = '/' + secrets.token_hex(16)
        target.payload = secrets.token_bytes(32)
        thread = threading.Thread(target=target.serve_forever, daemon=True)
        thread.start()
        stack.callback(thread.join, 3)
        stack.callback(target.shutdown)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        if core == 'sing-box':
            config = dict(inbounds=[dict(type='http', listen='127.0.0.1', listen_port=port)],
                          outbounds=[dict(type='direct', tag='direct')])
        else:
            config = dict(inbounds=[dict(protocol='http', listen='127.0.0.1', port=port)],
                          outbounds=[dict(protocol='freedom', tag='direct')])
        path = directory / (core + '.json')
        path.write_text(json.dumps(config), encoding='ascii')
        result = subprocess.run([str(binary), 'version'], check=True, capture_output=True, text=True,
                                timeout=10, cwd=directory, env=env)
        assert re.search(r'(?m)^(?:sing-box version|Xray) ' + re.escape(version) + r'(?:\s|$)', result.stdout)
        run = ['run', '-c', str(path)]
        check = ['check', '-c', str(path)] if core == 'sing-box' else ['run', '-test', '-c', str(path)]
        subprocess.run([str(binary), *check], check=True, capture_output=True, timeout=15,
                       cwd=directory, env=env)
        with subprocess.Popen([str(binary), *run], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, cwd=directory, env=env) as child:
            try:
                deadline = time.monotonic() + 10
                while True:
                    assert child.poll() is None, 'Candidate stopped before TCP verification'
                    if not owns_listener(child.pid, port):
                        assert time.monotonic() < deadline, 'Candidate did not own the TCP listener'
                        time.sleep(.05)
                        continue
                    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                    try:
                        process_binary = Path('/proc') / str(child.pid) / 'exe'
                        assert os.path.samestat(process_binary.stat(), binary.stat())
                        connection.request('GET', f'http://127.0.0.1:{target.server_port}{target.expected_path}')
                        response = connection.getresponse()
                        assert response.status == 200
                        assert response.getheader('Content-Length') == str(len(target.payload))
                        assert response.length == len(target.payload)
                        assert response.read() == target.payload
                        assert target.requests == [target.expected_path]
                        assert child.poll() is None and owns_listener(child.pid, port)
                        break
                    except ConnectionRefusedError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.05)
                    finally:
                        connection.close()
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)


def main():
    if not __debug__:
        raise SystemExit('Native candidate checks require assertions')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--target', choices=artifacts.TARGETS, required=True)
    args = parser.parse_args()
    assert os.geteuid() != 0 and os.environ['GITHUB_ACTIONS'] == 'true'
    assert os.environ['RUNNER_ENVIRONMENT'] == 'github-hosted'
    assert release_tools.target_key() == args.target
    source = os.environ['EXPECTED_SOURCE']
    assert re.fullmatch('[0-9a-f]{40}', source)
    context = dict(repository=os.environ['GITHUB_REPOSITORY'], repository_id=int(os.environ['GITHUB_REPOSITORY_ID']),
                   run_id=int(os.environ['GITHUB_RUN_ID']), run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']))
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='vui-native-core-') as temporary:
        directory = Path(temporary)
        results = {}
        for core in CORES:
            name = f"core-candidate-{core}-{source}-{context['run_id']}-{context['run_attempt']}"
            binary, version, digest, pack = native_binary(args.inputs/name, core, source, args.target, context, directory)
            try:
                verify_runtime(core, binary, version, directory)
            except Exception as error:
                error.add_note('Native TCP candidate: ' + core + ' / ' + args.target)
                raise
            results[core] = dict(version=version, binary_sha256=digest, pack_sha256=pack)
    print(json.dumps(dict(schema=1, kind='core-candidate-native-tcp-v1', source=source, context=context,
                          target=args.target, cores=results), sort_keys=True))


if __name__ == '__main__':
    main()
