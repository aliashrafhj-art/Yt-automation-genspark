import os
import logging
import subprocess
import json
from pathlib import Path
from utils.helpers import TEMP_DIR

logger = logging.getLogger(__name__)


def extract_audio(video_path: str, job_id: str) -> str:
    """Extract audio from video as MP3 for Whisper."""
    audio_path = str(TEMP_DIR / f"{job_id}_audio.mp3")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"ffmpeg audio extract error: {result.stderr}")
    return audio_path


def transcribe_audio(audio_path: str, model_size: str = "base") -> list:
    """
    Transcribe audio using OpenAI Whisper locally.
    Returns list of segments: [{start, end, text}]
    """
    try:
        import whisper
        logger.info(f"Loading Whisper model: {model_size}")
        model = whisper.load_model(model_size)
        logger.info(f"Transcribing: {audio_path}")
        result = model.transcribe(audio_path, task="transcribe", verbose=False)
        segments = []
        for seg in result.get("segments", []):
            segments.append({
                "start": float(seg["start"]),
                "end": float(seg["end"]),
                "text": seg["text"].strip()
            })
        return segments
    except ImportError:
        logger.warning("Whisper not installed, trying OpenAI API fallback.")
        return transcribe_via_openai_api(audio_path)
    except Exception as e:
        logger.error(f"Whisper error: {e}")
        raise


def transcribe_via_openai_api(audio_path: str) -> list:
    """Fallback: Use OpenAI Whisper API if local Whisper not available."""
    import openai
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise Exception("No OpenAI API key for Whisper fallback.")
    client = openai.OpenAI(api_key=api_key)
    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )
    segments = []
    for seg in (response.segments or []):
        segments.append({
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text.strip()
        })
    return segments


def format_transcript_for_ai(segments: list) -> str:
    """Format transcript segments for AI analysis."""
    lines = []
    for seg in segments:
        start = seg["start"]
        end = seg["end"]
        text = seg["text"]
        h_s, m_s, s_s = int(start//3600), int((start%3600)//60), int(start%60)
        h_e, m_e, s_e = int(end//3600), int((end%3600)//60), int(end%60)
        lines.append(f"[{h_s:02d}:{m_s:02d}:{s_s:02d} - {h_e:02d}:{m_e:02d}:{s_e:02d}] {text}")
    return "\n".join(lines)
