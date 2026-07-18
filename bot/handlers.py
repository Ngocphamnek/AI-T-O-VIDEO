"""Telegram bot handlers: commands và messages."""
import io
import re
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from bot.config import REDIRECT_URI, YT_PATTERNS
from bot import gemini, oauth, store, auto_video

# Lưu conversation sessions: {chat_id -> {conversation_id, reply_id, rc_id}}
_sessions: dict[int, dict] = {}

HELP_TEXT = """
🤖 *Gemini AI Bot* — Powered by Google Gemini

*Lệnh cơ bản:*
/start — Bắt đầu \& hiển thị menu
/help — Trợ giúp chi tiết
/reset — Reset phiên chat

*Tạo nội dung:*
/image \<mô tả\> — Tạo ảnh AI
/video \<mô tả\> — Tạo video AI \(Veo\)
/ask \<câu hỏi\> — Hỏi nhanh \(không lưu lịch sử\)
/youtube \<url\> — Phân tích video YouTube

*Cài đặt tài khoản:*
/setcookie — Cập nhật Gemini cookies \(PSID/PSIDTS\)
/setyoutube — Nhập YouTube cookies \(video riêng tư\)
/login — Đăng nhập Google OAuth
/logout — Đăng xuất Google

*Chat:*
Nhắn tin bình thường → Chat với Gemini \(nhớ lịch sử\)
Gửi link YouTube → Tự động phân tích video
Gửi ảnh \+ chú thích → Gemini phân tích ảnh

*Mẹo:*
• /reset để bắt đầu chủ đề mới
• Video mất 1\-2 phút để tạo, hãy chờ nhé\!
"""

WELCOME_TEXT = """
👋 Xin chào\! Tôi là *Gemini AI Bot*\.

Tôi có thể:
🗣 Chat thông minh với Gemini AI
🎨 Tạo và chỉnh sửa ảnh
🎬 Tạo video AI bằng Veo
▶️ Phân tích video YouTube
🤖 Tự động tạo & đăng video hoạt hình YouTube
🔐 Đăng nhập Google OAuth

Chọn một tính năng hoặc nhắn tin cho tôi\!
"""


def get_main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗣 Chat", callback_data="menu_chat"),
            InlineKeyboardButton("🎨 Tạo ảnh", callback_data="menu_image"),
        ],
        [
            InlineKeyboardButton("🎬 Tạo video", callback_data="menu_video"),
            InlineKeyboardButton("▶️ YouTube", callback_data="menu_youtube"),
        ],
        [
            InlineKeyboardButton("🤖 Auto Video Hoạt Hình", callback_data="menu_auto_video"),
        ],
        [
            InlineKeyboardButton("🔐 Đăng nhập", callback_data="menu_login"),
            InlineKeyboardButton("❓ Trợ giúp", callback_data="menu_help"),
        ],
        [
            InlineKeyboardButton("🔄 Reset chat", callback_data="menu_reset"),
        ],
    ])


def get_login_keyboard():
    """Menu chọn loại đăng nhập."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📺 YouTube (Google)", callback_data="login_youtube_oauth"),
            InlineKeyboardButton("🤖 Gemini Cookies", callback_data="login_gemini"),
        ],
        [InlineKeyboardButton("❌ Huỷ", callback_data="login_cancel")],
    ])


def get_youtube_oauth_keyboard():
    """Menu nhập Google OAuth credentials cho YouTube."""
    client_id_set = "✅" if store.get_google_client_id() else "❌"
    client_secret_set = "✅" if store.get_google_client_secret() else "❌"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🔑 Nhập Client ID {client_id_set}", callback_data="login_yt_client_id"),
            InlineKeyboardButton(f"🔑 Nhập Client Secret {client_secret_set}", callback_data="login_yt_client_secret"),
        ],
        [InlineKeyboardButton("📋 URI chuyển hướng được ủy quyền", callback_data="login_yt_redirect_uri")],
        [InlineKeyboardButton("🔗 Login YouTube", callback_data="login_yt_do_oauth")],
        [InlineKeyboardButton("◀️ Quay lại", callback_data="menu_login")],
    ])


def get_gemini_login_keyboard():
    """Menu nhập Gemini cookies."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 Nhập PSID", callback_data="login_gemini_psid"),
            InlineKeyboardButton("🔑 Nhập PSIDTS", callback_data="login_gemini_psidts"),
        ],
        [InlineKeyboardButton("◀️ Quay lại", callback_data="menu_login")],
    ])


def is_youtube_url(text: str) -> bool:
    return any(p in text for p in YT_PATTERNS)


