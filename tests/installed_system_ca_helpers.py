"""Actual installed portable validator; temporary host trust only on empty hosted CI.

Root executes the fixed stdlib-only bundle controller below. All PKI generation
and application imports execute as v-ui with the installed portable interpreter.
No application configuration, database, certificate binding or session is edited.
"""
import base64
import json
import os
from pathlib import Path
import platform
import re
import subprocess


_BUNDLE_CONTROL = r'''
import base64, hashlib, json, os, re, stat, sys, tempfile, uuid
from pathlib import Path

BUNDLE=Path('/etc/ssl/certs/ca-certificates.crt')
LIMIT=4*1024*1024
assert sys.platform.startswith('linux') and os.geteuid()==0
for parent in (Path('/etc'),Path('/etc/ssl'),BUNDLE.parent):
    info=parent.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode & 0o022

def read(path,limit=LIMIT,private=False):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        assert stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1
        assert not info.st_mode & 0o022 and info.st_size<=limit
        if private:assert stat.S_IMODE(info.st_mode)==0o600
        raw=stream.read(limit+1)
        assert len(raw)<=limit
    return raw,{'mode':stat.S_IMODE(info.st_mode),'uid':info.st_uid,'gid':info.st_gid,
                'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}

def save(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())

def sync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

def replace(raw,metadata):
    read(BUNDLE)  # Reject replacement of a symbolic/nonregular destination.
    fd,name=tempfile.mkstemp(prefix='.vui-ci-ca-',dir=BUNDLE.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchown(stream.fileno(),metadata['uid'],metadata['gid'])
            os.fchmod(stream.fileno(),metadata['mode'])
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(name,BUNDLE);sync(BUNDLE.parent)
    finally:
        if os.path.lexists(name):os.unlink(name)
    current,actual=read(BUNDLE)
    assert current==raw and all(actual[key]==metadata[key] for key in ('mode','uid','gid'))
    return actual

action=sys.argv[1]
if action=='save':
    original,metadata=read(BUNDLE)
    parent=Path('/var/tmp');info=parent.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid==0
    assert not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX
    directory=parent/('vui-system-ca-'+uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    save(directory/'original',original)
    save(directory/'metadata.json',json.dumps(metadata,sort_keys=True).encode())
    sync(directory)
    print(json.dumps({'journal':str(directory),'original':metadata}))
else:
    assert action in ('append','restore')
    directory=Path(sys.argv[2])
    assert re.fullmatch(r'/var/tmp/vui-system-ca-[0-9a-f]{32}',str(directory))
    info=directory.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o700
    original,original_info=read(directory/'original',private=True)
    raw,_=read(directory/'metadata.json',16384,private=True);metadata=json.loads(raw)
    assert set(metadata)=={'mode','uid','gid','size','sha256'}
    assert metadata['uid']==0 and metadata['sha256']==original_info['sha256'] and metadata['size']==len(original)
    if action=='append':
        current,info=read(BUNDLE);assert current==original
        assert all(info[key]==metadata[key] for key in ('mode','uid','gid'))
        certificate=base64.b64decode(sys.argv[3],validate=True)
        assert 0<len(certificate)<=16384 and certificate.startswith(b'-----BEGIN CERTIFICATE-----\n')
        assert certificate.endswith(b'-----END CERTIFICATE-----\n')
        actual=replace(original+b'\n'+certificate,metadata)
        print(json.dumps({'appended':True,'bundle':actual}))
    else:
        actual=replace(original,metadata)
        assert actual==metadata
        (directory/'original').unlink();(directory/'metadata.json').unlink();directory.rmdir()
        print(json.dumps({'restored':True,'bundle':actual}))
'''


