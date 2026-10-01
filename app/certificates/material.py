"""Validate material before it can replace any live consumer."""
from datetime import datetime, timezone
import hashlib
import ipaddress
from pathlib import Path
import re
import ssl
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from cryptography.x509.verification import PolicyBuilder, Store


class CertificateError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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
            roots = (x509.load_pem_x509_certificates(trusted_roots) if trusted_roots else
                     [x509.load_der_x509_certificate(raw) for raw in ssl.create_default_context().get_ca_certs(binary_form=True)])
            PolicyBuilder().store(Store(roots)).time(now).build_server_verifier(x509.DNSName(domain)).verify(leaf, chain[1:])
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
