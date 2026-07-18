"""Pipeline tự động tạo video hoạt hình và đăng lên YouTube."""
import asyncio
import json
import re
import time
import random

from loguru import logger


def _esc(text: str) -> str:
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


ANIMATION_TOPICS = [
    "hoạt hình thiên nhiên kỳ diệu",
    "cuộc phiêu lưu của những chú thỏ trong rừng phép thuật",
    "thành phố nước dưới đáy biển",
    "hành tinh xa xôi đầy màu sắc",
    "rồng nhỏ học bay lần đầu tiên",
    "khu rừng phát sáng ban đêm",
    "đảo nổi trên bầu trời",
    "vương quốc mây và các thiên thần nhỏ",
    "chú mèo phù thủy và cây đũa thần",
    "cuộc đua xe kẹo trong thế giới đồ ngọt",
    "cá voi bay qua bầu trời đêm đầy sao",
    "lâu đài băng tuyết và công chúa bướm",
    "rừng nấm phát sáng và những sinh vật kỳ diệu",
    "chuyến tàu hỏa xuyên qua các thế giới màu sắc",
    "thung lũng hoa nở theo điệu nhạc",
    "chú robot nhỏ và khu vườn diệu kỳ",
    "đại dương phát sáng và những sinh vật huyền bí",
    "thành phố mây nơi các thiên sứ sinh sống",
    "hành trình của hạt mưa từ biển lên trời",
    "vũ điệu của những vì sao trong đêm hội",
]

METADATA_PROMPT = """Bạn là chuyên gia YouTube tại Việt Nam. Chủ đề: {topic}

Trả về JSON hợp lệ (KHÔNG markdown):
{{
  "title": "...",
  "description": "...",
  "tags": ["tag1", "tag2"],
  "topic_display": "..."
}}

- title: Tiếng Việt, 50-80 ký tự, 1-2 emoji, viral
- description: 500-700 ký tự, hấp dẫn, hashtag cuối: #hoathinh #animation #viral #aivideo
- tags: 15-20 tag
- topic_display: tên chủ đề ngắn 5-10 từ"""


async def generate_metadata(topic: str) -> dict:
    from bot import gemini
    result = await gemini.chat(METADATA_PROMPT.format(topic=topic))
    text = result["text"].strip()
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"Gemini không trả về JSON hợp lệ")
    data = json.loads(m.group())
    for key in ("title", "description", "tags", "topic_display"):
        if key not in data:
            raise ValueError(f"JSON thiếu trường: {key}")
    return data


async def run_auto_video_pipeline(chat_id: int, update_msg) -> dict:
    from bot import gemini
    from bot.youtube_upload import upload_video

    start = time.time()

    def elapsed() -> str:
        s = int(time.time() - start)
        return f"{s // 60}p{s % 60}s" if s >= 60 else f"{s}s"

    # ── BƯỚC 1: Chọn chủ đề ─────────────────────────────────────
    topic = random.choice(ANIMATION_TOPICS)
    await update_msg(
        "🤖 *Auto Video — Đang chạy*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 *Bước 1/3 — Chọn chủ đề*\n"
        f"🎯 {_esc(topic)}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ {elapsed()}"
    )
    logger.info(f"[AutoVideo] Topic: {topic}")

    # ── BƯỚC 2: Tạo metadata YouTube ────────────────────────────
    await update_msg(
        "🤖 *Auto Video — Đang chạy*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Bước 1/3 — Chủ đề: {_esc(topic)}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 *Bước 2/3 — Tạo tiêu đề & mô tả YouTube*\n"
        "✍️ Đang nhờ Gemini viết tiêu đề, mô tả, tags\\.\\.\\.\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ {elapsed()}"
    )

    metadata = await generate_metadata(topic)
    title = metadata["title"]
    description = metadata["description"]
    tags = metadata.get("tags", [])
    topic_display = metadata.get("topic_display", topic)
    td = _esc(topic_display)
    ts = _esc(title[:50] + ("…" if len(title) > 50 else ""))

    logger.info(f"[AutoVideo] Metadata OK. Title: {title}")

    # ── BƯỚC 3: Nhờ Gemini tạo video ────────────────────────────
    await update_msg(
        "🤖 *Auto Video — Đang chạy*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Bước 1/3 — Chủ đề: {td}\n"
        f"✅ Bước 2/3 — Tiêu đề: {ts}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 *Bước 3/3 — Gemini tạo video*\n"
        f"🎬 Đang nhắn Gemini: *tạo video {_esc(topic)}*\n"
        "⏳ Đợi Gemini xử lý \\(1\\-3 phút\\)\\.\\.\\.\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ {elapsed()}"
    )

    from bot import gemini_playwright
    from bot.store import get_gemini_psid, get_gemini_psidts

    video_bytes = await gemini_playwright.generate_video(
        topic=topic,
        psid=get_gemini_psid(),
        psidts=get_gemini_psidts(),
        timeout=360,
    )

    if not video_bytes:
        raise RuntimeError(
            "Gemini không trả về video\\. Cookie có thể hết hạn — cập nhật qua /setcookie rồi thử lại\\."
        )

    size_kb = len(video_bytes) // 1024
    logger.info(f"[AutoVideo] Video từ Gemini Playwright: {size_kb} KB")

    await update_msg(
        "🤖 *Auto Video — Đang chạy*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Bước 1/3 — Chủ đề: {td}\n"
        f"✅ Bước 2/3 — Tiêu đề: {ts}\n"
        f"✅ Bước 3/3 — Video xong \\({size_kb} KB\\)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 Đang upload lên YouTube\\.\\.\\.\n"
        f"⏱ {elapsed()}"
    )

    youtube_url = await upload_video(
        video_bytes=video_bytes,
        title=title,
        description=description,
        tags=tags,
        chat_id=chat_id,
        category_id="1",
    )
    logger.info(f"[AutoVideo] Uploaded: {youtube_url}")

    return {
        "youtube_url": youtube_url,
        "title": title,
        "description": description,
        "tags": tags,
        "topic": topic_display,
        "elapsed": elapsed(),
    }
