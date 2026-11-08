"""Atomic certificate identity migration inside init_db's BEGIN IMMEDIATE.

The marker is a recovery fence, not a request to repair an incomplete restore.
Only the known legacy table is rebuilt; unknown schema is refused before DDL.
"""
import re

from sqlalchemy import CheckConstraint, MetaData, inspect, text
from sqlalchemy.schema import CreateTable

DNS_IDENTITY_SCHEMA_MAX = 1

MARKER = 'certificate_identity_schema'
CERTIFICATES = 'managed_certificates'
BINDINGS = 'certificate_bindings'
JOBS = 'certificate_jobs'
STAGING = 'managed_certificates_identity_upgrade'
OLD_CERTIFICATE_COLUMNS = frozenset({'id', 'domain', 'email', 'environment', 'auto_renew', 'revision',
    'not_before', 'not_after', 'renew_at', 'retry_at', 'last_attempt', 'failures', 'error', 'created_at'})
NEW_CERTIFICATE_COLUMNS = OLD_CERTIFICATE_COLUMNS | {'method', 'wildcard', 'credential_id'}
OLD_BINDING_COLUMNS = frozenset({'target', 'certificate_id', 'applied_certificate_id', 'applied_revision', 'error'})
NEW_BINDING_COLUMNS = OLD_BINDING_COLUMNS | {'server_name'}
CHECK_NAMES = frozenset({'ck_certificate_method', 'ck_certificate_wildcard',
                         'ck_certificate_credential', 'ck_certificate_method_fields'})


class CertificateIdentitySchemaError(RuntimeError):
    pass


def _need(condition):
    if not condition:
        raise CertificateIdentitySchemaError(
            'Certificate identity schema is incomplete, unsupported or inconsistent; restore a verified full database')


def _columns(conn, table):
    # Table names are constants, never external identifiers.
    return {row['name']: dict(row) for row in conn.execute(text('PRAGMA table_xinfo(' + table + ')')).mappings()}


def _extras(conn, table, *, modern=False):
    _need(not conn.execute(text("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=:table LIMIT 1"),
                           {'table': table}).first())
    checks = inspect(conn).get_check_constraints(table)
    _need({item.get('name') for item in checks} == (CHECK_NAMES if table == CERTIFICATES and modern else set()))
    if table == CERTIFICATES and modern:
        from app.certificates.models import Certificate
        def expression(value):
            _need(type(value) is str)
            # Ignore formatting whitespace, but retain bytes inside SQL string
            # literals: adding a space to the GLOB class changes its meaning.
            return ''.join(re.findall(r"'(?:[^']|'')*'|[^\s]", value))
        expected = {item.name: expression(str(item.sqltext)) for item in Certificate.__table__.constraints
                    if isinstance(item, CheckConstraint)}
        _need(len(checks) == len(expected)
              and {item['name']: expression(item.get('sqltext')) for item in checks} == expected)
    seen = set()
    certificate_indexes = {('pk', 1, 0, ('id',)), ('u', 1, 0,
        ('domain', 'environment', 'method', 'wildcard') if modern else ('domain', 'environment'))}
    for item in conn.execute(text('PRAGMA index_list(' + table + ')')).mappings():
        name = item['name']
        # PRAGMA's SQL identifier cannot be parameterized; reject any unknown
        # spelling rather than interpolating a user-defined index expression.
        _need(type(name) is str and re.fullmatch(r'[A-Za-z0-9_]+', name))
        columns = tuple(row['name'] for row in conn.execute(text('PRAGMA index_info(' + name + ')')).mappings())
        identity = (item['origin'], item['unique'], item['partial'], columns)
        if table == CERTIFICATES:
            allowed = certificate_indexes
        elif table == BINDINGS:
            allowed = {('pk', 1, 0, ('target',))}
            if name == 'ix_certificate_bindings_certificate_id':
                allowed.add(('c', 0, 0, ('certificate_id',)))
        else:
            allowed = set()
        _need(identity in allowed and identity not in seen)
        seen.add(identity)
    if table == CERTIFICATES:
        _need(seen == certificate_indexes)
    elif table == BINDINGS:
        _need(('pk', 1, 0, ('target',)) in seen)


def _identity_rows(conn, *, modern):
    from app.certificates.material import CertificateError, domain_name
    ids = set(); identities = set()
    for row in conn.execute(text('SELECT * FROM ' + CERTIFICATES)).mappings():
        try:
            _need(type(row['id']) is str and re.fullmatch(r'[a-f0-9]{32}', row['id'])
                  and row['id'] not in ids and domain_name(row['domain']) == row['domain']
                  and row['environment'] in ('production', 'staging'))
            method = row['method'] if modern else 'http-01'
            wildcard = row['wildcard'] if modern else 0
            credential = row['credential_id'] if modern else None
            _need(method in ('http-01', 'dns-01') and type(wildcard) is int and wildcard in (0, 1)
                  and (credential is None or type(credential) is str and re.fullmatch(r'[a-f0-9]{32}', credential))
                  and (method == 'http-01' and wildcard == 0 and credential is None
                       or method == 'dns-01' and credential is not None))
            identity = (row['domain'], row['environment'], method, wildcard)
            _need(identity not in identities)
            ids.add(row['id']); identities.add(identity)
        except CertificateError:
            _need(False)
    return ids


