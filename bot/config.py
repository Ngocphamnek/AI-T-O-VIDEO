"""Cấu hình bot từ environment variables."""
import os

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_PSID = os.environ.get("GEMINI_PSID", "")
GEMINI_PSIDTS = os.environ.get("GEMINI_PSIDTS", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

REPLIT_DEV_DOMAIN = os.environ.get("REPLIT_DEV_DOMAIN", "localhost")
PORT = int(os.environ.get("PORT", 3000))
BASE_URL = f"https://{REPLIT_DEV_DOMAIN}"
REDIRECT_URI = f"{BASE_URL}/api/auth/callback"

# Google OAuth scopes
OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
]

# YouTube URL patterns
YT_PATTERNS = ["youtube.com/watch", "youtu.be/", "youtube.com/shorts/", "youtube.com/live/"]
