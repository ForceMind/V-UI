"""Passive Linux-only D diagnostics. No new association, probe, retry or timer.

The caller owns all association creation/close and calls us on that same thread.
These observations cannot change the original lifecycle/capacity verdict.
"""
import copy
import ctypes
import errno
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time

import packet_boundary as p

KIND = 'vui-stock-mihomo-socks-association-observation'
FILE = 'socks-association-observation.json'
ROLE = 'device-mihomo'


def observable_socks_class(helper):
    class ObservedSocksDatagrams(helper.SocksDatagrams):
        # Frozen helper init, with only `self.control = control` added. A source
        # AST control below tests parity, including failed construction cleanup.
        def __init__(self, port, *, auth=None):
            self.stack = helper.ExitStack()
            try:
                control = self.stack.enter_context(helper.socket.create_connection((helper.CLIENT, port), timeout=3))
                self.control = control
                control.settimeout(3)
                method = b"\x02" if auth else b"\x00"
                control.sendall(b"\x05\x01" + method)
                if helper._recv_exact(control, 2) != b"\x05" + method:
                    raise AssertionError("SOCKS authentication negotiation failed")
                if auth:
                    user, password = (part.encode("ascii") for part in auth)
                    control.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
                    if helper._recv_exact(control, 2) != b"\x01\x00":
                        raise AssertionError("SOCKS username/password rejected")
                control.sendall(b"\x05\x03\x00\x01" + b"\x00" * 6)
                reply = helper._recv_exact(control, 4)
                if reply[:3] != b"\x05\x00\x00":
                    raise AssertionError("SOCKS UDP association failed")
                host, port = helper._socks_address_from_stream(control, reply[3])
                if host in ("0.0.0.0", "::"):
                    host = helper.CLIENT
                if host != helper.CLIENT:
                    raise AssertionError("SOCKS relay escaped the loopback fixture")
                self.relay = host, port
                self.stream = self.stack.enter_context(helper.socket.socket(helper.socket.AF_INET, helper.socket.SOCK_DGRAM))
                self.stream.bind((helper.CLIENT, 0))
                self.sent = []
            except BaseException:
                self.stack.close()
                raise
    return ObservedSocksDatagrams


