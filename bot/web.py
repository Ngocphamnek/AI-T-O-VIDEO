"""FastAPI web server để xử lý OAuth callback."""
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from bot import oauth
from bot.config import REDIRECT_URI

app = FastAPI(title="Gemini Bot - OAuth Server")

# Lưu tham chiếu đến Telegram app để gửi message sau OAuth
_telegram_app = None


def set_telegram_app(telegram_app):
    global _telegram_app
    _telegram_app = telegram_app


@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <html><body style="font-family:sans-serif;text-align:center;padding:50px">
    <h2>🤖 Gemini Telegram Bot</h2>
    <p>Bot đang hoạt động.</p>
    <p>Mở Telegram và chat với bot để sử dụng.</p>
    </body></html>
    """


@app.get("/auth/callback", response_class=HTMLResponse)
async def oauth_callback(request: Request):
    """Xử lý Google OAuth callback."""
    params = dict(request.query_params)
    code = params.get("code")
    state = params.get("state")
    error = params.get("error")

    if error:
        logger.warning(f"OAuth error: {error}")
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;text-align:center;padding:50px">
        <h2>❌ Lỗi xác thực</h2>
        <p>{error}</p>
        <p>Vui lòng thử lại trong Telegram.</p>
        </body></html>
        """)

    if not code or not state:
        return HTMLResponse("""
        <html><body style="font-family:sans-serif;text-align:center;padding:50px">
        <h2>⚠️ Thiếu thông tin xác thực</h2>
        <p>Yêu cầu không hợp lệ.</p>
        </body></html>
        """)

    result = await oauth.handle_callback(code, state)
    if not result:
        return HTMLResponse("""
        <html><body style="font-family:sans-serif;text-align:center;padding:50px">
        <h2>❌ Xác thực thất bại</h2>
        <p>Phiên đã hết hạn hoặc không hợp lệ. Thử lại từ Telegram.</p>
        </body></html>
        """)

    chat_id = result["chat_id"]
    user_info = result["user_info"]
    email = user_info.get("email", "Unknown")
    name = user_info.get("name", "")
    channel = user_info.get("channel")

    # Tạo nội dung thông báo
    def fmt_num(n: int) -> str:
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}K"
        return str(n)

    def esc(text: str) -> str:
        """Escape MarkdownV2 special characters."""
        for ch in r"\_*[]()~`>#+-=|{}.!":
            text = text.replace(ch, f"\\{ch}")
        return text

    lines = [
        "✅ *Đăng nhập YouTube thành công\\!*\n",
        f"👤 *{esc(name)}*",
        f"📧 {esc(email)}",
    ]
    if channel:
        lines += [
            f"\n📺 *Kênh YouTube:* {esc(channel['title'])}",
            f"👥 Subscribers: *{fmt_num(channel['subscribers'])}*",
            f"▶️ Tổng views: *{fmt_num(channel['views'])}*",
            f"🎬 Số video: *{channel['videos']}*",
        ]
    else:
        lines.append("\n⚠️ Không tìm thấy kênh YouTube nào với tài khoản này\\.")
    lines.append("\n_Bạn có thể đóng trình duyệt và quay lại Telegram\\._")

    tg_text = "\n".join(lines)

    if _telegram_app:
        try:
            await _telegram_app.bot.send_message(
                chat_id=chat_id,
                text=tg_text,
                parse_mode="MarkdownV2",
            )
        except Exception as e:
            logger.error(f"Failed to send login success message: {e}")

    # HTML response cho trình duyệt
    ch_html = ""
    if channel:
        ch_html = f"""
        <hr style="margin:20px 0;border:none;border-top:1px solid #ccc">
        <p style="font-size:18px">📺 <strong>{channel['title']}</strong></p>
        <p>👥 {fmt_num(channel['subscribers'])} subscribers &nbsp;|&nbsp;
           ▶️ {fmt_num(channel['views'])} views &nbsp;|&nbsp;
           🎬 {channel['videos']} videos</p>
        """
    return HTMLResponse(f"""
    <html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#f0f9f0">
    <h2>✅ Đăng nhập thành công!</h2>
    <p style="font-size:18px">👤 <strong>{name}</strong><br>
    <span style="color:#666;font-size:14px">{email}</span></p>
    {ch_html}
    <p style="color:#888;margin-top:20px">Quay lại Telegram để tiếp tục sử dụng bot.</p>
    <script>setTimeout(()=>window.close(),4000)</script>
    </body></html>
    """)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "gemini-telegram-bot"}
