import os
import logging
import json
import random
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_db_session_factory = None


def init_scheduler(db_session_factory):
    global _db_session_factory
    _db_session_factory = db_session_factory
    
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started.")
    
    # Load saved schedules from DB on startup
    _reload_all_schedules()


def _reload_all_schedules():
    """Reload all active schedules from DB."""
    try:
        from models.database import UploadSchedule
        db = _db_session_factory()
        schedules = db.query(UploadSchedule).filter(UploadSchedule.is_active == True).all()
        for s in schedules:
            _add_schedule_jobs(s)
        db.close()
    except Exception as e:
        logger.error(f"Failed to reload schedules: {e}")


def _add_schedule_jobs(schedule):
    """Add APScheduler jobs for a schedule."""
    times = json.loads(schedule.upload_times) if isinstance(schedule.upload_times, str) else schedule.upload_times
    
    for t in times:
        hour, minute = map(int, t.split(":"))
        job_id = f"upload_{schedule.schedule_id}_{t.replace(':', '')}"
        
        # Remove existing job if any
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        
        scheduler.add_job(
            func=_run_scheduled_upload,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=pytz.timezone("Asia/Dhaka")),
            args=[schedule.schedule_id, schedule.drive_folder_link],
            id=job_id,
            replace_existing=True,
            name=f"Upload at {t} for schedule {schedule.schedule_id[:8]}"
        )
        logger.info(f"Scheduled upload job: {job_id} at {t} BD time")


async def _run_scheduled_upload(schedule_id: str, drive_folder_link: str):
    """Execute scheduled upload: pick random video from Drive, upload to YouTube."""
    from services.drive_service import get_random_video_from_zip
    from services.ai_analyzer import generate_ai_metadata
    from services.youtube_service import upload_video, is_authenticated
    from services.video_processor import extract_thumbnail
    from models.database import UploadLog, UploadSchedule
    from utils.helpers import generate_id
    
    logger.info(f"[SCHEDULER] Running scheduled upload for schedule: {schedule_id}")
    job_id = generate_id()
    
    db = _db_session_factory()
    log_entry = UploadLog(
        video_title="Scheduled Upload",
        youtube_video_id="",
        status="running"
    )
    db.add(log_entry)
    db.commit()
    
    try:
        if not is_authenticated():
            raise Exception("YouTube not authenticated.")
        
        # Get random video from Drive
        video_path = get_random_video_from_zip(drive_folder_link, job_id)
        
        # Extract thumbnail at 5 seconds
        thumb_path = None
        try:
            thumb_path = extract_thumbnail(video_path, 5.0, f"sched_{job_id}")
        except Exception as e:
            logger.warning(f"Thumbnail extraction failed: {e}")
        
        # Generate AI metadata
        filename = os.path.basename(video_path)
        meta = generate_ai_metadata(
            title=filename.replace(".mp4", "").replace("_", " "),
            hook="Watch this amazing short!",
            reason="Scheduled daily upload"
        )
        
        tags = [t.lstrip("#") for t in meta.get("hashtags", "").split() if t.startswith("#")]
        
        # Upload to YouTube
        video_id = upload_video(
            video_path=video_path,
            title=meta.get("title", filename[:100]),
            description=meta.get("description", "") + "\n\n" + meta.get("hashtags", ""),
            tags=tags,
            thumbnail_path=thumb_path,
            privacy="public"
        )
        
        # Update log
        log_entry.video_title = meta.get("title", filename)
        log_entry.youtube_video_id = video_id
        log_entry.status = "success"
        
        # Update schedule last_upload
        sched = db.query(UploadSchedule).filter(UploadSchedule.schedule_id == schedule_id).first()
        if sched:
            sched.last_upload = datetime.utcnow()
        
        db.commit()
        logger.info(f"[SCHEDULER] Upload success! Video ID: {video_id}")
        
        # Cleanup
        try:
            os.remove(video_path)
            if thumb_path:
                os.remove(thumb_path)
        except Exception:
            pass
        
    except Exception as e:
        logger.error(f"[SCHEDULER] Upload failed: {e}")
        log_entry.status = "failed"
        log_entry.error_message = str(e)
        db.commit()
    finally:
        db.close()


def add_upload_schedule(schedule_id: str, drive_folder_link: str, upload_times: list):
    """Add new upload schedule."""
    from models.database import UploadSchedule
    
    class MockSchedule:
        pass
    
    s = MockSchedule()
    s.schedule_id = schedule_id
    s.drive_folder_link = drive_folder_link
    s.upload_times = json.dumps(upload_times)
    _add_schedule_jobs(s)


def remove_upload_schedule(schedule_id: str):
    """Remove all jobs for a schedule."""
    jobs = scheduler.get_jobs()
    for job in jobs:
        if job.id.startswith(f"upload_{schedule_id}"):
            scheduler.remove_job(job.id)
            logger.info(f"Removed schedule job: {job.id}")


def get_all_jobs_info() -> list:
    """Get info about all scheduled jobs."""
    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time.isoformat() if job.next_run_time else None
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": next_run
        })
    return jobs
