"""Upload video lên YouTube Data API v3."""
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger


def _build_youtube(chat_id: int):
    """Tạo YouTube API client từ credentials đã lưu."""
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from bot import store

    creds_data = store.get_google_credentials(chat_id)
    if not creds_data:
        raise RuntimeError("Chưa đăng nhập YouTube — dùng /login để xác thực Google trước.")

    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=store.get_google_client_id(),
        client_secret=store.get_google_client_secret(),
        scopes=creds_data.get("scopes"),
    )
    return build("youtube", "v3", credentials=creds)


async def upload_video(
    video_bytes: bytes,
    title: str,
    description: str,
    tags: list[str],
    chat_id: int,
    category_id: str = "1",  # Film & Animation
) -> str:
    """
    Upload video MP4 lên YouTube (công khai).
    Trả về URL video dạng https://youtube.com/watch?v=VIDEO_ID
    """
    import asyncio

    youtube = _build_youtube(chat_id)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(video_bytes)
        tmp_path = f.name

    try:
        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:30],
                "categoryId": category_id,
                "defaultLanguage": "vi",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(tmp_path, mimetype="video/mp4", resumable=True, chunksize=5 * 1024 * 1024)
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        # Chạy upload trong thread để không block event loop
        def _do_upload():
            response = None
            while response is None:
                _, response = request.next_chunk()
            return response

        response = await asyncio.get_event_loop().run_in_executor(None, _do_upload)
        video_id = response["id"]
        logger.info(f"Uploaded video {video_id} for chat {chat_id}")
        return f"https://youtube.com/watch?v={video_id}"

    finally:
        Path(tmp_path).unlink(missing_ok=True)
