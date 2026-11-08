"""Narrow, offline installer rollback; execute only as the service account.

The caller supplies the verified controller directory on sys.path, never the
installed release. No application initialization or third-party imports occur.
"""

SELF_CONTAINED_CODE = r'''
from contextlib import contextmanager
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

from app.release_tools import (ReleaseError, _maintenance_directory,
                               atomic_json, release_id, stopped)

MAX_JSON = 65536
MAX_CERTIFICATES = 512
BINDING_COLUMNS = ('target', 'certificate_id', 'applied_certificate_id',
                   'applied_revision', 'error')
BINDING_COLUMNS_V2 = BINDING_COLUMNS + ('server_name',)
CERTIFICATE_COLUMNS = ('id', 'domain', 'email', 'environment', 'auto_renew',
    'revision', 'not_before', 'not_after', 'renew_at', 'retry_at',
    'last_attempt', 'failures', 'error', 'created_at')
MODERN_CERTIFICATE_COLUMNS = CERTIFICATE_COLUMNS[:4] + ('method', 'wildcard', 'credential_id') + CERTIFICATE_COLUMNS[4:]
MODERN_BINDING_LAYOUTS = (BINDING_COLUMNS_V2, BINDING_COLUMNS[:4] + ('server_name', 'error'))


def require(condition, message='Invalid installer certificate state'):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def bounded_json(raw):
    require(type(raw) is str and len(raw.encode('utf-8')) <= MAX_JSON)
    return json.loads(raw, object_pairs_hook=unique_object,
        parse_constant=lambda _: require(False))


def pointer_value(value):
    if value is None:
        return None
    require(type(value) is dict and 'release_id' in value and 'previous_id' in value)
    release_id(value['release_id'])
    if value['previous_id'] is not None:
        release_id(value['previous_id'])
    require(len(json.dumps(value, allow_nan=False).encode('utf-8')) <= 16384)
    return value


def safe_file(path, *, limit=None):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
            and info.st_nlink == 1 and not info.st_mode & 0o022,
            'Unsafe installer state file')
    if limit is not None:
        require(info.st_size <= limit)
    return info.st_dev, info.st_ino


def current_pointer(root):
    path = root / 'CURRENT.json'
    if not os.path.lexists(path):
        return None
    identity = safe_file(path, limit=16384)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require((info.st_dev, info.st_ino) == identity)
        raw = stream.read(16385)
    require(len(raw) <= 16384)
    value = bounded_json(raw.decode('utf-8'))
    require(type(value) is dict, 'Invalid current release pointer')
    return pointer_value(value)


def short_text(value, maximum, *, nullable=False):
    require((nullable and value is None) or (type(value) is str
        and 0 < len(value) <= maximum and all(ord(c) >= 32 and ord(c) != 127 for c in value)))


def state_value(value):
    require(type(value) is dict and set(value) == {
        'version', 'db_exists', 'ready', 'panel_binding', 'certificates', 'current_pointer'})
    require(type(value['version']) is int and value['version'] in (1, 2))
    require(type(value['db_exists']) is bool and type(value['ready']) is bool)
    require(not value['ready'] or value['db_exists'])
    pointer_value(value['current_pointer'])
    certificates = value['certificates']
    require(type(certificates) is list and len(certificates) <= MAX_CERTIFICATES)
    seen = set()
    for row in certificates:
        require(type(row) is list and len(row) == 2)
        short_text(row[0], 32)
        short_text(row[1], 254)
        require(row[0] not in seen)
        seen.add(row[0])
    binding = value['panel_binding']
    if binding is not None:
        require(type(binding) is list and len(binding) == (5 if value['version'] == 1 else 6) and binding[0] == 'panel')
        short_text(binding[1], 32)
        short_text(binding[2], 32, nullable=True)
        short_text(binding[3], 64, nullable=True)
        short_text(binding[4], 64, nullable=True)
        if value['version'] == 2 and binding[5] is not None:
            short_text(binding[5], 253)
            require(domain_name(binding[5]) == binding[5])
        require(binding[1] in seen and (binding[2] is None or binding[2] in seen))
    require(value['ready'] or (binding is None and not certificates))
    return value


def domain_name(value):
    require(type(value) is str and bool(value) and value == value.strip(), 'Invalid domain')
    name = value.rstrip('.').encode('idna').decode('ascii').lower()
    require(len(name) <= 253 and '.' in name and not name.split('.')[-1].isdigit()
            and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p)
                    for p in name.split('.')), 'Invalid domain')
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name
    raise ValueError('Invalid domain')


def email_address(value):
    require(type(value) is str and len(value) <= 254
            and re.fullmatch(r'[A-Za-z0-9._%+\-]+@[^@]+', value), 'Invalid email')
    local, host = value.rsplit('@', 1)
    return local + '@' + domain_name(host)


def schema_ready(db):
    objects = dict(db.execute("SELECT name,type FROM sqlite_schema WHERE name IN (?,?,?,?)",
        ('managed_certificates', 'certificate_bindings', 'certificate_jobs', 'certificate_identity_schema')))
    tables = ('managed_certificates', 'certificate_bindings')
    if not any(name in objects for name in tables):
        require('certificate_identity_schema' not in objects, 'Incomplete certificate database schema')
        return False
    require(all(objects.get(name) == 'table' for name in tables), 'Incomplete certificate database schema')
    layouts = {}
    for table in tables:
        columns = list(db.execute('PRAGMA table_xinfo(' + table + ')'))
        names = tuple(row[1] for row in columns)
        permitted = ((CERTIFICATE_COLUMNS, MODERN_CERTIFICATE_COLUMNS) if table == 'managed_certificates'
                     else (BINDING_COLUMNS,) + MODERN_BINDING_LAYOUTS)
        require(names in permitted and all(row[6] == 0 for row in columns)
                and columns[0][5] == 1 and all(row[5] == 0 for row in columns[1:]),
                'Unsupported certificate database schema')
        integers = {'not_before', 'not_after', 'renew_at', 'retry_at', 'last_attempt', 'failures', 'created_at'}
        require(all(row[2].upper().startswith(('INTEGER',) if row[1] in integers else
                    ('BOOLEAN',) if row[1] in ('auto_renew', 'wildcard') else ('VARCHAR', 'TEXT'))
                    for row in columns), 'Unsupported certificate database schema')
        require(not list(db.execute('PRAGMA foreign_key_list(' + table + ')')),
                'Unsupported certificate database foreign key')
        require(db.execute("SELECT count(*) FROM sqlite_schema WHERE type='trigger' AND tbl_name=?",
                           (table,)).fetchone()[0] == 0, 'Unsupported certificate database trigger')
        layouts[table] = names
    modern = layouts['managed_certificates'] == MODERN_CERTIFICATE_COLUMNS
    require((layouts['certificate_bindings'] in MODERN_BINDING_LAYOUTS) == modern,
            'Incomplete certificate database schema')
    marked = 'certificate_identity_schema' in objects
    require(marked == modern, 'Certificate identity schema marker mismatch')
    if modern:
        require(objects['certificate_identity_schema'] == 'table' and objects.get('certificate_jobs') == 'table')
        marker = list(db.execute('PRAGMA table_xinfo(certificate_identity_schema)'))
        require(len(marker) == 1 and marker[0][1] == 'version' and marker[0][2].upper() == 'INTEGER'
                and marker[0][3] == 1 and marker[0][4] is None and marker[0][5] == 0 and marker[0][6] == 0
                and list(db.execute('SELECT typeof(version),version FROM certificate_identity_schema')) == [('integer', 1)],
                'Certificate identity schema marker mismatch')
        marker_sql = db.execute("SELECT sql FROM sqlite_schema WHERE name='certificate_identity_schema'").fetchone()[0]
        require(type(marker_sql) is str and not re.search(r'\bCHECK\s*\(', marker_sql, re.IGNORECASE),
                'Certificate identity schema marker mismatch')
        require(not list(db.execute('PRAGMA foreign_key_list(certificate_identity_schema)'))
                and not list(db.execute('PRAGMA index_list(certificate_identity_schema)'))
                and db.execute("SELECT count(*) FROM sqlite_schema WHERE type='trigger' AND tbl_name='certificate_identity_schema'").fetchone()[0] == 0)
        rows = db.execute('SELECT id,domain,environment,method,wildcard,credential_id FROM managed_certificates').fetchmany(MAX_CERTIFICATES + 1)
        require(len(rows) <= MAX_CERTIFICATES)
        identities, ids = set(), set()
        for identifier, domain, environment, method, wildcard, credential in rows:
            require(type(identifier) is str and re.fullmatch('[a-f0-9]{32}', identifier)
                    and identifier not in ids and domain_name(domain) == domain and environment in ('production', 'staging')
                    and method in ('http-01', 'dns-01') and type(wildcard) is int and wildcard in (0, 1)
                    and (method == 'http-01' and wildcard == 0 and credential is None
                         or method == 'dns-01' and type(credential) is str and re.fullmatch('[a-f0-9]{32}', credential)))
            identity = domain, environment, method, wildcard
            require(identity not in identities); identities.add(identity); ids.add(identifier)
        for target, desired, applied, sni in db.execute('SELECT target,certificate_id,applied_certificate_id,server_name FROM certificate_bindings'):
            require(desired in ids and (applied is None or applied in ids))
            require(sni is None or domain_name(sni) == sni)
    # A rollback DELETE/UPSERT must not cascade into unrelated business rows.
    # These are metadata reads only; never initialize or alter another table.
    all_tables = db.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchmany(513)
    require(len(all_tables) <= 512)
    for (table,) in all_tables:
        require(type(table) is str and len(table) <= 1024)
        quoted = '"' + table.replace('"', '""') + '"'
        require(not any(row[2] in ('managed_certificates', 'certificate_bindings', 'certificate_identity_schema')
                        for row in db.execute('PRAGMA foreign_key_list(' + quoted + ')')),
                'Unsupported certificate database foreign key')
    return True


def has_server_name(db):
    return any(row[1] == 'server_name' for row in db.execute('PRAGMA table_xinfo(certificate_bindings)'))


@contextmanager
def database(root, *, write):
    path = root / 'data' / 'v-ui.db'
    if not os.path.lexists(path):
        yield None
        return
    with _maintenance_directory(root / 'data'):
        identity = safe_file(path)
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = Path(str(path) + suffix)
            if os.path.lexists(sidecar):
                safe_file(sidecar)
        db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=0)
        try:
            db.execute('PRAGMA trusted_schema=OFF')
            if not write:
                db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            require(safe_file(path) == identity, 'Database changed during installer operation')
            if write:
                db.commit()
        finally:
            db.close()


def run(command, root, *arguments):
    root = Path(root).absolute()
    require('..' not in root.parts)
    require(command in ('snapshot', 'contact', 'restore'), 'Unknown installer state command')
    require(len(arguments) == {'snapshot': 0, 'contact': 2, 'restore': 1}[command])
    state = state_value(bounded_json(arguments[0])) if command == 'restore' else None
    if command == 'contact':
        domain, email = domain_name(arguments[0]), email_address(arguments[1])
    with _maintenance_directory(root), stopped(root):
        pointer = current_pointer(root)  # Also reject unsafe current pointers before a write.
        result = {'version': 2, 'db_exists': False, 'ready': False,
                  'panel_binding': None, 'certificates': [], 'current_pointer': pointer}
        with database(root, write=command != 'snapshot') as db:
            result['db_exists'] = db is not None
            result['ready'] = db is not None and schema_ready(db)
            if command == 'snapshot' and result['ready']:
                rows = db.execute('SELECT id,email FROM managed_certificates ORDER BY id').fetchmany(MAX_CERTIFICATES + 1)
                require(len(rows) <= MAX_CERTIFICATES, 'Too many certificates for installer snapshot')
                result['certificates'] = [list(row) for row in rows]
                binding = db.execute('SELECT ' + ','.join(BINDING_COLUMNS)
                    + (',server_name' if has_server_name(db) else ',NULL AS server_name')
                    + " FROM certificate_bindings WHERE target='panel'").fetchone()
                result['panel_binding'] = list(binding) if binding is not None else None
            elif command == 'contact':
                ready = result['ready']
                result = {'updated': 0}
                if ready:
                    result['updated'] = db.execute('UPDATE managed_certificates SET email=? '
                        "WHERE domain=? AND environment='production'" +
                        (" AND method='http-01' AND wildcard=0" if has_server_name(db) else ''), (email, domain)).rowcount
            elif command == 'restore' and state['ready']:
                require(result['ready'], 'Certificate database unavailable for rollback')
                for identifier, email in state['certificates']:
                    require(db.execute('UPDATE managed_certificates SET email=? WHERE id=?',
                        (email, identifier)).rowcount == 1, 'Original certificate missing during rollback')
                binding = state['panel_binding']
                if binding is None:
                    db.execute("DELETE FROM certificate_bindings WHERE target='panel'")
                else:
                    columns = BINDING_COLUMNS_V2 if has_server_name(db) else BINDING_COLUMNS
                    restored = list(binding)
                    if state['version'] == 1 and len(columns) == 6:
                        # v1 only described legacy HTTP identities; never erase
                        # the literal SNI of a DNS certificate by guessing NULL.
                        for identifier in (binding[1], binding[2]):
                            if identifier is not None:
                                row = db.execute('SELECT method,wildcard FROM managed_certificates WHERE id=?', (identifier,)).fetchone()
                                require(row == ('http-01', 0), 'Legacy rollback cannot restore a DNS binding')
                        restored.append(None)
                    if state['version'] == 2 and len(columns) == 5:
                        require(restored[5] is None, 'Legacy schema cannot preserve certificate SNI')
                        restored = restored[:5]
                    db.execute('INSERT INTO certificate_bindings (' + ','.join(columns)
                        + ') VALUES (' + ','.join('?' for _ in columns) + ') ON CONFLICT(target) DO UPDATE SET '
                        + ','.join(name + '=excluded.' + name for name in columns[1:]), restored)
            elif command == 'restore' and result['ready']:
                # A first/unfinished install had no binding to preserve. Its
                # failed bootstrap must not bind the restored configuration to
                # a newly issued domain; retain all certificate/job records.
                db.execute("DELETE FROM certificate_bindings WHERE target='panel'")
        if command == 'restore':
            if state['current_pointer'] is not None:
                atomic_json(root / 'CURRENT.json', state['current_pointer'])
            return {'restored': True}
        if command == 'snapshot':
            state_value(result)
            require(len(json.dumps(result).encode('utf-8')) <= MAX_JSON)
        return result


def main():
    if os.geteuid() == 0:
        raise SystemExit('Installer certificate state must run as the service user')
    try:
        require(len(sys.argv) >= 3)
        result = run(sys.argv[1], sys.argv[2], *sys.argv[3:])
    except (OSError, ValueError, RecursionError, sqlite3.Error, ReleaseError):
        raise SystemExit('Installer certificate state operation failed') from None
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
'''

exec(SELF_CONTAINED_CODE)
