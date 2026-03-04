import os
import logging
import subprocess
from pathlib import Path
from utils.helpers import TEMP_DIR, sanitize_filename

logger = logging.getLogger(__name__)


def download_video(url: str, job_id: str, progress_callback=None) -> dict:
    """
    Download YouTube video in best quality using yt-dlp.
    Returns dict with: path, title, duration, thumbnail_url, heatmap_data
    """
    output_template = str(TEMP_DIR / f"{job_id}_%(title)s.%(ext)s")
    info_path = str(TEMP_DIR / f"{job_id}_info.json")

    cmd_info = [
        "yt-dlp",
        "--dump-json",
        "--no-playlist",
        url
    ]

    logger.info(f"[{job_id}] Fetching video info: {url}")
    result = subprocess.run(cmd_info, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"yt-dlp info error: {result.stderr}")

    import json
    info = json.loads(result.stdout)
    title = info.get("title", "video")
    duration = float(info.get("duration", 0))
    thumbnail = info.get("thumbnail", "")

    # Extract heatmap (most-replayed data) if available
    heatmap_data = []
    heatmap_raw = info.get("heatmap", [])
    if heatmap_raw:
        for point in heatmap_raw:
            heatmap_data.append({
                "start": float(point.get("start_time", 0)),
                "end": float(point.get("end_time", 0)),
                "value": float(point.get("value", 0))
            })

    safe_title = sanitize_filename(title)
    output_template = str(TEMP_DIR / f"{job_id}_{safe_title}.%(ext)s")

    # Download best video+audio merged
    cmd_dl = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        "--no-playlist",
        "--newline",
        url
    ]

    logger.info(f"[{job_id}] Downloading video ...")
    proc = subprocess.Popen(cmd_dl, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        line = line.strip()
        if progress_callback and "[download]" in line and "%" in line:
            try:
                pct = float(line.split("%")[0].split()[-1])
                progress_callback(pct * 0.5)  # download = 0-50%
            except Exception:
                pass
    proc.wait()
    if proc.returncode != 0:
        raise Exception("yt-dlp download failed.")

    # Find downloaded file
    downloaded = None
    for f in TEMP_DIR.iterdir():
        if f.name.startswith(job_id) and f.suffix == ".mp4":
            downloaded = f
            break

    if not downloaded:
        # Check for other extensions
        for f in TEMP_DIR.iterdir():
            if f.name.startswith(job_id) and f.suffix in [".mkv", ".webm", ".avi"]:
                downloaded = f
                break

    if not downloaded:
        raise Exception("Downloaded file not found in temp dir.")

    logger.info(f"[{job_id}] Downloaded: {downloaded}")
    return {
        "path": str(downloaded),
        "title": title,
        "duration": duration,
        "thumbnail_url": thumbnail,
        "heatmap": heatmap_data
    }
