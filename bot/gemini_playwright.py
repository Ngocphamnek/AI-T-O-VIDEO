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


def _setup_playwright_lib_path() -> None:
    """
    Lấy đường dẫn thư viện hệ thống từ nix store cho Chromium headless shell.
    Dùng nix-build để lấy store path chính xác, sau đó set LD_LIBRARY_PATH.
    """
    # Các nix packages cần thiết cho Chromium
    nix_packages = [
        "nspr", "nss", "glib", "atk", "at-spi2-atk", "at-spi2-core",
        "dbus", "mesa", "expat", "libxkbcommon", "eudev", "alsa-lib",
        "xorg.libX11", "xorg.libXcomposite", "xorg.libXdamage",
        "xorg.libXext", "xorg.libXfixes", "xorg.libXrandr",
        "xorg.libxcb", "pango", "cairo", "libdrm", "libGL",
    ]

    lib_dirs: list[str] = []
    for pkg in nix_packages:
        try:
            result = subprocess.run(
                ["nix-build", "<nixpkgs>", "-A", pkg, "--no-out-link"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                store_path = result.stdout.strip()
                lib_path = f"{store_path}/lib"
                if os.path.isdir(lib_path):
                    lib_dirs.append(lib_path)
        except Exception:
            pass

    if lib_dirs:
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        parts = lib_dirs + ([existing] if existing else [])
        os.environ["LD_LIBRARY_PATH"] = ":".join(parts)
        logger.info(f"[PW] LD_LIBRARY_PATH: {len(lib_dirs)} nix lib dirs configured")
    else:
        logger.warning("[PW] Không tìm thấy nix libs — Chromium có thể crash")


# Chạy một lần khi module được import
_setup_playwright_lib_path()


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
        browser = await p.chromium.launch(
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
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        # ── Đặt cookies Gemini ────────────────────────────────────────
        cookies = [
            {
                "name": "__Secure-1PSID",
                "value": psid,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "None",
            },
        ]
        if psidts:
            cookies.append({
                "name": "__Secure-1PSIDTS",
                "value": psidts,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "None",
            })
        await context.add_cookies(cookies)

        page = await context.new_page()

        # ── Bắt video từ network responses ───────────────────────────
        async def on_response(response):
            nonlocal video_bytes, video_url
            if video_bytes:
                return
            ct = response.headers.get("content-type", "")
            url = response.url
            if ("video" in ct or url.endswith(".mp4") or "videoplayback" in url or "GeneratedVideo" in url):
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
        await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(4)

        # Kiểm tra đã đăng nhập chưa
        url_now = page.url
        logger.info(f"[PW] URL sau load: {url_now}")
        if "accounts.google.com" in url_now or "signin" in url_now:
            logger.error("[PW] Bị redirect sang trang đăng nhập — cookies không hợp lệ")
            await browser.close()
            return None

        # ── Tìm và kích hoạt Video mode ───────────────────────────────
        video_mode_activated = False

        # Cách 1: Tìm nút "+" / "More" / "Attach" gần textarea
        for selector in [
            '[aria-label*="Add content"]',
            '[aria-label*="Thêm nội dung"]',
            '[data-test-id="attachment-button"]',
            'button[aria-label*="plus"]',
            '.add-content-button',
            'mat-icon-button[mattooltip*="video" i]',
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await asyncio.sleep(1)
                    logger.info(f"[PW] Clicked: {selector}")
                    video_mode_activated = True
                    break
            except Exception:
                pass

        if video_mode_activated:
            # Tìm option "Video" trong menu vừa mở
            for sel in ['text="Video"', 'text="Create video"', '[aria-label*="video" i]', 'li:has-text("video")']:
                try:
                    opt = page.locator(sel).first
                    if await opt.is_visible(timeout=2000):
                        await opt.click()
                        await asyncio.sleep(1)
                        logger.info(f"[PW] Chọn Video mode: {sel}")
                        break
                except Exception:
                    pass
        else:
            logger.info("[PW] Không tìm thấy nút video mode — gửi prompt text trực tiếp")

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