def _binding_rows(conn, ids, *, modern):
    from app.certificates.material import CertificateError, domain_name
    for row in conn.execute(text('SELECT * FROM ' + BINDINGS)).mappings():
        _need(row['certificate_id'] in ids and (row['applied_certificate_id'] is None
                                               or row['applied_certificate_id'] in ids))
        if modern and row['server_name'] is not None:
            try:
                _need(domain_name(row['server_name']) == row['server_name'])
            except CertificateError:
                _need(False)


def _inspect(conn):
    tables = set(inspect(conn).get_table_names())
    _need(STAGING not in tables)
    marked = MARKER in tables
    if marked:
        marker = _columns(conn, MARKER)
        _need(set(marker) == {'version'} and marker['version']['type'].upper() == 'INTEGER'
              and marker['version']['notnull'] == 1
              and conn.execute(text('SELECT typeof(version), version FROM ' + MARKER)).all() == [('integer', 1)])
        _extras(conn, MARKER)
        _need({CERTIFICATES, BINDINGS, JOBS} <= tables)
    rows_exist = any(conn.execute(text('SELECT 1 FROM ' + name + ' LIMIT 1')).first()
                     for name in (CERTIFICATES, BINDINGS, JOBS) if name in tables)
    if rows_exist:
        _need({CERTIFICATES, BINDINGS, JOBS} <= tables)
    certificate_columns = _columns(conn, CERTIFICATES) if CERTIFICATES in tables else {}
    binding_columns = _columns(conn, BINDINGS) if BINDINGS in tables else {}
    if certificate_columns:
        _need(set(certificate_columns) in (OLD_CERTIFICATE_COLUMNS, NEW_CERTIFICATE_COLUMNS)
              and all(row['hidden'] == 0 for row in certificate_columns.values()))
        _extras(conn, CERTIFICATES, modern=set(certificate_columns) == NEW_CERTIFICATE_COLUMNS)
    if binding_columns:
        _need(set(binding_columns) in (OLD_BINDING_COLUMNS, NEW_BINDING_COLUMNS)
              and all(row['hidden'] == 0 for row in binding_columns.values()))
        _extras(conn, BINDINGS)
    modern = set(certificate_columns) == NEW_CERTIFICATE_COLUMNS
    modern_binding = set(binding_columns) == NEW_BINDING_COLUMNS
    if marked:
        _need(modern and modern_binding)
    elif rows_exist:
        # Never make an unmarked partially restored new database look legacy.
        _need(not modern and not modern_binding)
    if modern:
        _need(certificate_columns['method']['type'].upper() == 'VARCHAR(16)'
              and certificate_columns['wildcard']['type'].upper() == 'BOOLEAN'
              and certificate_columns['credential_id']['type'].upper() == 'VARCHAR(32)'
              and certificate_columns['credential_id']['notnull'] == 0
              and certificate_columns['method']['notnull'] == 1 and certificate_columns['wildcard']['notnull'] == 1
              and certificate_columns['method']['dflt_value'] in ("'http-01'", "('http-01')")
              and certificate_columns['wildcard']['dflt_value'] in ('0', '(0)'))
    if modern_binding:
        _need(binding_columns['server_name']['type'].upper() == 'VARCHAR(253)'
              and binding_columns['server_name']['notnull'] == 0)
    if CERTIFICATES in tables:
        ids = _identity_rows(conn, modern=modern)
        if BINDINGS in tables:
            _binding_rows(conn, ids, modern=modern_binding)
    return marked, modern, modern_binding


def guard_certificate_identity_schema(conn):
    """Run before create_all so a marker cannot hide lost fields/tables or bad rows."""
    return _inspect(conn)


def migrate_certificate_identity(conn):
    """Caller owns the serialized transaction; all table replacement and fencing roll back together."""
    marked, modern, modern_binding = _inspect(conn)
    if marked:
        return
    _need({CERTIFICATES, BINDINGS, JOBS} <= set(inspect(conn).get_table_names()))
    if not modern:
        # No shipped table has foreign keys into this table. Unknown references
        # could cascade during DROP, so refuse them rather than guess preservation.
        inspector = inspect(conn)
        for table in inspector.get_table_names():
            _need(not any(table == CERTIFICATES or item.get('referred_table') == CERTIFICATES
                          for item in inspector.get_foreign_keys(table)))
        from app.certificates.models import Certificate
        replacement = Certificate.__table__.to_metadata(MetaData(), name=STAGING)
        conn.execute(CreateTable(replacement))
        columns = ', '.join(sorted(OLD_CERTIFICATE_COLUMNS))
        before = conn.execute(text('SELECT ' + columns + ' FROM ' + CERTIFICATES + ' ORDER BY id')).all()
        conn.execute(text('INSERT INTO ' + STAGING + ' (' + columns + ", method, wildcard, credential_id) SELECT "
                          + columns + ", 'http-01', 0, NULL FROM " + CERTIFICATES))
        after = conn.execute(text('SELECT ' + columns + ' FROM ' + STAGING + ' ORDER BY id')).all()
        _need(before == after)
        conn.execute(text('DROP TABLE ' + CERTIFICATES))
        conn.execute(text('ALTER TABLE ' + STAGING + ' RENAME TO ' + CERTIFICATES))
    if not modern_binding:
        conn.execute(text('ALTER TABLE ' + BINDINGS + ' ADD COLUMN server_name VARCHAR(253)'))
    conn.execute(text('CREATE TABLE ' + MARKER + ' (version INTEGER NOT NULL)'))
    conn.execute(text('INSERT INTO ' + MARKER + ' (version) VALUES (1)'))
    _inspect(conn)
