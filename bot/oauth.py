"""Google OAuth 2.0 flow cho Telegram bot."""
import json
import os
from typing import Optional
from loguru import logger

from bot.config import (
    REDIRECT_URI,
    OAUTH_SCOPES,
)
from bot import store

# Lưu trạng thái OAuth: {state -> (chat_id, flow)}
_pending_states: dict[str, tuple] = {}

# Lưu thông tin user đã đăng nhập: {chat_id -> user_info}
_logged_in_users: dict[int, dict] = {}


def is_oauth_configured() -> bool:
    return bool(store.get_google_client_id() and store.get_google_client_secret())


def create_flow(state: str):
    """Tạo Google OAuth Flow."""
    from google_auth_oauthlib.flow import Flow

    client_config = {
        "web": {
            "client_id": store.get_google_client_id(),
            "client_secret": store.get_google_client_secret(),
            "redirect_uris": [REDIRECT_URI],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=OAUTH_SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    return flow


def get_auth_url(chat_id: int) -> tuple[str, str]:
    """
    Tạo URL ủy quyền Google OAuth.
    Trả về (auth_url, state).
    """
    import secrets
    state = secrets.token_urlsafe(16)

    flow = create_flow(state)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        state=state,
        prompt="consent",          # buộc Google trả về refresh_token mỗi lần
        include_granted_scopes="true",
    )
    # Lưu cả flow để giữ code_verifier (PKCE)
    _pending_states[state] = (chat_id, flow)
    return auth_url, state


async def handle_callback(code: str, state: str) -> Optional[dict]:
    """
    Xử lý OAuth callback. Trả về user_info nếu thành công.
    """
    if state not in _pending_states:
        logger.warning(f"Unknown OAuth state: {state}")
        return None

    chat_id, flow = _pending_states.pop(state)

    try:
        flow.fetch_token(code=code)
        credentials = flow.credentials

        import requests as req

        headers = {"Authorization": f"Bearer {credentials.token}"}

        # Lấy thông tin Google account
        user_info = req.get(
            "https://www.googleapis.com/oauth2/v2/userinfo", headers=headers
        ).json()
        user_info["chat_id"] = chat_id

        # Lấy thông tin kênh YouTube
        yt_resp = req.get(
            "https://www.googleapis.com/youtube/v3/channels",
            headers=headers,
            params={"part": "snippet,statistics", "mine": "true"},
        )
        yt_data = yt_resp.json()
        channel_info = None
        if yt_data.get("items"):
            ch = yt_data["items"][0]
            subs = int(ch["statistics"].get("subscriberCount", 0))
            views = int(ch["statistics"].get("viewCount", 0))
            videos = int(ch["statistics"].get("videoCount", 0))
            channel_info = {
                "title": ch["snippet"]["title"],
                "subscribers": subs,
                "views": views,
                "videos": videos,
                "url": f"https://youtube.com/channel/{ch['id']}",
            }
        user_info["channel"] = channel_info

        _logged_in_users[chat_id] = user_info

        # Lưu credentials để dùng upload video sau này
        from bot import store as _store
        _store.set_google_credentials(chat_id, {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri or "https://oauth2.googleapis.com/token",
            "client_id": credentials.client_id or _store.get_google_client_id(),
            "client_secret": credentials.client_secret or _store.get_google_client_secret(),
            "scopes": list(credentials.scopes or OAUTH_SCOPES),
        })

        logger.info(f"User {chat_id} logged in as {user_info.get('email')}, channel: {channel_info}")
        return {"chat_id": chat_id, "user_info": user_info}

    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return None


def get_user_info(chat_id: int) -> Optional[dict]:
    # Kiểm tra bộ nhớ trước
    if chat_id in _logged_in_users:
        return _logged_in_users[chat_id]

    # Bot vừa restart → thử phục hồi từ credentials đã lưu
    from bot import store as _store
    creds = _store.get_google_credentials(chat_id)
    if not creds:
        return None

    # Credentials tồn tại → thử lấy lại user info từ Google
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleRequest

        google_creds = Credentials(
            token=creds.get("token"),
            refresh_token=creds.get("refresh_token"),
            token_uri=creds.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=_store.get_google_client_id(),
            client_secret=_store.get_google_client_secret(),
            scopes=creds.get("scopes"),
        )
        # Refresh nếu token hết hạn
        if not google_creds.valid and google_creds.refresh_token:
            google_creds.refresh(GoogleRequest())
            # Cập nhật token mới vào store
            _store.set_google_credentials(chat_id, {
                "token": google_creds.token,
                "refresh_token": google_creds.refresh_token,
                "token_uri": google_creds.token_uri,
                "scopes": list(google_creds.scopes or OAUTH_SCOPES),
            })

        import requests as req
        headers = {"Authorization": f"Bearer {google_creds.token}"}

        user_info = req.get(
            "https://www.googleapis.com/oauth2/v2/userinfo", headers=headers, timeout=10
        ).json()
        user_info["chat_id"] = chat_id

        yt_resp = req.get(
            "https://www.googleapis.com/youtube/v3/channels",
            headers=headers,
            params={"part": "snippet,statistics", "mine": "true"},
            timeout=10,
        )
        yt_data = yt_resp.json()
        channel_info = None
        if yt_data.get("items"):
            ch = yt_data["items"][0]
            subs = int(ch["statistics"].get("subscriberCount", 0))
            views = int(ch["statistics"].get("viewCount", 0))
            videos = int(ch["statistics"].get("videoCount", 0))
            channel_info = {
                "title": ch["snippet"]["title"],
                "subscribers": subs,
                "views": views,
                "videos": videos,
                "url": f"https://youtube.com/channel/{ch['id']}",
            }
        user_info["channel"] = channel_info
        _logged_in_users[chat_id] = user_info
        logger.info(f"Restored session for chat {chat_id} ({user_info.get('email')})")
        return user_info

    except Exception as e:
        logger.warning(f"Could not restore session for chat {chat_id}: {e}")
        # Trả về stub tối thiểu để không bắt đăng nhập lại
        stub = {"chat_id": chat_id, "email": "restored", "channel": None}
        _logged_in_users[chat_id] = stub
        return stub


def logout(chat_id: int):
    _logged_in_users.pop(chat_id, None)
