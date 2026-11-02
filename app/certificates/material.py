"""Validate material before it can replace any live consumer."""
from datetime import datetime, timezone
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import ssl
import stat
import sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError


class CertificateError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_MAX_CA_BYTES = 4 * 1024 * 1024
_MAX_CA_CERTIFICATES = 4096
_MAX_CA_DIRECTORY_ENTRIES = 8192
_LINUX_CA_BUNDLES = (
    '/etc/ssl/certs/ca-certificates.crt',  # Debian, Ubuntu, Alpine
    '/etc/pki/tls/certs/ca-bundle.crt',  # RHEL family
    '/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem',
    '/etc/ssl/cert.pem',
)


def _ca_file(path: Path, *, limit: int = _MAX_CA_BYTES) -> bytes:
    # System bundles/hash links may legitimately be symlinks. Only read a
    # regular file and bound its bytes; never consume an arbitrary device/FIFO.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError()
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise ValueError()
    return raw


def _ca_directory(path: Path) -> list[x509.Certificate]:
    # OpenSSL capath loads subject-hash entries lazily, so get_ca_certs() can
    # remain empty until a handshake. Enumerate only its certificate entries,
    # not arbitrary PEM files or the revocation-list (<hash>.rN) namespace.
    from OpenSSL.crypto import X509  # Already pinned in the application runtime.
    roots, seen, groups, remaining = [], set(), {}, _MAX_CA_BYTES
    for count, entry in enumerate(path.iterdir(), start=1):
        if count > _MAX_CA_DIRECTORY_ENTRIES:
            raise ValueError()
        match = re.fullmatch(r'([0-9a-f]{8})\.(0|[1-9][0-9]*)', entry.name)
        if match:
            groups.setdefault(match[1], set()).add(match[2])
    for subject_hash, suffixes in groups.items():
        index = 0
        while str(index) in suffixes:
            raw = _ca_file(path / (subject_hash + '.' + str(index)), limit=remaining)
            remaining -= len(raw)
            index += 1
            certificates = x509.load_pem_x509_certificates(raw)
            if len(certificates) != 1:
                raise ValueError()
            certificate = certificates[0]
            # Old MD5 aliases share the same filename shape. They must not
            # expand current OpenSSL trust, nor invalidate otherwise good roots.
            if f'{X509.from_cryptography(certificate).subject_name_hash():08x}' != subject_hash:
                continue
            try:
                if not certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                    continue
            except x509.ExtensionNotFound:
                continue
            der = certificate.public_bytes(serialization.Encoding.DER)
            if der not in seen:
                seen.add(der)
                roots.append(certificate)
                if len(roots) > _MAX_CA_CERTIFICATES:
                    raise ValueError()
    return roots


def _certificate_trust_store(trusted_roots: bytes | None) -> Store:
    try:
        if trusted_roots is not None:
            # An explicit empty/invalid repository must not select host trust.
            if not isinstance(trusted_roots, bytes) or len(trusted_roots) > _MAX_CA_BYTES:
                raise ValueError()
            roots = x509.load_pem_x509_certificates(trusted_roots)
        elif 'SSL_CERT_FILE' in os.environ or 'SSL_CERT_DIR' in os.environ:
            # Explicit settings define an isolated trust repository. Do not
            # supplement it with unrequested host defaults, even on failure.
            roots = []
            if os.environ.get('SSL_CERT_FILE'):
                roots.extend(x509.load_pem_x509_certificates(_ca_file(Path(os.environ['SSL_CERT_FILE']))))
            for directory in os.environ.get('SSL_CERT_DIR', '').split(os.pathsep):
                if directory:
                    roots.extend(_ca_directory(Path(directory)))
        else:
            roots = [x509.load_der_x509_certificate(raw) for raw in
                     ssl.create_default_context().get_ca_certs(binary_form=True)]
            if not roots:
                paths = ssl.get_default_verify_paths()
                candidates = [paths.cafile, paths.openssl_cafile]
                if sys.platform.startswith('linux'):
                    candidates.extend(_LINUX_CA_BUNDLES)
                for candidate in dict.fromkeys(candidates):
                    if candidate and Path(candidate).exists():
                        roots = x509.load_pem_x509_certificates(_ca_file(Path(candidate)))
                        break
                else:
                    for directory in dict.fromkeys((paths.capath, paths.openssl_capath)):
                        if directory and Path(directory).exists():
                            roots.extend(_ca_directory(Path(directory)))
        roots = list({certificate.public_bytes(serialization.Encoding.DER): certificate
                      for certificate in roots}.values())
        if not roots or len(roots) > _MAX_CA_CERTIFICATES:
            raise ValueError()
        return Store(roots)
    except Exception:
        raise CertificateError('CERTIFICATE_CA_STORE_UNAVAILABLE') from None


def domain_name(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CertificateError('INVALID_DOMAIN')
    try:
        name = value.rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise CertificateError('INVALID_DOMAIN') from None
    if len(name) > 253 or '.' not in name or not all(
        re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in name.split('.')
    ) or name.split('.')[-1].isdigit():
        raise CertificateError('INVALID_DOMAIN')
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name
    raise CertificateError('DOMAIN_REQUIRED')


def email_address(value: str) -> str:
    if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(r'[A-Za-z0-9._%+\-]+@[^@]+', value):
        raise CertificateError('INVALID_EMAIL')
    local, host = value.rsplit('@', 1)
    return local + '@' + domain_name(host)


def make_csr(domain: str) -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)] if len(domain) <= 64 else []))
           .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
           .sign(key, hashes.SHA256()))
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()), csr.public_bytes(serialization.Encoding.PEM))


def validate_material(chain_pem: bytes, key_pem: bytes, domain: str, *,
                      trusted_roots: bytes | None = None, verify_chain: bool = True) -> dict:
    try:
        if len(chain_pem) > 131072 or len(key_pem) > 16384:
            raise ValueError()
        chain = x509.load_pem_x509_certificates(chain_pem)
        leaf = chain[0]
        key = serialization.load_pem_private_key(key_pem, password=None)
        encode = lambda k: k.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if encode(key.public_key()) != encode(leaf.public_key()):
            raise CertificateError('KEY_MISMATCH')
        names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        if names != [domain]:
            raise CertificateError('DOMAIN_MISMATCH')
        now = datetime.now(timezone.utc)
        if not leaf.not_valid_before_utc <= now < leaf.not_valid_after_utc:
            raise CertificateError('CERTIFICATE_EXPIRED_OR_NOT_YET_VALID')
        if verify_chain:
            store = _certificate_trust_store(trusted_roots)
            try:
                PolicyBuilder().store(store).time(now).build_server_verifier(x509.DNSName(domain)).verify(leaf, chain[1:])
            except VerificationError:
                raise CertificateError('CERTIFICATE_CHAIN_VERIFICATION_FAILED') from None
        start, end = int(leaf.not_valid_before_utc.timestamp()), int(leaf.not_valid_after_utc.timestamp())
        return {'revision': hashlib.sha256(chain_pem).hexdigest(), 'not_before': start, 'not_after': end,
                'renew_at': start + (end - start) * 2 // 3}
    except CertificateError:
        raise
    except Exception:
        # Do not echo PEM, CSR, private key or upstream diagnostic payloads.
        raise CertificateError('INVALID_CERTIFICATE_MATERIAL') from None


def private_write(path: Path, content: bytes) -> None:
    import os
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
