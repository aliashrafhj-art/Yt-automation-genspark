import os
import subprocess
import logging
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import textwrap
from utils.helpers import OUTPUT_DIR, TEMP_DIR, sanitize_filename

logger = logging.getLogger(__name__)


def crop_video_segment(
    input_path: str,
    start: float,
    end: float,
    output_name: str,
    vertical_crop: bool = True
) -> str:
    """
    Cut and optionally crop video to 9:16 (vertical) for Shorts.
    Uses FFmpeg for speed.
    """
    output_path = str(OUTPUT_DIR / f"{sanitize_filename(output_name)}.mp4")
    duration = end - start

    # Build FFmpeg filter for 9:16 crop
    if vertical_crop:
        # Crop to 9:16 center crop
        vf = "scale=iw*min(1080/iw\\,1920/ih):ih*min(1080/iw\\,1920/ih)," \
             "pad=1080:1920:(1080-iw)/2:(1920-ih)/2:black," \
             "setsar=1"
    else:
        vf = "scale=1080:1920:force_original_aspect_ratio=decrease," \
             "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
    ]

    logger.info(f"Cropping: {start}s - {end}s -> {output_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"FFmpeg crop error: {result.stderr[-500:]}")
    
    return output_path


def add_text_overlay(
    input_path: str,
    text: str,
    output_name: str,
    position: str = "center",   # top, center, bottom
    font_size: int = 60,
    text_color: str = "white",
    bg_color: str = "black",
    bg_opacity: float = 0.6
) -> str:
    """
    Add text overlay with semi-transparent background to video using FFmpeg.
    """
    output_path = str(OUTPUT_DIR / f"{sanitize_filename(output_name)}_text.mp4")

    # Position mapping
    y_positions = {
        "top": "50",
        "center": "(h-text_h)/2",
        "bottom": "h-text_h-80"
    }
    y_pos = y_positions.get(position, "(h-text_h)/2")

    # Escape special chars for FFmpeg drawtext
    safe_text = text.replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")[:100]
    # Wrap long text
    words = safe_text.split()
    lines = []
    line = ""
    for word in words:
        if len(line) + len(word) > 22:
            lines.append(line.strip())
            line = word + " "
        else:
            line += word + " "
    if line:
        lines.append(line.strip())
    
    # Use boxed drawtext for each line
    filters = []
    line_height = font_size + 10
    total_height = len(lines) * line_height
    
    for i, line_text in enumerate(lines):
        offset = f"{y_pos}" if i == 0 else f"{y_pos}+{i * line_height}"
        if position == "top":
            offset = f"50+{i * line_height}"
        elif position == "bottom":
            offset = f"h-text_h-80-{(len(lines)-1-i) * line_height}"
        else:
            offset = f"(h-{total_height})/2+{i * line_height}"
        
        esc_line = line_text.replace("'", "").replace(":", " ").replace("%", "pct")
        
        filters.append(
            f"drawtext=text='{esc_line}'"
            f":fontsize={font_size}"
            f":fontcolor={text_color}"
            f":x=(w-text_w)/2"
            f":y={offset}"
            f":box=1"
            f":boxcolor={bg_color}@{bg_opacity}"
            f":boxborderw=10"
        )
    
    vf = ",".join(filters) if filters else f"drawtext=text='':fontsize=1"

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path
    ]

    logger.info(f"Adding text overlay: '{text[:50]}...' -> {output_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(f"FFmpeg drawtext failed, returning original. Error: {result.stderr[-300:]}")
        return input_path
    
    return output_path


def extract_thumbnail(video_path: str, timestamp: float, output_name: str) -> str:
    """Extract a thumbnail frame from video at given timestamp."""
    thumb_path = str(OUTPUT_DIR / f"{sanitize_filename(output_name)}_thumb.jpg")
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
               "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black",
        thumb_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Thumbnail extraction error: {result.stderr[-200:]}")
    return thumb_path


def get_video_info(video_path: str) -> dict:
    """Get video duration and resolution via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        video_path
    ]
    import json
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {"duration": 0, "width": 0, "height": 0}
    data = json.loads(result.stdout)
    
    duration = float(data.get("format", {}).get("duration", 0))
    width, height = 0, 0
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = stream.get("width", 0)
            height = stream.get("height", 0)
            break
    
    return {"duration": duration, "width": width, "height": height}
