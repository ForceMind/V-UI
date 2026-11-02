"""Synthetic PKI only; never install roots, access the network, or initialize data."""
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from OpenSSL.crypto import X509

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.certificates import material


class CertificateSystemTrustTests(unittest.TestCase):
    domain = 'panel.example.test'

    @staticmethod
    def ca(name):
        now = datetime.now(timezone.utc)
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2)).not_valid_after(now + timedelta(days=2))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False, key_agreement=False,
                key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))
        return key, certificate

    @classmethod
    def setUpClass(cls):
        cls.ca_key, cls.root_certificate = cls.ca('V-UI synthetic accepted CA')
        _, cls.other_root = cls.ca('V-UI synthetic unrelated CA')
        cls.root_pem = cls.root_certificate.public_bytes(serialization.Encoding.PEM)
        cls.other_root_pem = cls.other_root.public_bytes(serialization.Encoding.PEM)

    def leaf(self, *, before=None, after=None):
        now = datetime.now(timezone.utc)
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, self.domain)]))
            .issuer_name(self.root_certificate.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(before or now - timedelta(minutes=5))
            .not_valid_after(after or now + timedelta(hours=12))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(self.domain)]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.ca_key.public_key()), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(self.ca_key, hashes.SHA256()))
        return (certificate.public_bytes(serialization.Encoding.PEM) + self.root_pem,
                key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vui-ca-store-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.bundle = self.work / 'ca-certificates.crt'
        self.bundle.write_bytes(self.root_pem)
        self.capath = self.work / 'certs'
        self.capath.mkdir()
        self.chain, self.key = self.leaf()
        # No test may silently consume the developer machine's actual roots.
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.context = Mock()
        self.context.get_ca_certs.return_value = []
        self.default_context = self.enterContext(patch.object(material.ssl, 'create_default_context', return_value=self.context))
        self.paths = SimpleNamespace(cafile=None, capath=str(self.capath),
            openssl_cafile=str(self.work / 'missing-default.pem'), openssl_capath=str(self.capath))
        self.enterContext(patch.object(material.ssl, 'get_default_verify_paths', return_value=self.paths))
        self.enterContext(patch.object(material, '_LINUX_CA_BUNDLES', (str(self.bundle),)))
        self.enterContext(patch.object(material.sys, 'platform', 'linux'))

    def validate(self, **kwargs):
        return material.validate_material(self.chain, self.key, self.domain, **kwargs)

    def hash_entry(self, certificate=None, *, index=0, directory=None):
        certificate = certificate or self.root_certificate
        identity = f'{X509.from_cryptography(certificate).subject_name_hash():08x}'
        path = (directory or self.capath) / (identity + '.' + str(index))
        path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        return path

    def assert_error(self, code, callback):
        with self.assertRaises(material.CertificateError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertIsNone(caught.exception.__cause__)
        return caught.exception

    def test_capath_only_default_enumeration_uses_linux_system_bundle(self):
        # Reproduce portable Python: cafile=None, only lazy capath available,
        # zero enumerated roots, but the distro's complete PEM bundle exists.
        result = self.validate()
        self.assertEqual(result['revision'], hashlib.sha256(self.chain).hexdigest())
        self.assertLess(result['not_before'], result['renew_at'])
        self.assertLess(result['renew_at'], result['not_after'])
        self.context.get_ca_certs.assert_called_once_with(binary_form=True)

    def test_default_enumerated_roots_remain_supported_without_file_fallback(self):
        self.context.get_ca_certs.return_value = [self.root_certificate.public_bytes(serialization.Encoding.DER)]
        self.bundle.write_bytes(b'damaged repository must not be read')
        self.validate()
        material.ssl.get_default_verify_paths.assert_not_called()

    def test_explicit_roots_take_precedence_over_system_and_environment(self):
        os.environ.update(SSL_CERT_FILE='/missing/explicit-ca.pem', SSL_CERT_DIR='/missing/explicit-dir')
        self.bundle.write_bytes(self.other_root_pem)
        self.validate(trusted_roots=self.root_pem)
        self.default_context.assert_not_called()
        material.ssl.get_default_verify_paths.assert_not_called()

    def test_explicit_empty_malformed_and_oversize_roots_never_fall_back(self):
        for roots in (b'', b'not a certificate', b'x' * (material._MAX_CA_BYTES + 1)):
            with self.subTest(size=len(roots)):
                self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE',
                    lambda: self.validate(trusted_roots=roots))
        self.default_context.assert_not_called()

    def test_valid_wrong_explicit_ca_does_not_trust_root_from_returned_chain(self):
        self.assertIn(self.root_pem, self.chain)
        self.assert_error('CERTIFICATE_CHAIN_VERIFICATION_FAILED',
            lambda: self.validate(trusted_roots=self.other_root_pem))
        self.default_context.assert_not_called()

    def test_empty_malformed_and_missing_default_stores_are_distinct_from_bad_material(self):
        for contents in (b'', b'not a certificate', None):
            with self.subTest(contents=contents):
                if contents is None:
                    self.bundle.unlink()
                else:
                    self.bundle.write_bytes(contents)
                self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)

    def test_unreadable_and_nonregular_repository_are_rejected_safely(self):
        with patch.object(material, '_ca_file', side_effect=PermissionError('sensitive path details')):
            self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)
        self.bundle.unlink()
        self.bundle.mkdir()
        self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)

    def test_explicit_environment_bundle_is_used_without_host_roots(self):
        isolated = self.work / 'isolated.pem'
        isolated.write_bytes(self.root_pem)
        os.environ.update(SSL_CERT_FILE=str(isolated), SSL_CERT_DIR=str(self.capath))
        self.bundle.write_bytes(self.other_root_pem)
        self.validate()
        self.default_context.assert_not_called()

    def test_wrong_environment_ca_rejects_chain_even_when_system_bundle_is_correct(self):
        isolated = self.work / 'wrong.pem'
        isolated.write_bytes(self.other_root_pem)
        os.environ.update(SSL_CERT_FILE=str(isolated), SSL_CERT_DIR=str(self.capath))
        self.assert_error('CERTIFICATE_CHAIN_VERIFICATION_FAILED', self.validate)
        self.default_context.assert_not_called()

    def test_bad_empty_or_missing_environment_file_does_not_escape_isolation(self):
        isolated = self.work / 'isolated.pem'
        os.environ.update(SSL_CERT_FILE=str(isolated), SSL_CERT_DIR=str(self.capath))
        for contents in (b'', b'invalid trust repository', None):
            with self.subTest(contents=contents):
                if contents is None:
                    isolated.unlink()
                else:
                    isolated.write_bytes(contents)
                self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)
        os.environ.update(SSL_CERT_FILE='', SSL_CERT_DIR='')
        self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)
        self.default_context.assert_not_called()

    def test_explicit_hashed_capath_is_enumerated_without_loading_other_pem_files(self):
        entry = self.hash_entry()
        (self.capath / 'ignored.pem').write_bytes(b'not a certificate')
        (self.capath / (entry.stem + '.r0')).write_bytes(b'not a certificate revocation list')
        os.environ.update(SSL_CERT_FILE='', SSL_CERT_DIR=str(self.capath))
        self.validate()
        self.default_context.assert_not_called()

    def test_invalid_hashed_directory_entry_and_empty_directory_fail_closed(self):
        os.environ.update(SSL_CERT_FILE='', SSL_CERT_DIR=str(self.capath))
        self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)
        self.hash_entry().write_bytes(b'invalid hashed entry')
        self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)

    def test_default_openssl_directory_is_supported_without_a_bundle(self):
        self.bundle.unlink()
        self.hash_entry()
        self.validate()

    def test_wrong_hash_uppercase_and_noncanonical_index_do_not_expand_trust(self):
        identity = f'{X509.from_cryptography(self.root_certificate).subject_name_hash():08x}'
        wrong_hash = f'{(int(identity,16) + 1) % (2**32):08x}'
        other_hash = f'{X509.from_cryptography(self.other_root).subject_name_hash():08x}'
        if wrong_hash == other_hash:
            wrong_hash = f'{(int(wrong_hash,16) + 1) % (2**32):08x}'
        for name in (wrong_hash+'.0', identity+'.00', identity.upper()+'.0'):
            # Numeric-only hashes have no distinct uppercase spelling.
            if name == identity+'.0':
                continue
            with self.subTest(name=name):
                path=self.capath/name;path.write_bytes(self.root_pem)
                self.assertEqual(material._ca_directory(self.capath),[])
                path.unlink()
        self.hash_entry(self.other_root)
        (self.capath/(wrong_hash+'.0')).write_bytes(self.root_pem)
        os.environ.update(SSL_CERT_FILE='', SSL_CERT_DIR=str(self.capath))
        self.assert_error('CERTIFICATE_CHAIN_VERIFICATION_FAILED', self.validate)

    def test_hash_indices_start_at_zero_and_stop_at_first_gap(self):
        later=self.hash_entry(index=1)
        self.assertEqual(material._ca_directory(self.capath),[])
        later.unlink()
        first=self.hash_entry()
        self.hash_entry(index=2)
        with patch.object(material,'_ca_file',wraps=material._ca_file) as reads:
            self.assertEqual(len(material._ca_directory(self.capath)),1)
        self.assertEqual([call.args[0] for call in reads.call_args_list],[first])

    def test_multiple_certificates_in_one_hashed_file_cannot_add_an_unrelated_root(self):
        self.hash_entry().write_bytes(self.root_pem+self.other_root_pem)
        os.environ.update(SSL_CERT_FILE='', SSL_CERT_DIR=str(self.capath))
        self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)

    def test_hashed_leaf_certificate_does_not_become_a_trust_anchor(self):
        leaf=x509.load_pem_x509_certificate(self.chain)
        self.hash_entry(leaf)
        self.assertEqual(material._ca_directory(self.capath),[])
        os.environ.update(SSL_CERT_FILE='', SSL_CERT_DIR=str(self.capath))
        self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)

    def test_duplicate_hash_links_are_deduplicated_but_bytes_and_entries_are_bounded(self):
        first=self.hash_entry()
        second=first.with_suffix('.1');second.symlink_to(first.name)
        with patch.object(material,'_MAX_CA_CERTIFICATES',1):
            self.assertEqual(len(material._ca_directory(self.capath)),1)
        os.environ.update(SSL_CERT_FILE='', SSL_CERT_DIR=str(self.capath))
        with patch.object(material,'_MAX_CA_BYTES',len(self.root_pem)):
            self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)
        with patch.object(material,'_MAX_CA_DIRECTORY_ENTRIES',1):
            self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)
        second.unlink();self.hash_entry(self.other_root)
        with patch.object(material,'_MAX_CA_CERTIFICATES',1):
            self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)

    def test_repeated_explicit_directory_is_deduplicated_before_certificate_limit(self):
        self.hash_entry()
        os.environ.update(SSL_CERT_FILE='', SSL_CERT_DIR=os.pathsep.join([str(self.capath)]*2))
        with patch.object(material,'_MAX_CA_CERTIFICATES',1):
            self.validate()

    def test_default_openssl_file_is_supported_without_linux_specific_fallback(self):
        self.paths.cafile = str(self.bundle)
        with patch.object(material.sys, 'platform', 'darwin'):
            self.validate()

    def test_material_error_categories_and_staging_boundary_are_preserved(self):
        _, wrong_key = self.leaf()
        self.assert_error('KEY_MISMATCH', lambda: material.validate_material(
            self.chain, wrong_key, self.domain, trusted_roots=self.root_pem))
        self.assert_error('DOMAIN_MISMATCH', lambda: material.validate_material(
            self.chain, self.key, 'other.example.test', trusted_roots=self.root_pem))
        for chain, key in ((b'not a chain', self.key), (self.chain, b'not a private key')):
            self.assert_error('INVALID_CERTIFICATE_MATERIAL', lambda: material.validate_material(
                chain, key, self.domain, trusted_roots=self.root_pem))
        # Staging still validates key/domain/time but does not require public CA trust.
        with patch.object(material, '_certificate_trust_store', side_effect=AssertionError('must not load trust')):
            self.validate(verify_chain=False)

    def test_expired_and_not_yet_valid_certificates_are_rejected_before_trust_loading(self):
        now = datetime.now(timezone.utc)
        for before, after in ((now - timedelta(days=2), now - timedelta(days=1)),
                              (now + timedelta(days=1), now + timedelta(days=2))):
            chain, key = self.leaf(before=before, after=after)
            with self.subTest(before=before):
                self.assert_error('CERTIFICATE_EXPIRED_OR_NOT_YET_VALID',
                    lambda: material.validate_material(chain, key, self.domain, trusted_roots=self.root_pem))
        self.default_context.assert_not_called()

    def test_raw_trust_and_verification_exceptions_do_not_leak(self):
        sensitive = 'SECRET-PEM-PRIVATE-KEY-OR-PROVIDER-DIAGNOSTIC'
        with patch.object(material.ssl, 'create_default_context', side_effect=RuntimeError(sensitive)):
            error = self.assert_error('CERTIFICATE_CA_STORE_UNAVAILABLE', self.validate)
        self.assertNotIn(sensitive, str(error))
        verifier = Mock()
        verifier.store.return_value.time.return_value.build_server_verifier.return_value.verify.side_effect = material.VerificationError(sensitive)
        with patch.object(material, 'PolicyBuilder', return_value=verifier):
            error = self.assert_error('CERTIFICATE_CHAIN_VERIFICATION_FAILED',
                lambda: self.validate(trusted_roots=self.root_pem))
        self.assertNotIn(sensitive, str(error))


if __name__ == '__main__':
    unittest.main()