def peek(control):
    """Bypass Python's timeout preselect without altering its timeout/O_NONBLOCK."""
    p.need(sys.platform.startswith('linux') and hasattr(socket, 'MSG_DONTWAIT'),
           'SOCKS passive PEEK requires Linux MSG_DONTWAIT; no blocking fallback')
    fd = control.fileno()
    if fd < 0:
        return {'fd': fd, 'state': 'local_closed', 'count': None, 'errno': None}
    libc = ctypes.CDLL(None, use_errno=True)
    recv = libc.recv
    recv.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
    recv.restype = ctypes.c_ssize_t
    byte = ctypes.create_string_buffer(1)
    count = recv(fd, byte, 1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
    error = ctypes.get_errno() if count < 0 else None
    state = ('would_block' if error in (errno.EAGAIN, errno.EWOULDBLOCK) else 'error') if count < 0 else ('eof' if count == 0 else 'data')
    return {'fd': fd, 'state': state, 'count': count, 'errno': error}


def local_socket(stream):
    fd = stream.fileno()
    if fd < 0:
        return {'fd': fd, 'inode': None, 'local': None, 'peer': None}
    match = re.fullmatch(r'socket:\[([0-9]+)\]', os.readlink('/proc/self/fd/' + str(fd)))
    p.need(match is not None, 'Application descriptor is not a socket')
    try:
        peer = list(stream.getpeername())
    except OSError as exc:
        if exc.errno != errno.ENOTCONN:
            raise
        peer = None
    return {'fd': fd, 'inode': int(match[1]), 'local': list(stream.getsockname()), 'peer': peer}


def exe_stat(pid):
    st = os.stat('/proc/' + str(pid) + '/exe')
    return {'device': st.st_dev, 'inode': st.st_ino, 'size': st.st_size,
            'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns}


def fd_snapshot(pid):
    rows, races = [], []
    for entry in sorted((Path('/proc') / str(pid) / 'fd').iterdir(), key=lambda x: int(x.name)):
        try:
            target = os.readlink(entry)
        except FileNotFoundError:
            races.append({'fd': int(entry.name), 'kind': 'fd_removed_during_readlink'})
            continue
        match = re.fullmatch(r'socket:\[([0-9]+)\]', target)
        if match:
            rows.append({'fd': int(entry.name), 'inode': int(match[1])})
    return rows, races


def device_snapshot(core, psutil, observer, launch):
    """Explicit device role. Never pass a fake hop role to the frozen observer."""
    row = {'role': ROLE, 'started': time.monotonic()}
    p.need(core.process is not None and core.process.poll() is None, 'Device exited during observation')
    pid = core.process.pid
    process = psutil.Process(pid)
    row.update(pid=pid, birth_time=process.create_time(), executable=exe_stat(pid))
    p.need(pid == launch['pid'] and row['birth_time'] == launch['birth_time'], 'Device PID/birth changed')
    before, races = fd_snapshot(pid)
    row['fd_before'] = before
    connections = process.net_connections(kind='inet')
    table = observer.read_proc_sockets(pid, {v['inode'] for v in before})
    after, more_races = fd_snapshot(pid)
    row['fd_after'] = after
    races.extend(more_races)
    old, new = ({x['fd']: x['inode'] for x in data} for data in (before, after))
    for fd in old.keys() | new.keys():
        if old.get(fd) != new.get(fd):
            races.append({'fd': fd, 'kind': 'fd_changed_during_snapshot',
                          'before_inode': old.get(fd), 'after_inode': new.get(fd)})
    sockets = []
    for connection in connections:
        fd = connection.fd
        inode = old.get(fd)
        if fd < 0 or inode is None or new.get(fd) != inode or inode not in table:
            races.append({'fd': fd, 'kind': 'connection_unresolved_during_snapshot'})
            continue
        item = table[inode]
        local, remote = observer.psutil_address(connection.laddr), observer.psutil_address(connection.raddr)
        p.need(connection.type in (socket.SOCK_DGRAM, socket.SOCK_STREAM), 'Unexpected device socket type')
        protocol = 'udp' if connection.type == socket.SOCK_DGRAM else 'tcp'
        p.need(item['protocol'] == protocol and item['local'] == local and
               item['family'] == ('ipv4' if connection.family == socket.AF_INET else 'ipv6'),
               'Device psutil and proc socket identities disagree')
        p.need(item['remote'] == (remote or {'host': '0.0.0.0' if item['family'] == 'ipv4' else '::', 'port': 0}),
               'Device psutil and proc remote identities disagree')
        sockets.append({**item, 'fd': fd, 'psutil_state': connection.status})
    stable = {inode for fd, inode in old.items() if new.get(fd) == inode and inode in table}
    if {v['inode'] for v in sockets} != stable:
        races.append({'kind': 'stable_proc_socket_omitted_by_psutil'})
    row.update(sockets=sockets, races=races, birth_time_after=process.create_time(),
               executable_after=exe_stat(pid), completed=time.monotonic())
    p.need(core.process.poll() is None and row['birth_time_after'] == row['birth_time'] and
           row['executable_after'] == row['executable'], 'Device changed during sample')
    return row


class Observation:
    def __init__(self, path, case, core, psutil, observer, source, role):
        self.path, self.core, self.psutil, self.observer = path, core, psutil, observer
        self.owner = threading.get_ident()
        self.associations = []
        self.data = {'schema_version': 1, 'kind': KIND, 'case': case['id'], 'mode': case['mode'],
                     'source_identity': copy.deepcopy(source), 'device_role': copy.deepcopy(role),
                     'status': 'collecting', 'associations': [], 'samples': [], 'errors': [],
                     'product_qualified': False, 'capacity_evaluated': False}
        self.flush()

    def flush(self):
        p.save(self.path, self.data)

    def add(self, association, label):
        self.associations.append((label, association))
        try:
            self.data['associations'].append({'label': label, 'control': local_socket(association.control),
                'application': local_socket(association.stream), 'relay': list(association.relay),
                'created': time.monotonic()})
        except Exception as exc:
            self.data['errors'].append({'phase': 'association_identity', 'type': type(exc).__name__, 'message': str(exc)})
        self.flush()

    def capture(self, phase, hop_sample=None, *, hash_executable=False):
        row = {'phase': phase, 'started': time.monotonic(), 'associations': []}
        if hop_sample is not None:
            row['hop_sample'] = {'started': hop_sample['monotonic'], 'completed': hop_sample['sample_completed_monotonic']}
        try:
            p.need(threading.get_ident() == self.owner, 'SOCKS observation escaped owner thread')
            role = self.data['device_role']
            p.need(role['role'] == ROLE and len(role['launches']) == 1, 'Wrong passive device role')
            row['device'] = device_snapshot(self.core, self.psutil, self.observer, role['launches'][0])
            if hash_executable:
                row['executable_sha256'] = p.sha('/proc/' + str(row['device']['pid']) + '/exe')
                p.need(row['executable_sha256'] == role['sha256'], 'Actual device executable differs from role')
            for label, association in self.associations:
                before = local_socket(association.control)
                state = peek(association.control)
                after = local_socket(association.control)
                p.need(before == after and state['fd'] == before['fd'], 'Control descriptor changed around PEEK')
                row['associations'].append({'label': label, 'control_before': before, 'peek': state,
                    'control_after': after, 'application': local_socket(association.stream), 'relay': list(association.relay)})
        except Exception as exc:
            row['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            self.data['errors'].append({'phase': phase, **row['error']})
        row['completed'] = time.monotonic()
        self.data['samples'].append(row)
        self.flush()

    def finish(self):
        self.data['cleanup'] = [{'label': label, 'control_fd': association.control.fileno(),
                                'application_fd': association.stream.fileno()} for label, association in self.associations]
        races = any(s.get('device', {}).get('races') for s in self.data['samples'])
        self.data['status'] = ('inconclusive' if self.data['errors'] else
                               'complete_with_inconclusive_samples' if races else 'complete')
        self.flush()
