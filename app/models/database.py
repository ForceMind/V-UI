from sqlalchemy import create_engine, Column, Integer, String, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# Ensure data directory exists
os.makedirs("data", exist_ok=True)

SQLALCHEMY_DATABASE_URL = "sqlite:///./data/v-ui.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    is_active = Column(Boolean, default=True)

class Inbound(Base):
    __tablename__ = "inbounds"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)
    up = Column(Integer, default=0)
    down = Column(Integer, default=0)
    total = Column(Integer, default=0)
    remark = Column(String)
    enable = Column(Boolean, default=True)
    expiry_time = Column(Integer, default=0)
    port = Column(Integer)
    protocol = Column(String)
    settings = Column(JSON)
    stream_settings = Column(JSON)
    tag = Column(String)

def init_db():
    Base.metadata.create_all(bind=engine)
