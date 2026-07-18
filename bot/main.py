"""
Entry point: chạy Telegram bot (polling) + FastAPI web server (OAuth) cùng lúc.
"""
import asyncio
import os
import sys

import uvicorn
from loguru import logger
from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.config import TELEGRAM_BOT_TOKEN, PORT, REDIRECT_URI, BASE_URL
from bot.handlers import (
    ask_command,
    handle_callback_query,
    handle_document,
    handle_message,
    handle_photo,
    help_command,
    image_command,
    login_command,
    logout_command,
    reset_command,
    setcookie_command,
    setyoutube_command,
    start_command,
    video_command,
    youtube_command,
)
from bot.web import app as fastapi_app, set_telegram_app


def build_telegram_app() -> Application:
    """Khởi tạo Telegram Application và đăng ký handlers."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("login", login_command))
    app.add_handler(CommandHandler("logout", logout_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("image", image_command))
    app.add_handler(CommandHandler("video", video_command))
    app.add_handler(CommandHandler("youtube", youtube_command))
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(CommandHandler("setcookie", setcookie_command))
    app.add_handler(CommandHandler("setyoutube", setyoutube_command))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Messages
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app


async def set_bot_commands(app: Application):
    """Cài đặt danh sách lệnh hiển thị trong Telegram."""
    commands = [
        BotCommand("start", "Bắt đầu / Menu chính"),
        BotCommand("help", "Trợ giúp"),
        BotCommand("image", "Tạo ảnh AI: /image <mô tả>"),
        BotCommand("video", "Tạo video AI (Veo): /video <mô tả>"),
        BotCommand("youtube", "Phân tích YouTube: /youtube <url>"),
        BotCommand("ask", "Hỏi nhanh: /ask <câu hỏi>"),
        BotCommand("setcookie", "Cập nhật Gemini cookies (PSID/PSIDTS)"),
        BotCommand("setyoutube", "Nhập YouTube cookies (video riêng tư)"),
        BotCommand("login", "Đăng nhập Google OAuth"),
        BotCommand("logout", "Đăng xuất"),
        BotCommand("reset", "Reset phiên chat"),
    ]
    await app.bot.set_my_commands(commands)
    logger.info("Bot commands set OK")


async def main():
    logger.info("=" * 50)
    logger.info("Starting Gemini Telegram Bot")
    logger.info(f"Base URL: {BASE_URL}")
    logger.info(f"OAuth Redirect URI: {REDIRECT_URI}")
    logger.info(f"Web server port: {PORT}")
    logger.info("=" * 50)

    # Khởi tạo Telegram app
    tg_app = build_telegram_app()
    set_telegram_app(tg_app)

    # Khởi tạo Gemini client trước
    try:
        from bot import gemini
        await gemini.get_client()
        logger.info("Gemini client initialized OK")
    except Exception as e:
        logger.warning(f"Gemini client init failed (sẽ thử lại khi cần): {e}")

    # Cấu hình uvicorn cho FastAPI
    uv_config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=PORT,
        log_level="warning",
    )
    uv_server = uvicorn.Server(uv_config)

    # Chạy cả hai concurrently
    async with tg_app:
        await set_bot_commands(tg_app)
        await tg_app.start()
        await tg_app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        logger.info("Telegram bot polling started")

        # Gửi thông tin redirect URI qua log (để dễ tìm)
        logger.info(f"\n{'='*50}")
        logger.info(f"AUTHORIZED REDIRECT URI (thêm vào Google Cloud Console):")
        logger.info(f"  {REDIRECT_URI}")
        logger.info(f"{'='*50}\n")

        # Chạy web server
        await uv_server.serve()

        await tg_app.updater.stop()
        await tg_app.stop()


if __name__ == "__main__":
    asyncio.run(main())
