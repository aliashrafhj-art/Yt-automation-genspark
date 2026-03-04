import os
import re
import uuid
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

TEMP_DIR = Path(os.getenv("TEMP_DIR", "./temp"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs"))

TEMP_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def generate_id():
    return str(uuid.uuid4())[:12]


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)[:100]


def seconds_to_hhmmss(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def hhmmss_to_seconds(ts: str) -> float:
    """Parse HH:MM:SS or MM:SS or plain seconds string."""
    parts = ts.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    else:
        return float(parts[0])


def get_temp_path(filename: str) -> Path:
    return TEMP_DIR / filename


def get_output_path(filename: str) -> Path:
    return OUTPUT_DIR / filename


def cleanup_temp(job_id: str):
    """Remove temp files for a given job_id."""
    for f in TEMP_DIR.iterdir():
        if job_id in f.name:
            try:
                f.unlink()
                logger.info(f"Cleaned up temp file: {f}")
            except Exception as e:
                logger.warning(f"Could not remove {f}: {e}")
