import os
import json
import logging
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime, Boolean, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./youtube_automation.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Settings(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VideoJob(Base):
    __tablename__ = "video_jobs"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String, unique=True, index=True)
    source_url = Column(String)
    status = Column(String, default="pending")   # pending, downloading, analyzing, processing, done, error
    progress = Column(Float, default=0.0)
    message = Column(Text)
    result_data = Column(Text)  # JSON
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UploadSchedule(Base):
    __tablename__ = "upload_schedules"
    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(String, unique=True, index=True)
    drive_folder_link = Column(String)
    upload_times = Column(Text)   # JSON array ["12:00", "20:00"]
    is_active = Column(Boolean, default=True)
    last_upload = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UploadLog(Base):
    __tablename__ = "upload_logs"
    id = Column(Integer, primary_key=True, index=True)
    video_title = Column(String)
    youtube_video_id = Column(String)
    status = Column(String)
    error_message = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized.")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_setting(db, key: str, default=None):
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        try:
            return json.loads(row.value)
        except Exception:
            return row.value
    return default


def set_setting(db, key: str, value):
    row = db.query(Settings).filter(Settings.key == key).first()
    serialized = json.dumps(value) if not isinstance(value, str) else value
    if row:
        row.value = serialized
        row.updated_at = datetime.utcnow()
    else:
        row = Settings(key=key, value=serialized)
        db.add(row)
    db.commit()
