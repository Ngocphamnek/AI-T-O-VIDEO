"""
Tự động hoá Gemini web bằng Playwright để tạo video.
Luồng: mở Chrome headless → đăng nhập bằng cookies → chọn Video mode → nhắn prompt → đợi video → tải về.
"""
import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from loguru import logger


def _get_nix_chromium() -> str | None:
    """
    Lấy đường dẫn Chromium từ nixpkgs — đã build đúng cho NixOS, không cần
    LD_LIBRARY_PATH thủ công.  Trả về path đến binary hoặc None nếu không tìm được.
    """
    try:
        result = subprocess.run(
            ["nix-build", "<nixpkgs>", "-A", "chromium", "--no-out-link"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            store_path = result.stdout.strip()
            binary = f"{store_path}/bin/chromium"
            if os.path.isfile(binary):
                logger.info(f"[PW] Dùng nixpkgs Chromium: {binary}")
                return binary
    except Exception as e:
        logger.debug(f"[PW] nix-build chromium thất bại: {e}")
    logger.warning("[PW] Không tìm được nixpkgs Chromium — dùng Playwright mặc định")
    return None


# Cache chromium path tại import time (chạy một lần)
_NIX_CHROMIUM: str | None = _get_nix_chromium()


async def generate_video(topic: str, psid: str, psidts: str, timeout: int = 360) -> bytes | None:
    """
    Dùng Playwright điều khiển Gemini web để tạo video.
    Trả về bytes của file MP4, hoặc None nếu thất bại.
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        logger.error("[PW] playwright chưa được cài. Chạy: playwright install chromium")
        return None

    from bot.store import get_gemini_psid, get_gemini_psidts
    psid = psid or get_gemini_psid()
    psidts = psidts or get_gemini_psidts()

    prompt_text = f"tạo video: {topic}"
    logger.info(f"[PW] Bắt đầu: '{prompt_text}'")

    video_bytes: bytes | None = None
    video_url: str | None = None

    async with async_playwright() as p:
        launch_kwargs: dict = dict(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-gpu-sandbox",
                "--disable-software-rasterizer",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-sync",
                "--disable-translate",
                "--no-first-run",
                "--no-zygote",
                "--disable-features=VizDisplayCompositor,site-per-process",
                "--enable-features=NetworkService",
                "--allow-running-insecure-content",
                "--memory-pressure-off",
            ],
        )
        if _NIX_CHROMIUM:
            launch_kwargs["executable_path"] = _NIX_CHROMIUM
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        # ── Đặt cookies Gemini ────────────────────────────────────────
        from bot import store as _store

        # Ưu tiên full cookie string (set qua /setcookie all ...)
        full_cookie_str = _store.get_gemini_cookie_string()

        def _secure_cookie(name: str, value: str) -> dict:
            """Cookie mặc định cho domain .google.com với __Secure- prefix."""
            return {
                "name": name, "value": value,
                "domain": ".google.com", "path": "/",
                "secure": True, "httpOnly": True, "sameSite": "None",
            }

        if full_cookie_str:
            # Parse chuỗi "key=value; key2=value2" từ browser
            cookies = []
            for part in full_cookie_str.split(";"):
                part = part.strip()
                if "=" not in part:
                    continue
                k, v = part.split("=", 1)
                k = k.strip(); v = v.strip()
                if not k or not v:
                    continue
                is_secure = k.startswith("__Secure-") or k.startswith("__Host-")
                cookies.append({
                    "name": k, "value": v,
                    "domain": ".google.com", "path": "/",
                    "secure": is_secure,
                    "httpOnly": False,
                    "sameSite": "None" if is_secure else "Lax",
                })
            logger.info(f"[PW] Dùng full cookie string: {len(cookies)} cookies")
        else:
            # Fallback: chỉ PSID + PSIDTS
            cookies = [_secure_cookie("__Secure-1PSID", psid)]
            if psidts:
                cookies.append(_secure_cookie("__Secure-1PSIDTS", psidts))
            logger.info("[PW] Dùng PSID/PSIDTS (chế độ cơ bản — video có thể không khả dụng)")

        await context.add_cookies(cookies)

        page = await context.new_page()

        # ── Bắt video từ network responses ───────────────────────────
        async def on_response(response):
            nonlocal video_bytes, video_url
            if video_bytes:
                return
            ct = response.headers.get("content-type", "")
            url = response.url
            is_video = (
                "video" in ct
                or url.endswith(".mp4")
                or "videoplayback" in url
                or "GeneratedVideo" in url
                or ("contribution.usercontent.google.com" in url and "download" in url)
            )
            if is_video:
                try:
                    body = await response.body()
                    if len(body) > 50_000:  # > 50 KB → likely real video
                        video_bytes = body
                        video_url = url
                        logger.info(f"[PW] Captured video từ network: {len(body)//1024} KB — {url[:80]}")
                except Exception as e:
                    logger.debug(f"[PW] on_response error: {e}")

        page.on("response", on_response)

        # ── Mở Gemini ─────────────────────────────────────────────────
        logger.info("[PW] Đang mở gemini.google.com...")
        await page.goto("https://gemini.google.com/app", wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(3)

        # Kiểm tra đã đăng nhập chưa
        url_now = page.url
        logger.info(f"[PW] URL sau load: {url_now}")
        if "accounts.google.com" in url_now or "signin" in url_now:
            logger.error("[PW] Bị redirect sang trang đăng nhập — cookies không hợp lệ")
            await browser.close()
            return None

        # Kiểm tra chế độ đăng nhập (guest vs full)
        try:
            mode_text = await page.locator("button[aria-label*='mode picker']").text_content(timeout=3000)
            is_guest = mode_text and "Sign in" in mode_text
            logger.info(f"[PW] Chế độ: {'GUEST (hạn chế)' if is_guest else 'ĐÃ ĐĂNG NHẬP'} — {mode_text!r}")
            if is_guest and not full_cookie_str:
                logger.warning("[PW] Đang ở GUEST mode. Video generation có thể không hoạt động. "
                               "Dùng /setcookie all <full_cookie> để đăng nhập đầy đủ.")
        except Exception:
            is_guest = False

        # ── Tìm và kích hoạt Video mode ───────────────────────────────
        video_mode_activated = False

        # Nút "Upload & tools" — selector đã xác nhận từ debug
        try:
            tools_btn = page.locator("button[aria-label='Upload & tools']")
            if await tools_btn.is_visible(timeout=3000):
                await tools_btn.click()
                await asyncio.sleep(1.5)
                logger.info("[PW] Clicked 'Upload & tools'")

                # Tìm option "Create video" hoặc "Video" trong menu
                for sel, label in [
                    ("button:has-text('Create video')", "Create video"),
                    ("button:has-text('Video')", "Video"),
                    ("[aria-label*='video' i]", "aria-video"),
                    ("button:has-text('Tạo video')", "Tạo video"),
                ]:
                    try:
                        opt = page.locator(sel).first
                        if await opt.is_visible(timeout=1500):
                            await opt.click()
                            await asyncio.sleep(1)
                            video_mode_activated = True
                            logger.info(f"[PW] Video mode activated via: {label}")
                            break
                    except Exception:
                        pass

                if not video_mode_activated:
                    # Đóng menu nếu không tìm thấy video option
                    await page.keyboard.press("Escape")
                    logger.info("[PW] Không có 'Create video' trong menu — guest mode? Thử prompt text trực tiếp")
        except Exception as e:
            logger.debug(f"[PW] Upload & tools error: {e}")

        if not video_mode_activated:
            logger.info("[PW] Gửi prompt text trực tiếp (không có video mode)")

        # ── Nhập prompt ───────────────────────────────────────────────
        input_sel = None
        for sel in [
            'rich-textarea [contenteditable="true"]',
            '[contenteditable="true"]',
            'textarea',
            '.ql-editor',
            'div[role="textbox"]',
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    input_sel = sel
                    break
            except Exception:
                pass

        if not input_sel:
            logger.error("[PW] Không tìm thấy ô nhập liệu")
            await browser.close()
            return None

        logger.info(f"[PW] Đang gõ prompt: '{prompt_text}'")
        await page.locator(input_sel).first.click()
        await asyncio.sleep(0.5)
        await page.keyboard.type(prompt_text, delay=30)
        await asyncio.sleep(0.5)

        # ── Submit ────────────────────────────────────────────────────
        submitted = False
        for sel in [
            'button[aria-label*="Send"]',
            'button[aria-label*="Gửi"]',
            '[data-test-id="send-button"]',
            'button.send-button',
            'button[mattooltip*="Send" i]',
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    submitted = True
                    logger.info(f"[PW] Submit bằng: {sel}")
                    break
            except Exception:
                pass

        if not submitted:
            await page.keyboard.press("Enter")
            logger.info("[PW] Submit bằng Enter")

        # ── Đợi video ─────────────────────────────────────────────────
        logger.info(f"[PW] Đang chờ Gemini tạo video (tối đa {timeout}s)...")
        deadline = asyncio.get_event_loop().time() + timeout
        check_interval = 5

        while asyncio.get_event_loop().time() < deadline:
            # Ưu tiên: video đã bị bắt qua network
            if video_bytes:
                break

            # Kiểm tra thẻ <video> trong DOM
            try:
                video_el = page.locator("video").first
                if await video_el.is_visible(timeout=1000):
                    src = await video_el.get_attribute("src")
                    if not src:
                        # Thử lấy từ <source> bên trong
                        src = await page.locator("video source").first.get_attribute("src")
                    if src and src.startswith("http"):
                        video_url = src
                        logger.info(f"[PW] Tìm thấy <video> src: {src[:80]}")
                        break
                    elif src and src.startswith("blob:"):
                        # blob URL: cần dùng JS để lấy bytes
                        js_result = await page.evaluate("""
                            async (blobUrl) => {
                                const r = await fetch(blobUrl);
                                const buf = await r.arrayBuffer();
                                return Array.from(new Uint8Array(buf));
                            }
                        """, src)
                        if js_result and len(js_result) > 10000:
                            video_bytes = bytes(js_result)
                            logger.info(f"[PW] Blob video: {len(video_bytes)//1024} KB")
                            break
            except Exception:
                pass

            # Kiểm tra nút download
            try:
                dl_btn = page.locator('[aria-label*="Download"], [aria-label*="Tải"], button:has-text("Download")').first
                if await dl_btn.is_visible(timeout=1000):
                    logger.info("[PW] Tìm thấy nút Download!")
                    # Click và bắt file download
                    async with page.expect_download(timeout=60_000) as dl_info:
                        await dl_btn.click()
                    download = await dl_info.value
                    tmp = Path(tempfile.mkdtemp()) / "gemini_video.mp4"
                    await download.save_as(str(tmp))
                    video_bytes = tmp.read_bytes()
                    logger.info(f"[PW] Downloaded: {len(video_bytes)//1024} KB")
                    break
            except Exception:
                pass

            remaining = int(deadline - asyncio.get_event_loop().time())
            logger.debug(f"[PW] Chờ video... còn {remaining}s")
            await asyncio.sleep(check_interval)

        # Nếu có URL nhưng chưa có bytes → tải về
        if not video_bytes and video_url and video_url.startswith("http"):
            logger.info(f"[PW] Tải video từ URL: {video_url[:80]}")
            try:
                import httpx
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(video_url)
                    if resp.status_code == 200 and len(resp.content) > 10000:
                        video_bytes = resp.content
                        logger.info(f"[PW] Tải OK: {len(video_bytes)//1024} KB")
            except Exception as e:
                logger.error(f"[PW] Tải URL thất bại: {e}")

        await browser.close()

        if video_bytes:
            logger.info(f"[PW] Hoàn thành. Video: {len(video_bytes)//1024} KB")
        else:
            logger.warning("[PW] Không lấy được video sau khi hết thời gian chờ")

        return video_bytes