def extract_youtube_url(text: str) -> Optional[str]:
    pattern = r"(https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)[^\s]+)"
    match = re.search(pattern, text)
    return match.group(1) if match else None


# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=get_main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        HELP_TEXT,
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị menu chọn loại đăng nhập."""
    await update.message.reply_text(
        "🔐 *Đăng nhập*\n\nChọn dịch vụ bạn muốn đăng nhập:",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=get_login_keyboard(),
    )


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    oauth.logout(chat_id)
    await update.message.reply_text("✅ Đã đăng xuất khỏi tài khoản Google.")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    _sessions.pop(chat_id, None)
    await update.message.reply_text("🔄 Đã reset phiên chat. Bắt đầu cuộc trò chuyện mới!")


async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text(
            "📝 Dùng: `/image <mô tả ảnh>`\n"
            "Ví dụ: `/image mèo ngồi trên mặt trăng, phong cách anime`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)
    msg = await update.message.reply_text(f"🎨 Đang tạo ảnh: _{prompt}_...", parse_mode=ParseMode.MARKDOWN_V2)

    try:
        image_list = await gemini.generate_image(prompt)
        if not image_list:
            await msg.edit_text("⚠️ Gemini không trả về ảnh. Thử lại với mô tả khác.")
            return
        for img_bytes in image_list:
            await update.message.reply_photo(photo=io.BytesIO(img_bytes), caption=f"🎨 {prompt}")
        await msg.delete()
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        await msg.edit_text(f"❌ Lỗi tạo ảnh: {str(e)[:200]}")


async def youtube_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "📝 Dùng: `/youtube <url>`\n"
            "Ví dụ: `/youtube https://youtube.com/watch?v=...`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    url = args[0]
    extra_prompt = " ".join(args[1:]) if len(args) > 1 else ""
    await _analyze_youtube(update, url, extra_prompt)


async def setcookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cập nhật Gemini cookies (PSID / PSIDTS) trực tiếp qua Telegram."""
    chat_id = update.effective_chat.id

    # Lần đầu dùng → tự đặt làm admin
    admin_id = store.get_admin_id()
    if admin_id is None:
        store.set_admin_id(chat_id)
        admin_id = chat_id

    if chat_id != admin_id:
        await update.message.reply_text("⛔ Chỉ admin mới được dùng lệnh này.")
        return

    args = context.args  # /setcookie PSID [PSIDTS]  hoặc  /setcookie all <full_string>
    if not args:
        await update.message.reply_text(
            "📋 *Cách cập nhật Gemini cookies:*\n\n"
            "*Cách 1 — Full cookies \\(khuyến nghị để dùng tính năng video\\):*\n"
            "1\\. Mở [gemini\\.google\\.com](https://gemini.google.com) và đăng nhập\n"
            "2\\. Nhấn F12 → Console, dán lệnh:\n"
            "`copy\\(document\\.cookie\\)`\n"
            "3\\. Paste chuỗi vừa copy:\n"
            "`/setcookie all <chuỗi cookie>`\n\n"
            "*Cách 2 — Chỉ PSID/PSIDTS \\(tính năng chat/ảnh cơ bản\\):*\n"
            "`/setcookie <PSID> <PSIDTS>`\n\n"
            "⚠️ Tin nhắn chứa cookie sẽ bị xóa ngay sau khi đọc\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )
        return

    # Xóa tin nhắn ngay để bảo mật
    try:
        await update.message.delete()
    except Exception:
        pass

    # Phân biệt hai cú pháp
    if args[0].strip().lower() == "all":
        # /setcookie all <full_cookie_string>
        cookie_string = " ".join(args[1:]).strip()
        if not cookie_string:
            await update.effective_chat.send_message("❌ Thiếu chuỗi cookie. Dùng: `/setcookie all <chuỗi>`")
            return
        store.set_gemini_cookie_string(cookie_string)
        # Cũng trích PSID/PSIDTS từ chuỗi để dùng cho gemini-webapi
        for part in cookie_string.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip(); v = v.strip()
                if k == "__Secure-1PSID":
                    store.set_value("GEMINI_PSID", v)
                elif k == "__Secure-1PSIDTS":
                    store.set_value("GEMINI_PSIDTS", v)
        mode = "full cookie string"
    else:
        # /setcookie PSID [PSIDTS]
        psid = args[0].strip()
        psidts = args[1].strip() if len(args) > 1 else ""
        store.set_gemini_cookies(psid, psidts)
        mode = "PSID/PSIDTS"

    # Reset Gemini client để dùng cookies mới
    processing = await update.effective_chat.send_message("🔄 Đang kết nối lại Gemini với cookies mới...")
    try:
        await gemini.reset_client()
        await processing.edit_text(
            f"✅ *Gemini cookies đã cập nhật\\!* \\({_esc(mode)}\\)\n\n"
            "Client đã được kết nối lại\\. Thử chat ngay\\!",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        await processing.edit_text(
            f"⚠️ Cookies đã lưu \\({_esc(mode)}\\) nhưng kết nối thất bại:\n`{str(e)[:200]}`\n\n"
            "Kiểm tra lại cookie và thử lại\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


async def setyoutube_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhập YouTube cookies (Netscape format) để xem video riêng tư / giới hạn tuổi."""
    chat_id = update.effective_chat.id

    admin_id = store.get_admin_id()
    if admin_id is None:
        store.set_admin_id(chat_id)
        admin_id = chat_id

    if chat_id != admin_id:
        await update.message.reply_text("⛔ Chỉ admin mới được dùng lệnh này.")
        return

    # Nếu không có args → hướng dẫn, chờ user gửi file/text tiếp theo
    if not context.args:
        context.user_data["waiting_yt_cookies"] = True
        await update.message.reply_text(
            "📋 *Cách lấy YouTube cookies:*\n\n"
            "1\\. Cài extension: *Get cookies\\.txt LOCALLY* \\(Chrome/Firefox\\)\n"
            "2\\. Mở [youtube\\.com](https://youtube.com) và đăng nhập\n"
            "3\\. Nhấn icon extension → Export → chọn `youtube.com`\n"
            "4\\. Gửi file `cookies.txt` vào đây, hoặc paste nội dung sau lệnh:\n\n"
            "`/setyoutube <nội dung cookies>`\n\n"
            "⚠️ Cookies sẽ được lưu an toàn và dùng khi phân tích video bị giới hạn\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )
        return

    cookies_text = " ".join(context.args)
    try:
        await update.message.delete()
    except Exception:
        pass

    store.set_youtube_cookies(cookies_text)
    await update.effective_chat.send_message(
        "✅ *YouTube cookies đã lưu\\!*\n\n"
        "Bot sẽ dùng tài khoản YouTube của bạn khi phân tích video riêng tư hoặc giới hạn tuổi\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text(
            "📝 Dùng: `/video <mô tả video>`\n"
            "Ví dụ: `/video a cat playing piano in a jazz bar`\n\n"
            "⏳ Video mất khoảng 1\-2 phút để tạo\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_VIDEO)
    msg = await update.message.reply_text(
        f"🎬 Đang tạo video: _{prompt}_\n\n⏳ Veo đang xử lý, vui lòng chờ 1\-2 phút\.\.\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    try:
        video_list = await gemini.generate_video(prompt)
        if not video_list:
            await msg.edit_text(
                "⚠️ Gemini không trả về video\\. Có thể do:\n"
                "• Tài khoản chưa có quyền dùng Veo\n"
                "• Đã hết giới hạn tạo video hôm nay\n"
                "• Mô tả vi phạm chính sách nội dung\n\n"
                "Thử mô tả khác hoặc dùng `/image` để tạo ảnh thay thế\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
        for i, vid_bytes in enumerate(video_list, 1):
            await update.message.reply_video(
                video=io.BytesIO(vid_bytes),
                caption=f"🎬 {prompt}",
                supports_streaming=True,
            )
        await msg.delete()
    except Exception as e:
        err = str(e)
        logger.error(f"Video generation error: {e}")
        if "rate limit" in err.lower() or "today" in err.lower():
            await msg.edit_text("⚠️ Đã hết giới hạn tạo video hôm nay\\. Thử lại ngày mai\\.", parse_mode=ParseMode.MARKDOWN_V2)
        elif "not available" in err.lower():
            await msg.edit_text("⚠️ Tạo video chưa khả dụng với tài khoản này\\.", parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await msg.edit_text(f"❌ Lỗi tạo video: {err[:200]}")


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hỏi nhanh không lưu lịch sử."""
    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text("📝 Dùng: `/ask <câu hỏi>`", parse_mode=ParseMode.MARKDOWN_V2)
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    msg = await update.message.reply_text("⏳ Đang xử lý...")
    try:
        result = await gemini.chat(prompt)
        text = result["text"] or "(Không có phản hồi)"
        await msg.edit_text(f"💬 {text[:4000]}")
    except Exception as e:
        await msg.edit_text(f"❌ Lỗi: {str(e)[:200]}")


# ─────────────────────────────────────────────
# MESSAGE HANDLERS
# ─────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý tin nhắn văn bản thông thường."""
    text = update.message.text or ""
    chat_id = update.effective_chat.id

    async def _edit_menu(text: str, reply_markup=None, **kwargs):
        """Edit tin nhắn menu cũ nếu có, không thì gửi mới."""
        menu_msg_id = context.user_data.get("menu_msg_id")
        if menu_msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=menu_msg_id,
                    text=text,
                    reply_markup=reply_markup,
                    **kwargs,
                )
                return
            except Exception:
                pass
        await update.effective_chat.send_message(text, reply_markup=reply_markup, **kwargs)

    async def _delete_user_msg():
        try:
            await update.message.delete()
        except Exception:
            pass

    # ── Đang chờ nhập YouTube Client ID / Secret / URL ──
    waiting_yt = context.user_data.get("waiting_yt")
    if waiting_yt in ("client_id", "client_secret", "url"):
        context.user_data.pop("waiting_yt", None)
        value = text.strip()

        if waiting_yt == "url":
            await _analyze_youtube(update, value)
            return

        await _delete_user_msg()

        if waiting_yt == "client_id":
            store.set_google_client_id(value)
            label = "Client ID"
        else:
            store.set_google_client_secret(value)
            label = "Client Secret"

        hint = "Nhấn 🔗 Login YouTube khi đã nhập đủ cả 2 giá trị\\." if not oauth.is_oauth_configured() else "Đã đủ thông tin, nhấn 🔗 Login YouTube để xác thực\\."
        await _edit_menu(
            f"📺 *YouTube — Đăng nhập Google OAuth*\n\n✅ *{label} đã lưu\\!*\n\n{hint}",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=get_youtube_oauth_keyboard(),
        )
        return

    # ── Đang chờ nhập Gemini PSID hoặc PSIDTS ──
    waiting_gemini = context.user_data.get("waiting_gemini")
    if waiting_gemini in ("psid", "psidts"):
        context.user_data.pop("waiting_gemini", None)
        await _delete_user_msg()
        value = text.strip()
        if waiting_gemini == "psid":
            store.set_gemini_cookies(value, store.get_gemini_psidts())
            field = "PSID"
        else:
            store.set_gemini_cookies(store.get_gemini_psid(), value)
            field = "PSIDTS"
        psid_set = "✅" if store.get_gemini_psid() else "❌"
        psidts_set = "✅" if store.get_gemini_psidts() else "❌"
        await _edit_menu(
            f"🤖 *Gemini Cookies*\n\n"
            f"✅ *{field} đã lưu\\!* Đang kết nối lại\\.\\.\\.\n\n"
            f"Trạng thái:\n• PSID: {psid_set}\n• PSIDTS: {psidts_set}",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=get_gemini_login_keyboard(),
        )
        try:
            await gemini.reset_client()
            psid_set2 = "✅" if store.get_gemini_psid() else "❌"
            psidts_set2 = "✅" if store.get_gemini_psidts() else "❌"
            await _edit_menu(
                f"🤖 *Gemini Cookies*\n\n"
                f"✅ *{field} đã lưu và kết nối thành công\\!*\n\n"
                f"Trạng thái:\n• PSID: {psid_set2}\n• PSIDTS: {psidts_set2}",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=get_gemini_login_keyboard(),
            )
        except Exception as e:
            await _edit_menu(
                f"🤖 *Gemini Cookies*\n\n"
                f"⚠️ *{field} đã lưu* nhưng kết nối thất bại:\n`{str(e)[:150]}`\n\n"
                f"Trạng thái:\n• PSID: {psid_set}\n• PSIDTS: {psidts_set}",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=get_gemini_login_keyboard(),
            )
        return

    # Tự động phát hiện YouTube URL
    yt_url = extract_youtube_url(text)
    if yt_url:
        extra = text.replace(yt_url, "").strip()
        await _analyze_youtube(update, yt_url, extra)
        return

    # Chat với Gemini (có memory)
    await update.message.chat.send_action(ChatAction.TYPING)
    session = _sessions.get(chat_id, {})
    msg = await update.message.reply_text("⏳ Gemini đang trả lời...")

    try:
        result = await gemini.chat(
            text,
            conversation_id=session.get("conversation_id"),
            reply_to=session.get("reply_id"),
        )
        # Lưu session
        if result.get("conversation_id"):
            _sessions[chat_id] = {
                "conversation_id": result["conversation_id"],
                "reply_id": result["reply_id"],
                "rc_id": result.get("rc_id"),
            }

        reply_text = result["text"] or "(Không có phản hồi)"

        # Gửi ảnh nếu có
        if result.get("images"):
            for img in result["images"]:
                if hasattr(img, "bytes") and img.bytes:
                    await update.message.reply_photo(photo=io.BytesIO(img.bytes))

        # Chia nhỏ nếu quá dài
        if len(reply_text) > 4000:
            chunks = [reply_text[i:i+4000] for i in range(0, len(reply_text), 4000)]
            await msg.edit_text(chunks[0])
            for chunk in chunks[1:]:
                await update.message.reply_text(chunk)
        else:
            await msg.edit_text(reply_text)

    except Exception as e:
        logger.error(f"Chat error: {e}")
        await msg.edit_text(f"❌ Lỗi: {str(e)[:200]}\n\nThử /reset để bắt đầu lại.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý file được gửi lên — nếu đang chờ YouTube cookies thì lưu."""
    chat_id = update.effective_chat.id
    doc = update.message.document

    # Chờ file cookies YouTube
    if context.user_data.get("waiting_yt_cookies"):
        if doc and doc.file_name and doc.file_name.endswith(".txt"):
            context.user_data.pop("waiting_yt_cookies", None)
            msg = await update.message.reply_text("📥 Đang đọc file cookies...")
            try:
                file = await context.bot.get_file(doc.file_id)
                with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
                    tmp = f.name
                await file.download_to_drive(tmp)
                cookies_text = Path(tmp).read_text()
                Path(tmp).unlink(missing_ok=True)
                store.set_youtube_cookies(cookies_text)
                await msg.edit_text(
                    "✅ *YouTube cookies đã lưu từ file\\!*\n\n"
                    "Bot sẽ dùng tài khoản YouTube của bạn khi phân tích video riêng tư hoặc giới hạn tuổi\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            except Exception as e:
                await msg.edit_text(f"❌ Lỗi đọc file: {str(e)[:200]}")
        else:
            await update.message.reply_text("⚠️ Gửi file `.txt` chứa cookies theo định dạng Netscape.")
        return

    # File khác → gửi cho Gemini phân tích
    await update.message.chat.send_action(ChatAction.TYPING)
    caption = update.message.caption or f"Phân tích file này: {doc.file_name if doc else 'file'}"
    msg = await update.message.reply_text("📄 Đang phân tích file...")
    try:
        result = await gemini.chat(f"[File đính kèm: {doc.file_name if doc else 'unknown'}]\n{caption}")
        await msg.edit_text((result["text"] or "(Không có phản hồi)")[:4000])
    except Exception as e:
        await msg.edit_text(f"❌ Lỗi: {str(e)[:200]}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý ảnh được gửi lên."""
    caption = update.message.caption or "Mô tả ảnh này chi tiết bằng tiếng Việt."
    await update.message.chat.send_action(ChatAction.TYPING)
    msg = await update.message.reply_text("📸 Đang phân tích ảnh...")

    try:
        # Tải ảnh về
        photo = update.message.photo[-1]  # Lấy ảnh chất lượng cao nhất
        file = await context.bot.get_file(photo.file_id)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp_path = f.name
        await file.download_to_drive(tmp_path)

        result = await gemini.chat(f"[Ảnh được đính kèm]\n{caption}")
        reply_text = result["text"] or "(Không có phản hồi)"
        await msg.edit_text(reply_text[:4000])
        Path(tmp_path).unlink(missing_ok=True)
    except Exception as e:
        logger.error(f"Photo analysis error: {e}")
        await msg.edit_text(f"❌ Lỗi phân tích ảnh: {str(e)[:200]}")


# ─────────────────────────────────────────────
# CALLBACK QUERIES (Inline buttons)
# ─────────────────────────────────────────────

async def _edit(query, text: str, reply_markup=None, **kwargs):
    """Edit tin nhắn hiện tại, fallback gửi mới nếu thất bại."""
    try:
        await query.message.edit_text(text, reply_markup=reply_markup, **kwargs)
    except Exception:
        await query.message.reply_text(text, reply_markup=reply_markup, **kwargs)


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id

    # Lưu message_id để handle_message có thể edit lại
    context.user_data["menu_msg_id"] = query.message.message_id

    if data == "menu_chat":
        await _edit(query,
            "🗣 *Chế độ Chat*\n\nNhắn bất kỳ tin nhắn nào để chat với Gemini AI\\.\nDùng /reset để bắt đầu chủ đề mới\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Menu chính", callback_data="back_main")]]),
        )

    elif data == "menu_image":
        await _edit(query,
            "🎨 *Tạo ảnh AI*\n\nDùng lệnh:\n`/image <mô tả ảnh>`\n\nVí dụ:\n`/image bãi biển lúc hoàng hôn, phong cách watercolor`",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Menu chính", callback_data="back_main")]]),
        )

    elif data == "menu_video":
        await _edit(query,
            "🎬 *Tạo video AI \\(Veo\\)*\n\nDùng lệnh:\n`/video <mô tả video>`\n\nVí dụ:\n`/video a sunset over the ocean with waves crashing`\n\n⏳ Video mất khoảng 1\\-2 phút để tạo\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Menu chính", callback_data="back_main")]]),
        )

    elif data == "menu_youtube":
        await _edit(query,
            "▶️ *Phân tích YouTube*\n\nCách 1: Gửi link YouTube vào chat\nCách 2: `/youtube <url>`\n\nGemini sẽ tóm tắt và phân tích nội dung video\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Menu chính", callback_data="back_main")]]),
        )

    elif data == "menu_help":
        await _edit(query, HELP_TEXT,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Menu chính", callback_data="back_main")]]),
        )

    elif data == "menu_reset":
        _sessions.pop(chat_id, None)
        await _edit(query,
            "🔄 Đã reset phiên chat\\! Bắt đầu cuộc trò chuyện mới nhé\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Menu chính", callback_data="back_main")]]),
        )

    elif data == "back_main":
        await _edit(query, WELCOME_TEXT,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=get_main_keyboard(),
        )

    # ── Login menu ──────────────────────────────
    elif data == "menu_login":
        await _edit(query,
            "🔐 *Đăng nhập*\n\nChọn dịch vụ bạn muốn đăng nhập:",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=get_login_keyboard(),
        )

    elif data == "login_cancel":
        context.user_data.pop("waiting_gemini", None)
        context.user_data.pop("waiting_yt", None)
        context.user_data.pop("waiting_yt_cookies", None)
        context.user_data.pop("menu_msg_id", None)
        try:
            await query.message.delete()
        except Exception:
            pass

    # ── YouTube OAuth ────────────────────────────
    elif data == "login_youtube_oauth":
        user_info = oauth.get_user_info(chat_id)
        if user_info:
            name = user_info.get('name', '').replace('.', '\\.').replace('-', '\\-')
            email = user_info.get('email', '').replace('.', '\\.').replace('@', '\\@').replace('-', '\\-')
            await _edit(query,
                f"✅ *Đã đăng nhập Google*\n\n👤 {name}\n📧 {email}\n\nDùng /logout để đăng xuất\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=get_youtube_oauth_keyboard(),
            )
            return
        await _edit(query,
            "📺 *YouTube — Đăng nhập Google OAuth*\n\n"
            "Nhập `Client ID` và `Client Secret` từ Google Cloud Console,\n"
            "sau đó nhấn *Login YouTube* để xác thực\\.\n\n"
            "📌 [console\\.cloud\\.google\\.com](https://console.cloud.google.com) → APIs & Services → Credentials",
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
            reply_markup=get_youtube_oauth_keyboard(),
        )

    elif data == "login_yt_client_id":
        context.user_data["waiting_yt"] = "client_id"
        context.user_data.pop("waiting_gemini", None)
        await _edit(query,
            "🔑 *Nhập Google Client ID*\n\nGửi giá trị Client ID vào đây:\n\n⚠️ Tin nhắn của bạn sẽ bị xóa ngay sau khi lưu\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Huỷ", callback_data="login_youtube_oauth")]]),
        )

    elif data == "login_yt_client_secret":
        context.user_data["waiting_yt"] = "client_secret"
        context.user_data.pop("waiting_gemini", None)
        await _edit(query,
            "🔑 *Nhập Google Client Secret*\n\nGửi giá trị Client Secret vào đây:\n\n⚠️ Tin nhắn của bạn sẽ bị xóa ngay sau khi lưu\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Huỷ", callback_data="login_youtube_oauth")]]),
        )

    elif data == "login_yt_do_oauth":
        if not oauth.is_oauth_configured():
            await _edit(query,
                "⚠️ Nhập đủ Client ID và Client Secret trước nhé\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=get_youtube_oauth_keyboard(),
            )
            return
        try:
            auth_url, _ = oauth.get_auth_url(chat_id)
        except Exception as e:
            await _edit(query,
                f"❌ *Không thể tạo link đăng nhập*\n\n"
                f"`{str(e)[:300]}`\n\n"
                f"Kiểm tra lại Client ID / Client Secret có đúng không\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=get_youtube_oauth_keyboard(),
            )
            return
        await _edit(query,
            "🔗 *Đăng nhập YouTube qua Google*\n\n"
            "Nhấn nút bên dưới để xác thực tài khoản Google của bạn\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Đăng nhập với Google", url=auth_url)],
                [InlineKeyboardButton("◀️ Quay lại", callback_data="login_youtube_oauth")],
            ]),
        )

    elif data == "login_yt_redirect_uri":
        await _edit(query,
            f"📋 *URI chuyển hướng được ủy quyền*\n\n"
            f"Copy URI bên dưới và dán vào Google Cloud Console:\n\n"
            f"`{REDIRECT_URI}`\n\n"
            f"📌 *Cách thêm:*\n"
            f"Google Cloud Console → APIs & Services → Credentials → OAuth 2\\.0 Client IDs → Authorized redirect URIs → Add URI",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Quay lại", callback_data="login_youtube_oauth")]]),
        )

    # ── Gemini cookies ───────────────────────────
    elif data == "login_gemini":
        psid_set = "✅" if store.get_gemini_psid() else "❌"
        psidts_set = "✅" if store.get_gemini_psidts() else "❌"
        await _edit(query,
            f"🤖 *Gemini Cookies*\n\n"
            f"Trạng thái:\n• PSID: {psid_set}\n• PSIDTS: {psidts_set}\n\n"
            f"Cách lấy:\n"
            f"1\\. Mở [gemini\\.google\\.com](https://gemini.google.com) → Đăng nhập\n"
            f"2\\. F12 → Application → Cookies\n"
            f"3\\. Copy `__Secure\\-1PSID` và `__Secure\\-1PSIDTS`\n\n"
            f"Chọn giá trị muốn nhập:",
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
            reply_markup=get_gemini_login_keyboard(),
        )

    elif data == "login_gemini_psid":
        context.user_data["waiting_gemini"] = "psid"
        context.user_data.pop("waiting_yt", None)
        await _edit(query,
            "🔑 *Nhập PSID*\n\nGửi giá trị `__Secure\\-1PSID` của bạn:\n\n⚠️ Tin nhắn của bạn sẽ bị xóa ngay sau khi lưu\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Huỷ", callback_data="login_gemini")]]),
        )

    elif data == "login_gemini_psidts":
        context.user_data["waiting_gemini"] = "psidts"
        context.user_data.pop("waiting_yt", None)
        await _edit(query,
            "🔑 *Nhập PSIDTS*\n\nGửi giá trị `__Secure\\-1PSIDTS` của bạn:\n\n⚠️ Tin nhắn của bạn sẽ bị xóa ngay sau khi lưu\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Huỷ", callback_data="login_gemini")]]),
        )

    elif data == "copy_uri":
        await _edit(query,
            f"📋 *Authorized Redirect URI:*\n\n`{REDIRECT_URI}`\n\n"
            "Thêm URI trên vào Google Cloud Console → Credentials → OAuth 2\\.0 Client → Authorized redirect URIs\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Quay lại", callback_data="menu_login")]]),
        )

    elif data == "menu_auto_video":
        user_info = oauth.get_user_info(chat_id)
        creds = store.get_google_credentials(chat_id)
        if not user_info or not creds:
            await _edit(query,
                "🤖 *Auto Video Hoạt Hình*\n\n"
                "⚠️ Bạn cần đăng nhập YouTube trước để bot có thể tự đăng video lên kênh của bạn\\.\n\n"
                "Nhấn 🔐 Đăng nhập → 📺 YouTube \\(Google\\) để xác thực\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔐 Đăng nhập YouTube", callback_data="login_youtube_oauth")],
                    [InlineKeyboardButton("◀️ Menu chính", callback_data="back_main")],
                ]),
            )
            return
        channel = user_info.get("channel")
        ch_name = _esc(channel["title"] if channel else user_info.get("name", "kênh của bạn"))
        await _edit(query,
            f"🤖 *Auto Video Hoạt Hình*\n\n"
            f"📺 Kênh: *{ch_name}*\n\n"
            f"Bot sẽ tự động:\n"
            f"1\\. 🎯 Chọn chủ đề hoạt hình ngẫu nhiên\n"
            f"2\\. ✍️ AI viết kịch bản \\+ tiêu đề \\+ mô tả SEO\n"
            f"3\\. 🎬 Tạo video hoạt hình bằng Gemini Veo \\(\\~2 phút\\)\n"
            f"4\\. 🚀 Tự đăng công khai lên YouTube\n\n"
            f"⏳ Toàn bộ quá trình mất khoảng *3\\-5 phút*\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Bắt đầu tạo video!", callback_data="auto_video_start")],
                [InlineKeyboardButton("◀️ Huỷ", callback_data="back_main")],
            ]),
        )

    elif data == "auto_video_start":
        user_info = oauth.get_user_info(chat_id)
        creds = store.get_google_credentials(chat_id)
        if not user_info or not creds:
            await _edit(query,
                "⚠️ Phiên đăng nhập hết hạn\\. Vui lòng /login lại\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
        status_msg = await query.message.reply_text(
            "🤖 *Auto Video Hoạt Hình đang khởi động\\.\\.\\.*\n\n⏳ Đang chuẩn bị\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        import asyncio
        asyncio.create_task(_run_auto_video(chat_id, status_msg))


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────


def _esc(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


async def _run_auto_video(chat_id: int, status_msg):
    """Pipeline tạo video tự động — chạy dưới background task."""

    async def update_msg(text: str):
        """Gửi cập nhật lên Telegram, escape MarkdownV2 tự động cho phần plain text."""
        try:
            await status_msg.edit_text(text, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.warning(f"[AutoVideo] update_msg error: {e}")

    try:
        result = await auto_video.run_auto_video_pipeline(chat_id, update_msg)

        url = result["youtube_url"]
        title = result["title"]
        topic = result["topic"]
        elapsed = result.get("elapsed", "")
        tags_str = " ".join(
            f"\\#{t.replace(' ', '').replace('-', '')}"
            for t in result.get("tags", [])[:10]
        )

        await status_msg.edit_text(
            f"🎉 *Video hoạt hình đã đăng lên YouTube\\!*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Bước 1/4 — Chủ đề chọn\n"
            f"✅ Bước 2/4 — Kịch bản viết xong\n"
            f"✅ Bước 3/4 — Video render xong\n"
            f"✅ Bước 4/4 — Đã đăng công khai\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *Chủ đề:* {_esc(topic)}\n"
            f"📝 *Tiêu đề:* {_esc(title)}\n"
            f"🔗 *Link:* {_esc(url)}\n"
            f"⏱ *Tổng thời gian:* {_esc(elapsed)}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{tags_str}",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Xem video trên YouTube", url=url)],
                [InlineKeyboardButton("🔁 Tạo video khác", callback_data="auto_video_start")],
                [InlineKeyboardButton("◀️ Menu chính", callback_data="back_main")],
            ]),
        )
    except Exception as e:
        logger.error(f"[AutoVideo] Pipeline error: {e}")
        err_lines = str(e).split("\n")
        err_display = "\n".join(f"`{_esc(line)}`" for line in err_lines[:5] if line.strip())
        try:
            await status_msg.edit_text(
                f"❌ *Lỗi trong quá trình tạo video:*\n\n"
                f"{err_display}\n\n"
                f"Kiểm tra lại:\n"
                f"• Gemini cookies \\(/setcookie\\)\n"
                f"• Đăng nhập YouTube \\(/login\\)\n"
                f"• YouTube Data API v3 đã bật trong Google Cloud",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔁 Thử lại", callback_data="auto_video_start")],
                    [InlineKeyboardButton("◀️ Menu chính", callback_data="back_main")],
                ]),
            )
        except Exception:
            pass

async def _analyze_youtube(update: Update, url: str, extra_prompt: str = ""):
    prompt = extra_prompt if extra_prompt else "Tóm tắt và phân tích chi tiết video YouTube này bằng tiếng Việt. Nêu các điểm chính, nội dung quan trọng và nhận xét."
    await update.message.chat.send_action(ChatAction.TYPING)
    msg = await update.message.reply_text(f"▶️ Đang phân tích video YouTube...")

    try:
        result = await gemini.analyze_url(url, prompt)
        reply = f"▶️ *Phân tích YouTube:*\n\n{result}"
        if len(reply) > 4000:
            chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
            await msg.edit_text(chunks[0], parse_mode=ParseMode.MARKDOWN_V2)
            for chunk in chunks[1:]:
                await update.message.reply_text(chunk)
        else:
            await msg.edit_text(reply, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"YouTube analysis error: {e}")
        # Thử lại không có markdown
        try:
            result = await gemini.analyze_url(url, prompt)
            await msg.edit_text(f"▶️ Phân tích YouTube:\n\n{result[:4000]}")
        except Exception as e2:
            await msg.edit_text(f"❌ Lỗi phân tích: {str(e2)[:200]}")
