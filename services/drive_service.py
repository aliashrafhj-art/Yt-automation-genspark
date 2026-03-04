import os
import io
import zipfile
import random
import logging
import requests
import re
from pathlib import Path
from utils.helpers import TEMP_DIR

logger = logging.getLogger(__name__)


def parse_drive_folder_id(link: str) -> str:
    """Extract folder ID from Google Drive share link."""
    patterns = [
        r"/folders/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
        r"^([a-zA-Z0-9_-]{25,})$"
    ]
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return match.group(1)
    raise Exception(f"Could not parse Google Drive folder ID from: {link}")


def list_drive_folder_files(folder_id: str) -> list:
    """List files in a public Google Drive folder using Drive API or public URL."""
    api_key = os.getenv("GOOGLE_DRIVE_API_KEY", "")
    
    if api_key:
        url = f"https://www.googleapis.com/drive/v3/files"
        params = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "key": api_key,
            "fields": "files(id,name,mimeType,size)"
        }
        resp = requests.get(url, params=params)
        if resp.status_code == 200:
            return resp.json().get("files", [])
    
    # Fallback: Use public folder export
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    resp = requests.get(url, timeout=15)
    
    # Parse file IDs from HTML (basic scraping)
    files = []
    matches = re.findall(r'"([a-zA-Z0-9_-]{25,})".*?"([^"]+\.(mp4|zip|mkv|avi))"', resp.text)
    for file_id, name, ext in matches:
        files.append({"id": file_id, "name": name, "mimeType": f"video/{ext}"})
    
    return files


def download_drive_file(file_id: str, filename: str) -> str:
    """Download a file from Google Drive by file ID."""
    output_path = str(TEMP_DIR / filename)
    
    # Direct download URL
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    
    session = requests.Session()
    response = session.get(download_url, stream=True, timeout=60)
    
    # Handle confirmation for large files
    for key, value in response.cookies.items():
        if key.startswith("download_warning"):
            download_url = f"{download_url}&confirm={value}"
            response = session.get(download_url, stream=True, timeout=60)
            break
    
    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=32768):
            f.write(chunk)
    
    logger.info(f"Downloaded from Drive: {output_path}")
    return output_path


def get_random_video_from_zip(folder_link: str, job_id: str) -> str:
    """
    Download ZIP from Google Drive folder, extract random video file.
    Returns path to extracted video.
    """
    try:
        folder_id = parse_drive_folder_id(folder_link)
    except Exception as e:
        raise Exception(f"Invalid Google Drive link: {e}")
    
    # List files in folder
    files = list_drive_folder_files(folder_id)
    
    # Find ZIP files
    zip_files = [f for f in files if f["name"].endswith(".zip") or f.get("mimeType") == "application/zip"]
    
    if not zip_files:
        # Try direct video files
        video_files = [f for f in files if any(f["name"].endswith(ext) for ext in [".mp4", ".mkv", ".avi", ".mov"])]
        if not video_files:
            raise Exception("No ZIP or video files found in Google Drive folder.")
        
        chosen = random.choice(video_files)
        return download_drive_file(chosen["id"], f"{job_id}_{chosen['name']}")
    
    # Download random ZIP
    chosen_zip = random.choice(zip_files)
    zip_path = download_drive_file(chosen_zip["id"], f"{job_id}_{chosen_zip['name']}")
    
    # Extract random video from ZIP
    with zipfile.ZipFile(zip_path, "r") as zf:
        video_files = [name for name in zf.namelist() 
                      if any(name.endswith(ext) for ext in [".mp4", ".mkv", ".avi", ".mov"])]
        
        if not video_files:
            raise Exception("No video files found in ZIP archive.")
        
        chosen_video = random.choice(video_files)
        extract_path = str(TEMP_DIR / f"{job_id}_{Path(chosen_video).name}")
        
        with zf.open(chosen_video) as source, open(extract_path, "wb") as target:
            target.write(source.read())
        
        logger.info(f"Extracted video from ZIP: {extract_path}")
    
    # Clean up ZIP
    try:
        os.remove(zip_path)
    except Exception:
        pass
    
    return extract_path


def download_drive_zip_direct(zip_url: str, job_id: str) -> str:
    """Download a ZIP file directly from a Google Drive share link."""
    # Extract file ID from share link
    file_id_match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", zip_url)
    if not file_id_match:
        file_id_match = re.search(r"id=([a-zA-Z0-9_-]+)", zip_url)
    
    if not file_id_match:
        raise Exception(f"Cannot parse file ID from: {zip_url}")
    
    file_id = file_id_match.group(1)
    return download_drive_file(file_id, f"{job_id}_drive.zip")
