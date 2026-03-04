import os
import json
import uuid
import asyncio
import logging
from pathlib import Path
from typing import Optional, List
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Import local modules
from models.database import init_db, get_db, get_setting, set_setting, VideoJob, UploadSchedule, UploadLog
from utils.helpers import generate_id, OUTPUT_DIR, TEMP_DIR
from sqlalchemy.orm import Session

# ── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from models.database import SessionLocal
    from services.scheduler_service import init_scheduler
    init_scheduler(SessionLocal)
    logger.info("🚀 YouTube Automation Tool started!")
    yield
    logger.info("Shutting down...")

app = FastAPI(title="YouTube Automation Tool", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Static files & outputs
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")
app.mount("/static", StaticFiles(directory="static"), name="static")

# In-memory job progress store
job_progress: dict = {}


# ════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ════════════════════════════════════════════════════════════════════════════

class AnalyzeRequest(BaseModel):
    url: str
    num_clips: int = 5
    use_grok: bool = True
    use_gemini: bool = True
    whisper_model: str = "base"

class ManualCropRequest(BaseModel):
    video_path: str
    start_time: str  # "HH:MM:SS" or "MM:SS"
    end_time: str
    output_name: str = ""

class TextOverlayRequest(BaseModel):
    video_path: str
    text: str
    position: str = "center"
    font_size: int = 60
    text_color: str = "white"
    bg_color: str = "black"
    bg_opacity: float = 0.6

class UploadVideoRequest(BaseModel):
    video_path: str
    title: str
    description: str
    hashtags: str
    privacy: str = "public"
    thumbnail_path: str = ""

class ScheduleRequest(BaseModel):
    drive_folder_link: str
    upload_times: List[str]  # ["12:00", "20:00"]

class SaveSettingsRequest(BaseModel):
    gemini_api_key: str = ""
    grok_api_key: str = ""
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    google_drive_api_key: str = ""
    openai_api_key: str = ""


# ════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ════════════════════════════════════════════════════════════════════════════

async def process_video_job(job_id: str, url: str, num_clips: int,
                            use_grok: bool, use_gemini: bool, whisper_model: str):
    """Full pipeline: download → transcribe → AI analyze → crop clips."""
    from models.database import SessionLocal, VideoJob
    from services.downloader import download_video
    from services.transcriber import extract_audio, transcribe_audio, format_transcript_for_ai
    from services.ai_analyzer import (analyze_with_gemini, analyze_with_grok,
                                       fallback_heatmap_segments, merge_and_rank_segments,
                                       generate_ai_metadata)
    from services.video_processor import crop_video_segment, extract_thumbnail

    db = SessionLocal()
    job = db.query(VideoJob).filter(VideoJob.job_id == job_id).first()

    def update_job(status: str, progress: float, message: str, result=None):
        job.status = status
        job.progress = progress
        job.message = message
        if result:
            job.result_data = json.dumps(result)
        job.updated_at = datetime.utcnow()
        db.commit()
        job_progress[job_id] = {"status": status, "progress": progress, "message": message}
        logger.info(f"[{job_id}] {progress:.0f}% - {message}")

    try:
        update_job("downloading", 5, "📥 ভিডিও ডাউনলোড হচ্ছে...")

        def dl_progress(pct):
            update_job("downloading", pct, f"📥 ডাউনলোড হচ্ছে... {pct:.0f}%")

        video_info = download_video(url, job_id, progress_callback=dl_progress)
        update_job("transcribing", 52, "🎙️ ট্রান্সক্রিপ্ট তৈরি হচ্ছে (Whisper)...")

        audio_path = extract_audio(video_info["path"], job_id)
        segments = transcribe_audio(audio_path, model_size=whisper_model)
        transcript_text = format_transcript_for_ai(segments)

        # Clean audio temp
        try:
            os.remove(audio_path)
        except Exception:
            pass

        update_job("analyzing", 65, "🤖 AI ভাইরাল সেগমেন্ট খুঁজছে...")

        gemini_segs, grok_segs = [], []
        errors = []

        if use_gemini and os.getenv("GEMINI_API_KEY"):
            try:
                gemini_segs = analyze_with_gemini(
                    title=video_info["title"],
                    duration=video_info["duration"],
                    transcript=transcript_text,
                    heatmap=video_info["heatmap"],
                    num_clips=num_clips
                )
            except Exception as e:
                errors.append(f"Gemini: {e}")
                logger.warning(f"Gemini analysis failed: {e}")

        if use_grok and os.getenv("GROK_API_KEY"):
            try:
                grok_segs = analyze_with_grok(
                    title=video_info["title"],
                    duration=video_info["duration"],
                    transcript=transcript_text,
                    heatmap=video_info["heatmap"],
                    num_clips=num_clips
                )
            except Exception as e:
                errors.append(f"Grok: {e}")
                logger.warning(f"Grok analysis failed: {e}")

        # Merge or fallback
        if gemini_segs or grok_segs:
            all_segments = merge_and_rank_segments(gemini_segs, grok_segs)[:num_clips]
        else:
            logger.warning("Both AI failed, using heatmap fallback.")
            all_segments = fallback_heatmap_segments(video_info["heatmap"], video_info["duration"], num_clips)

        update_job("processing", 75, f"✂️ {len(all_segments)} টি ক্লিপ কাট হচ্ছে...")

        clips = []
        for i, seg in enumerate(all_segments):
            pct = 75 + (i / len(all_segments)) * 20
            update_job("processing", pct, f"✂️ ক্লিপ {i+1}/{len(all_segments)} প্রসেস হচ্ছে...")

            try:
                clip_name = f"{job_id}_clip{i+1}_{seg.get('category','short')}"
                clip_path = crop_video_segment(
                    input_path=video_info["path"],
                    start=float(seg["start"]),
                    end=float(seg["end"]),
                    output_name=clip_name
                )
                # Thumbnail
                thumb_ts = float(seg["start"]) + 2
                thumb_path = extract_thumbnail(video_info["path"], thumb_ts, clip_name)

                # AI Metadata
                meta = {}
                try:
                    meta = generate_ai_metadata(
                        title=seg.get("title", ""),
                        hook=seg.get("hook", ""),
                        reason=seg.get("reason", "")
                    )
                except Exception as e:
                    meta = {
                        "title": seg.get("title", f"Short {i+1}"),
                        "description": seg.get("reason", ""),
                        "hashtags": "#shorts #viral #trending"
                    }

                clips.append({
                    "index": i + 1,
                    "rank": seg.get("rank", i + 1),
                    "start": seg["start"],
                    "end": seg["end"],
                    "duration": round(float(seg["end"]) - float(seg["start"]), 1),
                    "title": meta.get("title", seg.get("title", f"Short {i+1}")),
                    "description": meta.get("description", ""),
                    "hashtags": meta.get("hashtags", "#shorts"),
                    "hook": seg.get("hook", ""),
                    "reason": seg.get("reason", ""),
                    "viral_score": seg.get("viral_score", 5.0),
                    "category": seg.get("category", "general"),
                    "clip_path": clip_path,
                    "clip_url": f"/outputs/{Path(clip_path).name}",
                    "thumbnail_path": thumb_path,
                    "thumbnail_url": f"/outputs/{Path(thumb_path).name}" if os.path.exists(thumb_path) else ""
                })
            except Exception as e:
                logger.error(f"Clip {i+1} processing error: {e}")
                clips.append({
                    "index": i + 1,
                    "error": str(e),
                    "start": seg.get("start", 0),
                    "end": seg.get("end", 60)
                })

        result = {
            "video_title": video_info["title"],
            "video_duration": video_info["duration"],
            "video_path": video_info["path"],
            "thumbnail_url": video_info.get("thumbnail_url", ""),
            "has_heatmap": len(video_info.get("heatmap", [])) > 0,
            "clips": clips,
            "ai_errors": errors,
            "total_clips": len(clips)
        }

        update_job("done", 100, f"✅ সম্পন্ন! {len(clips)} টি শর্টস ক্লিপ তৈরি হয়েছে।", result)

    except Exception as e:
        logger.error(f"[{job_id}] Fatal error: {e}", exc_info=True)
        update_job("error", 0, f"❌ ত্রুটি: {str(e)}")
    finally:
        db.close()


# ════════════════════════════════════════════════════════════════════════════
# API ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    return FileResponse("static/index.html")


@app.post("/api/analyze")
async def start_analysis(req: AnalyzeRequest, background_tasks: BackgroundTasks,
                          db: Session = Depends(get_db)):
    """Start video analysis job."""
    job_id = generate_id()
    
    # Inject API keys from DB settings into env
    _load_api_keys_from_db(db)

    job = VideoJob(
        job_id=job_id,
        source_url=req.url,
        status="pending",
        progress=0,
        message="작업 시작됨..."
    )
    db.add(job)
    db.commit()

    job_progress[job_id] = {"status": "pending", "progress": 0, "message": "시작..."}

    background_tasks.add_task(
        process_video_job, job_id, req.url, req.num_clips,
        req.use_grok, req.use_gemini, req.whisper_model
    )

    return {"job_id": job_id, "message": "Job started!"}


@app.get("/api/job/{job_id}")
async def get_job_status(job_id: str, db: Session = Depends(get_db)):
    """Poll job status."""
    if job_id in job_progress:
        info = job_progress[job_id]
        if info["status"] in ["done", "error"]:
            job = db.query(VideoJob).filter(VideoJob.job_id == job_id).first()
            if job and job.result_data:
                info["result"] = json.loads(job.result_data)
        return info
    
    job = db.query(VideoJob).filter(VideoJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    
    result = {}
    if job.result_data:
        result = json.loads(job.result_data)
    
    return {
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "result": result
    }


@app.post("/api/manual-crop")
async def manual_crop(req: ManualCropRequest):
    """Manual crop: cut specific time range."""
    from services.video_processor import crop_video_segment
    from utils.helpers import hhmmss_to_seconds
    
    start_sec = hhmmss_to_seconds(req.start_time)
    end_sec = hhmmss_to_seconds(req.end_time)
    
    if end_sec <= start_sec:
        raise HTTPException(400, "End time must be greater than start time.")
    
    output_name = req.output_name or f"manual_{generate_id()}"
    
    clip_path = crop_video_segment(
        input_path=req.video_path,
        start=start_sec,
        end=end_sec,
        output_name=output_name
    )
    
    return {
        "clip_path": clip_path,
        "clip_url": f"/outputs/{Path(clip_path).name}",
        "duration": round(end_sec - start_sec, 1)
    }


@app.post("/api/add-text")
async def add_text_to_video(req: TextOverlayRequest):
    """Add text overlay to a video clip."""
    from services.video_processor import add_text_overlay
    
    output_name = f"text_{generate_id()}"
    result_path = add_text_overlay(
        input_path=req.video_path,
        text=req.text,
        output_name=output_name,
        position=req.position,
        font_size=req.font_size,
        text_color=req.text_color,
        bg_color=req.bg_color,
        bg_opacity=req.bg_opacity
    )
    
    return {
        "clip_path": result_path,
        "clip_url": f"/outputs/{Path(result_path).name}"
    }


@app.post("/api/upload-youtube")
async def upload_to_youtube(req: UploadVideoRequest):
    """Upload a clip to YouTube."""
    from services.youtube_service import upload_video
    
    tags = [t.lstrip("#").strip() for t in req.hashtags.split() if t.startswith("#")]
    
    video_id = upload_video(
        video_path=req.video_path,
        title=req.title,
        description=req.description + "\n\n" + req.hashtags,
        tags=tags,
        thumbnail_path=req.thumbnail_path if req.thumbnail_path else None,
        privacy=req.privacy
    )
    
    return {
        "success": True,
        "video_id": video_id,
        "youtube_url": f"https://www.youtube.com/shorts/{video_id}"
    }


# ── YouTube OAuth ─────────────────────────────────────────────────────────────

@app.get("/api/youtube/auth-url")
async def get_youtube_auth_url(request: Request):
    from services.youtube_service import get_auth_url
    redirect_uri = str(request.base_url) + "auth/callback"
    redirect_uri = redirect_uri.rstrip("/")
    auth_url, state = get_auth_url(redirect_uri)
    return {"auth_url": auth_url, "state": state}


@app.get("/auth/callback")
async def youtube_oauth_callback(request: Request):
    from services.youtube_service import exchange_code_for_token
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    redirect_uri = str(request.base_url) + "auth/callback"
    redirect_uri = redirect_uri.rstrip("/")
    
    if not code:
        return HTMLResponse("<h2>❌ Authorization failed. No code received.</h2>")
    
    try:
        token_data = exchange_code_for_token(code, redirect_uri, state)
        return HTMLResponse("""
        <html><body style="font-family:Arial;text-align:center;padding:50px;background:#0f0f0f;color:white">
        <h2>✅ YouTube চ্যানেল সফলভাবে কানেক্ট হয়েছে!</h2>
        <p>এই ট্যাব বন্ধ করুন এবং টুলসে ফিরে যান।</p>
        <script>window.opener && window.opener.postMessage('yt_auth_success', '*'); setTimeout(()=>window.close(), 2000);</script>
        </body></html>
        """)
    except Exception as e:
        return HTMLResponse(f"<h2>❌ Error: {e}</h2>")


@app.get("/api/youtube/status")
async def youtube_status():
    from services.youtube_service import is_authenticated, get_channel_info
    if not is_authenticated():
        return {"connected": False}
    try:
        channel = get_channel_info()
        return {"connected": True, "channel": channel}
    except Exception as e:
        return {"connected": False, "error": str(e)}


@app.post("/api/youtube/logout")
async def youtube_logout():
    from services.youtube_service import logout
    logout()
    return {"success": True, "message": "YouTube চ্যানেল ডিসকানেক্ট হয়েছে।"}


# ── Schedule ──────────────────────────────────────────────────────────────────

@app.post("/api/schedule")
async def create_schedule(req: ScheduleRequest, db: Session = Depends(get_db)):
    from services.scheduler_service import add_upload_schedule
    
    schedule_id = generate_id()
    schedule = UploadSchedule(
        schedule_id=schedule_id,
        drive_folder_link=req.drive_folder_link,
        upload_times=json.dumps(req.upload_times),
        is_active=True
    )
    db.add(schedule)
    db.commit()
    
    add_upload_schedule(schedule_id, req.drive_folder_link, req.upload_times)
    
    return {
        "success": True,
        "schedule_id": schedule_id,
        "message": f"Schedule তৈরি হয়েছে! প্রতিদিন {', '.join(req.upload_times)} তে আপলোড হবে।"
    }


@app.get("/api/schedule")
async def get_schedules(db: Session = Depends(get_db)):
    from services.scheduler_service import get_all_jobs_info
    schedules = db.query(UploadSchedule).all()
    result = []
    for s in schedules:
        result.append({
            "schedule_id": s.schedule_id,
            "drive_folder_link": s.drive_folder_link,
            "upload_times": json.loads(s.upload_times) if isinstance(s.upload_times, str) else s.upload_times,
            "is_active": s.is_active,
            "last_upload": s.last_upload.isoformat() if s.last_upload else None
        })
    jobs_info = get_all_jobs_info()
    return {"schedules": result, "scheduler_jobs": jobs_info}


@app.delete("/api/schedule/{schedule_id}")
async def delete_schedule(schedule_id: str, db: Session = Depends(get_db)):
    from services.scheduler_service import remove_upload_schedule
    s = db.query(UploadSchedule).filter(UploadSchedule.schedule_id == schedule_id).first()
    if s:
        s.is_active = False
        db.commit()
    remove_upload_schedule(schedule_id)
    return {"success": True}


@app.get("/api/upload-logs")
async def get_upload_logs(db: Session = Depends(get_db)):
    logs = db.query(UploadLog).order_by(UploadLog.uploaded_at.desc()).limit(50).all()
    return [{"title": l.video_title, "video_id": l.youtube_video_id,
             "status": l.status, "uploaded_at": l.uploaded_at.isoformat()} for l in logs]


# ── Settings ──────────────────────────────────────────────────────────────────

@app.post("/api/settings")
async def save_settings(req: SaveSettingsRequest, db: Session = Depends(get_db)):
    fields = req.model_dump()
    for key, value in fields.items():
        if value:
            set_setting(db, key, value)
            os.environ[key.upper()] = value
    return {"success": True, "message": "Settings সেভ হয়েছে!"}


@app.get("/api/settings")
async def load_settings(db: Session = Depends(get_db)):
    keys = ["gemini_api_key", "grok_api_key", "youtube_client_id",
            "youtube_client_secret", "google_drive_api_key", "openai_api_key"]
    result = {}
    for key in keys:
        val = get_setting(db, key, "")
        if val and len(val) > 8:
            result[key] = val[:4] + "****" + val[-4:]  # Mask
        else:
            result[key] = "" if not val else val
    return result


@app.post("/api/settings/load-to-env")
async def load_settings_to_env(db: Session = Depends(get_db)):
    """Load saved API keys into environment."""
    _load_api_keys_from_db(db)
    return {"success": True}


def _load_api_keys_from_db(db):
    """Load API keys from DB into os.environ."""
    mapping = {
        "gemini_api_key": "GEMINI_API_KEY",
        "grok_api_key": "GROK_API_KEY",
        "youtube_client_id": "YOUTUBE_CLIENT_ID",
        "youtube_client_secret": "YOUTUBE_CLIENT_SECRET",
        "google_drive_api_key": "GOOGLE_DRIVE_API_KEY",
        "openai_api_key": "OPENAI_API_KEY"
    }
    for db_key, env_key in mapping.items():
        if not os.getenv(env_key):
            val = get_setting(db, db_key, "")
            if val:
                os.environ[env_key] = val


# ── File serving & download ───────────────────────────────────────────────────

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path, media_type="video/mp4", filename=filename)


@app.get("/api/jobs")
async def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(VideoJob).order_by(VideoJob.created_at.desc()).limit(20).all()
    return [{"job_id": j.job_id, "status": j.status, "progress": j.progress,
             "message": j.message, "url": j.source_url,
             "created_at": j.created_at.isoformat()} for j in jobs]


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
