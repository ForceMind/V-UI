from sqlalchemy import Boolean, Column, Integer, String, UniqueConstraint
from app.models.database import Base


class Certificate(Base):
    __tablename__ = 'managed_certificates'
    __table_args__ = (UniqueConstraint('domain', 'environment'),)
    id = Column(String(32), primary_key=True)
    domain = Column(String(253), nullable=False)
    email = Column(String(254), nullable=False)
    environment = Column(String(16), nullable=False)
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
    error = Column(String(64), nullable=True)