_PORTABLE_PROBE = r'''
import base64, json, os, pwd, re, ssl, stat, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

release=Path(sys.argv[1]);payload=release/'payload';python=release/'runtime/python/bin/python3'
assert release.parent==Path('/var/lib/v-ui/releases') and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}',release.name)
assert os.geteuid()==pwd.getpwnam('v-ui').pw_uid and os.geteuid()!=0
assert Path(sys.executable).resolve()==python.resolve() and Path(sys.prefix).resolve()==python.parent.parent.resolve()
assert sys.version_info[:2]==(3,12)
assert not {'SSL_CERT_FILE','SSL_CERT_DIR'} & set(os.environ)
version=(payload/'VERSION').read_text().strip();assert re.fullmatch(r'\d+\.\d+\.\d+',version)
assert tuple(map(int,version.split('.'))) >= (0,4,12)
sys.path.insert(0,str(payload))
from app.certificates import material
assert Path(material.__file__).resolve()==(payload/'app/certificates/material.py').resolve()
action=sys.argv[2]
if action=='create':
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID,NameOID
    now=datetime.now(timezone.utc);root_key=ec.generate_private_key(ec.SECP256R1())
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'V-UI hosted CI temporary CA')])
    ca=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()),critical=False)
        .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,
            data_encipherment=False,key_agreement=False,key_cert_sign=True,crl_sign=True,
            encipher_only=False,decipher_only=False),critical=True).sign(root_key,hashes.SHA256()))
    key=ec.generate_private_key(ec.SECP256R1());domain='installed-ca.example.test'
    leaf=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,domain)]))
        .issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]),critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
        .sign(root_key,hashes.SHA256()))
    os.umask(0o077);directory=Path(tempfile.mkdtemp(prefix='vui-installed-ca-',dir='/tmp'))
    root=ca.public_bytes(serialization.Encoding.PEM)
    (directory/'chain.pem').write_bytes(leaf.public_bytes(serialization.Encoding.PEM)+root)
    (directory/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    print(json.dumps({'directory':str(directory),'root':base64.b64encode(root).decode()}))
else:
    directory=Path(sys.argv[3]);assert re.fullmatch(r'/tmp/vui-installed-ca-[A-Za-z0-9_-]+',str(directory))
    info=directory.lstat();assert stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid()
    assert stat.S_IMODE(info.st_mode)==0o700
    if action=='cleanup':
        (directory/'chain.pem').unlink();(directory/'key.pem').unlink();directory.rmdir()
        print(json.dumps({'cleaned':True}))
    else:
        assert action in ('reject','accept')
        chain=(directory/'chain.pem').read_bytes();key=(directory/'key.pem').read_bytes()
        report={'uid':os.geteuid(),'python':sys.executable,'prefix':sys.prefix,'version':sys.version,
                'payload':str(payload),'default_paths':ssl.get_default_verify_paths()._asdict(),
                'default_enumerated_roots':len(ssl.create_default_context().get_ca_certs(binary_form=True))}
        def validate(domain='installed-ca.example.test'):
            return material.validate_material(chain,key,domain)
        if action=='reject':
            try:validate()
            except material.CertificateError as exc:
                assert exc.code=='CERTIFICATE_CHAIN_VERIFICATION_FAILED',exc.code
                report['unknown_ca_rejected']=exc.code
            else:raise AssertionError('Unknown synthetic CA was trusted')
        else:
            report['default_validated_revision']=validate()['revision']
            try:validate('wrong.example.test')
            except material.CertificateError as exc:
                assert exc.code=='DOMAIN_MISMATCH',exc.code
                report['wrong_domain_rejected']=exc.code
            else:raise AssertionError('Wrong certificate domain was accepted')
            # Explicit injection subcase, separate from the real default above.
            capath=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            capath.load_verify_locations(capath='/etc/ssl/certs')
            assert len(capath.get_ca_certs(binary_form=True))==0
            previous=material.ssl.create_default_context
            try:
                material.ssl.create_default_context=lambda:capath
                assert validate()['revision']==report['default_validated_revision']
            finally:material.ssl.create_default_context=previous
            report['forced_capath_only']={'enumerated_roots':0,'injected':True,'validated':True}
        print(json.dumps(report,sort_keys=True))
'''


def installed_system_ca_check(test, release):
    """Do not call on a user host; caller first proved an empty CI installation."""
    expected = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'ForceMind/V-UI',
                'RUNNER_ENVIRONMENT': 'github-hosted', 'VUI_SYSTEM_INSTALL_TEST': '1'}
    if platform.system() != 'Linux' or os.geteuid() == 0 or any(
            os.environ.get(name) != value for name, value in expected.items()):
        raise AssertionError('Installed CA mutation requires an explicit empty GitHub-hosted runner')
    release = Path(release)
    test.assertEqual(release.parent, Path('/var/lib/v-ui/releases'))
    test.assertRegex(release.name, r'\A[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z')
    python = release / 'runtime/python/bin/python3'

    def root(action, *args):
        result = subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/python3', '-B', '-I', '-S',
            '-c', _BUNDLE_CONTROL, action, *args], check=True, capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout)

    def portable(action, directory=None):
        command = ['/usr/bin/sudo', '-n', '-u', 'v-ui', '/usr/bin/env',
            '-u', 'SSL_CERT_FILE', '-u', 'SSL_CERT_DIR', '-u', 'PYTHONPATH', '-u', 'SSLKEYLOGFILE',
            str(python), '-B', '-I', '-c', _PORTABLE_PROBE, str(release), action]
        if directory is not None:
            command.append(directory)
        result = subprocess.run(command, check=True, cwd='/tmp', capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout)

    created = portable('create')
    directory = created['directory']
    test.assertRegex(directory, r'\A/tmp/vui-installed-ca-[A-Za-z0-9_-]+\Z')
    test.assertLess(len(base64.b64decode(created['root'], validate=True)), 16384)
    try:
        before = portable('reject', directory)
        snapshot = root('save')
        try:
            root('append', snapshot['journal'], created['root'])
            added = portable('accept', directory)
        finally:
            restored = root('restore', snapshot['journal'])
            test.assertTrue(restored['restored'])
            test.assertEqual(restored['bundle'], snapshot['original'])
        after = portable('reject', directory)
        test.assertEqual(before['unknown_ca_rejected'], after['unknown_ca_rejected'])
        print('Installed portable system CA evidence: ' + json.dumps(
            {'before': before, 'temporary_root': added, 'after_restore': after,
             'original_bundle': snapshot['original'], 'restored_bundle': restored['bundle']}, sort_keys=True))
    finally:
        test.assertTrue(portable('cleanup', directory)['cleaned'])
