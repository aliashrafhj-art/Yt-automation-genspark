import os
import json
import logging
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]
TOKEN_FILE = "./yt_token.json"
CLIENT_SECRETS = "./client_secrets.json"

def get_auth_url(redirect_uri: str) -> str:
    """Generate OAuth2 authorization URL."""
    client_config = _get_client_config()
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )
    return auth_url, state


def exchange_code_for_token(code: str, redirect_uri: str, state: str = None) -> dict:
    """Exchange authorization code for access token."""
    client_config = _get_client_config()
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES)
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)
    return token_data


def get_credentials() -> Credentials:
    """Load and refresh credentials."""
    if not os.path.exists(TOKEN_FILE):
        raise Exception("Not authenticated. Please connect YouTube channel first.")
    with open(TOKEN_FILE, "r") as f:
        data = json.load(f)
    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes", SCOPES)
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        data["token"] = creds.token
        with open(TOKEN_FILE, "w") as f:
            json.dump(data, f)
    return creds


def get_channel_info() -> dict:
    """Get authenticated channel info."""
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    response = youtube.channels().list(part="snippet,statistics", mine=True).execute()
    items = response.get("items", [])
    if not items:
        return {}
    ch = items[0]
    return {
        "id": ch["id"],
        "title": ch["snippet"]["title"],
        "thumbnail": ch["snippet"]["thumbnails"].get("default", {}).get("url", ""),
        "subscribers": ch["statistics"].get("subscriberCount", "0"),
        "video_count": ch["statistics"].get("videoCount", "0")
    }


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list,
    thumbnail_path: str = None,
    privacy: str = "public",
    category_id: str = "22"
) -> str:
    """Upload video to YouTube. Returns video ID."""
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:30],
            "categoryId": category_id
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        video_path,
        chunksize=10 * 1024 * 1024,
        resumable=True,
        mimetype="video/mp4"
    )

    logger.info(f"Uploading video: {title}")
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            logger.info(f"Upload progress: {progress}%")

    video_id = response.get("id", "")
    logger.info(f"Uploaded! Video ID: {video_id}")

    # Set thumbnail if provided
    if thumbnail_path and os.path.exists(thumbnail_path) and video_id:
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
            ).execute()
            logger.info(f"Thumbnail set for {video_id}")
        except Exception as e:
            logger.warning(f"Thumbnail set failed: {e}")

    return video_id


def logout():
    """Remove stored credentials."""
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    return True


def is_authenticated() -> bool:
    """Check if YouTube is connected."""
    try:
        get_credentials()
        return True
    except Exception:
        return False


def _get_client_config() -> dict:
    """Load OAuth2 client config from env or file."""
    client_id = os.getenv("YOUTUBE_CLIENT_ID", "")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "")
    
    if client_id and client_secret:
        return {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:8000/auth/callback"]
            }
        }
    elif os.path.exists(CLIENT_SECRETS):
        with open(CLIENT_SECRETS) as f:
            return json.load(f)
    else:
        raise Exception("YouTube OAuth credentials not configured. Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET.")
