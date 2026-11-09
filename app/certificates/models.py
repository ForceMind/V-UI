from sqlalchemy import Boolean, CheckConstraint, Column, Integer, String, UniqueConstraint, inspect, text
from sqlalchemy.orm import validates
from app.models.database import Base


class Certificate(Base):
    __tablename__ = 'managed_certificates'
    __table_args__ = (
        UniqueConstraint('domain', 'environment', 'method', 'wildcard'),
        CheckConstraint("method IN ('http-01','dns-01')", name='ck_certificate_method'),
        CheckConstraint('wildcard IN (0,1)', name='ck_certificate_wildcard'),
        CheckConstraint("credential_id IS NULL OR (length(credential_id)=32 AND credential_id NOT GLOB '*[^0-9a-f]*')",
                        name='ck_certificate_credential'),
        CheckConstraint("(method='http-01' AND wildcard=0 AND credential_id IS NULL) OR "
                        "(method='dns-01' AND credential_id IS NOT NULL)", name='ck_certificate_method_fields'),
    )
    id = Column(String(32), primary_key=True)
    domain = Column(String(253), nullable=False)
    email = Column(String(254), nullable=False)
    environment = Column(String(16), nullable=False)
    method = Column(String(16), nullable=False, default='http-01', server_default='http-01')
    wildcard = Column(Boolean, nullable=False, default=False, server_default=text('0'))
    credential_id = Column(String(32), nullable=True)
    auto_renew = Column(Boolean, nullable=False, default=True)
    revision = Column(String(64), nullable=True)
    not_before = Column(Integer, nullable=True)
    not_after = Column(Integer, nullable=True)
    renew_at = Column(Integer, nullable=False, default=0)
    retry_at = Column(Integer, nullable=False, default=0)
    last_attempt = Column(Integer, nullable=False, default=0)
    failures = Column(Integer, nullable=False, default=0)
    error = Column(String(64), nullable=True)
    created_at = Column(Integer, nullable=False)


    @validates('domain', 'environment', 'method', 'wildcard')
    def _validate_identity(self, key, value):
        from app.certificates.material import CertificateError, domain_name
        if key == 'domain':
            value = domain_name(value)
        elif key == 'environment' and (type(value) is not str or value not in ('production', 'staging')):
            raise CertificateError('INVALID_ENVIRONMENT')
        elif key == 'method' and (type(value) is not str or value not in ('http-01', 'dns-01')):
            raise CertificateError('INVALID_CERTIFICATE_METHOD')
        elif key == 'wildcard' and type(value) is not bool:
            raise CertificateError('INVALID_WILDCARD')
        if inspect(self).has_identity and getattr(self, key) != value:
            raise CertificateError('CERTIFICATE_IDENTITY_IMMUTABLE')
        return value

    @validates('credential_id')
    def _validate_credential_id(self, key, value):
        import re
        from app.certificates.material import CertificateError
        if value is not None and (type(value) is not str or not re.fullmatch(r'[0-9a-f]{32}', value)):
            raise CertificateError('INVALID_DNS_CREDENTIAL')
        if inspect(self).has_identity and self.credential_id != value:
            raise CertificateError('CERTIFICATE_IDENTITY_IMMUTABLE')
        return value


class CertificateJob(Base):
    __tablename__ = 'certificate_jobs'
    id = Column(String(32), primary_key=True)
    certificate_id = Column(String(32), nullable=False, index=True)
    sequence = Column(Integer, nullable=False, index=True)
    state = Column(String(16), nullable=False)  # queued/running/succeeded/failed
    error = Column(String(64), nullable=True)
    created_at = Column(Integer, nullable=False)
    started_at = Column(Integer, nullable=True)
    finished_at = Column(Integer, nullable=True)


class CertificateBinding(Base):
    __tablename__ = 'certificate_bindings'
    target = Column(String(64), primary_key=True)  # panel or inbound:<id>
    certificate_id = Column(String(32), nullable=False, index=True)
    applied_certificate_id = Column(String(32), nullable=True)
    applied_revision = Column(String(64), nullable=True)
    server_name = Column(String(253), nullable=True)
    error = Column(String(64), nullable=True)

    @validates('server_name')
    def _validate_server_name(self, key, value):
        from app.certificates.material import domain_name
        return None if value is None else domain_name(value)
