"""
Lưu trữ credentials runtime vào file JSON.
Ưu tiên: file store → environment variables.
"""
import json
import os
from pathlib import Path

_STORE_PATH = Path(__file__).parent.parent / ".credentials_store.json"


def _load() -> dict:
    try:
        if _STORE_PATH.exists():
            return json.loads(_STORE_PATH.read_text())
    except Exception:
        pass
    return {}


def _save(data: dict):
    _STORE_PATH.write_text(json.dumps(data, indent=2))


def get(key: str, default: str = "") -> str:
    return _load().get(key) or os.environ.get(key, default)


def set_value(key: str, value: str):
    data = _load()
    data[key] = value
    _save(data)


def delete(key: str):
    data = _load()
    data.pop(key, None)
    _save(data)


# ── Gemini cookies ──────────────────────────────────────────────

def get_gemini_psid() -> str:
    return get("GEMINI_PSID")


def get_gemini_psidts() -> str:
    return get("GEMINI_PSIDTS")


def set_gemini_cookies(psid: str, psidts: str = ""):
    set_value("GEMINI_PSID", psid)
    if psidts:
        set_value("GEMINI_PSIDTS", psidts)


def get_gemini_cookie_string() -> str:
    """Trả về full cookie string (key=value; ...) cho Playwright nếu có."""
    return get("GEMINI_COOKIE_STRING", "")


def set_gemini_cookie_string(cookie_str: str):
    """Lưu full cookie string từ browser (copy từ DevTools)."""
    set_value("GEMINI_COOKIE_STRING", cookie_str)


# ── YouTube cookies (Netscape format string) ───────────────────

def get_youtube_cookies() -> str:
    """Trả về nội dung file cookies YouTube dạng Netscape (cho yt-dlp)."""
    return get("YOUTUBE_COOKIES", "")


def set_youtube_cookies(cookies_text: str):
    set_value("YOUTUBE_COOKIES", cookies_text)


def get_youtube_cookies_path() -> str | None:
    """Ghi cookies ra file tạm, trả về đường dẫn (cho yt-dlp)."""
    text = get_youtube_cookies()
    if not text:
        return None
    path = Path("/tmp/yt_cookies.txt")
    path.write_text(text)
    return str(path)


# ── Google OAuth credentials ────────────────────────────────────

def get_google_client_id() -> str:
    return get("GOOGLE_CLIENT_ID")


def get_google_client_secret() -> str:
    return get("GOOGLE_CLIENT_SECRET")


def set_google_client_id(value: str):
    set_value("GOOGLE_CLIENT_ID", value)


def set_google_client_secret(value: str):
    set_value("GOOGLE_CLIENT_SECRET", value)


# ── Google OAuth credentials (per user) ────────────────────────

def get_google_credentials(chat_id: int) -> dict | None:
    """Lấy OAuth credentials đã lưu cho chat_id."""
    data = _load()
    return data.get(f"google_creds_{chat_id}")


def set_google_credentials(chat_id: int, creds: dict):
    """Lưu OAuth credentials (token, refresh_token, ...) cho chat_id."""
    set_value(f"google_creds_{chat_id}", creds)


def delete_google_credentials(chat_id: int):
    delete(f"google_creds_{chat_id}")


# ── Admin ───────────────────────────────────────────────────────

def get_admin_id() -> int | None:
    raw = get("ADMIN_CHAT_ID")
    return int(raw) if raw else None


def set_admin_id(chat_id: int):
    set_value("ADMIN_CHAT_ID", str(chat_id))
