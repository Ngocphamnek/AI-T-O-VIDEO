"""Wrapper tích hợp Gemini thông qua gemini-webapi."""
import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional
from loguru import logger

_client = None
_lock = asyncio.Lock()


async def get_client():
    """Lấy hoặc khởi tạo GeminiClient (singleton)."""
    global _client
    if _client is not None:
        return _client
    async with _lock:
        if _client is not None:
            return _client
        from gemini_webapi import GeminiClient
        from bot.store import get_gemini_psid, get_gemini_psidts
        psid = get_gemini_psid()
        psidts = get_gemini_psidts()
        if not psid:
            raise RuntimeError("GEMINI_PSID chưa được cấu hình")
        logger.info("Initializing GeminiClient...")
        client = GeminiClient(
            secure_1psid=psid,
            secure_1psidts=psidts or None,
        )
        await client.init(timeout=300, watchdog_timeout=45, auto_close=False, auto_refresh=True)
        _client = client
        logger.info("GeminiClient initialized OK")
        return _client


async def chat(
    prompt: str,
    conversation_id: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> dict:
    """
    Gửi tin nhắn đến Gemini.
    Trả về dict: {text, images, conversation_id, reply_id, rc_id}
    """
    client = await get_client()
    try:
        if conversation_id and reply_to:
            response = await client.generate_content(
                prompt,
                conversation_id=conversation_id,
                reply_to=reply_to,
            )
        else:
            response = await client.generate_content(prompt)

        images = []
        if hasattr(response, "images") and response.images:
            images = response.images

        return {
            "text": response.text or "",
            "images": images,
            "conversation_id": getattr(response, "conversation_id", None),
            "reply_id": getattr(response, "reply_id", None),
            "rc_id": getattr(response, "rc_id", None),
        }
    except Exception as e:
        logger.error(f"Gemini chat error: {e}")
        raise


async def _generate_image_pollinations(prompt: str) -> list[bytes]:
    """
    Fallback: tạo ảnh qua Pollinations AI (miễn phí, không cần API key).
    """
    import httpx
    from urllib.parse import quote
    url = (
        f"https://image.pollinations.ai/prompt/{quote(prompt)}"
        "?nologo=true&width=1280&height=720&model=flux&seed=-1"
    )
    logger.info(f"[Pollinations] Generating image: {prompt[:60]}...")
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.content
        if not data or len(data) < 1000:
            raise RuntimeError("Pollinations trả về ảnh rỗng hoặc quá nhỏ")
        logger.info(f"[Pollinations] OK ({len(data)//1024} KB)")
        return [data]


async def generate_image(
    prompt: str,
    input_image_path: Optional[str] = None,
) -> list[bytes]:
    """
    Tạo hoặc chỉnh sửa ảnh với Gemini.
    Nếu Gemini thất bại, tự động dùng Pollinations AI làm fallback.
    Trả về danh sách bytes của ảnh.
    """
    # Nếu có ảnh input, chỉ Gemini hỗ trợ — không có fallback
    if input_image_path:
        client = await get_client()
        try:
            response = await client.generate_content(
                prompt,
                files=[input_image_path],
                image_generation=True,
            )
            image_bytes_list = []
            if hasattr(response, "images") and response.images:
                for img in response.images:
                    if hasattr(img, "to_file"):
                        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                            tmp_path = f.name
                        await img.to_file(tmp_path)
                        with open(tmp_path, "rb") as f:
                            image_bytes_list.append(f.read())
                        Path(tmp_path).unlink(missing_ok=True)
                    elif hasattr(img, "bytes") and img.bytes:
                        image_bytes_list.append(img.bytes)
            return image_bytes_list
        except Exception as e:
            logger.error(f"Gemini image (with input) error: {e}")
            raise

    # Không có ảnh input — thử Gemini trước, fallback Pollinations
    try:
        client = await get_client()
        response = await client.generate_content(
            prompt,
            image_generation=True,
        )
        image_bytes_list = []
        if hasattr(response, "images") and response.images:
            for img in response.images:
                if hasattr(img, "to_file"):
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                        tmp_path = f.name
                    await img.to_file(tmp_path)
                    with open(tmp_path, "rb") as f:
                        image_bytes_list.append(f.read())
                    Path(tmp_path).unlink(missing_ok=True)
                elif hasattr(img, "bytes") and img.bytes:
                    image_bytes_list.append(img.bytes)
        if image_bytes_list:
            return image_bytes_list
        # Gemini không trả về ảnh → fallback
        logger.warning("[Image] Gemini không trả về ảnh, chuyển sang Pollinations AI")
        return await _generate_image_pollinations(prompt)
    except Exception as e:
        logger.warning(f"[Image] Gemini thất bại ({e}), chuyển sang Pollinations AI")
        return await _generate_image_pollinations(prompt)


async def generate_video(prompt: str) -> list[bytes]:
    """
    Tạo video bằng Gemini Veo qua tài khoản đã đăng nhập.
    Chỉ cần thêm 'tạo video' vào đầu prompt là Gemini tự nhận và tạo.
    Trả về danh sách bytes của video MP4.
    """
    import tempfile, shutil
    client = await get_client()

    full_prompt = f"tạo video: {prompt}"
    logger.info(f"[Video] Sending to Gemini: {full_prompt[:100]}...")

    try:
        response = await client.generate_content(full_prompt)
        logger.info(f"[Video] Response text: {(response.text or '')[:200]}")
        logger.info(f"[Video] Videos in response: {len(response.videos)}")

        if not response.videos:
            logger.warning("[Video] Gemini không trả về video nào")
            return []

        tmp_dir = Path(tempfile.mkdtemp(prefix="gemvid_"))
        try:
            video_bytes_list = []
            for i, vid in enumerate(response.videos):
                logger.info(f"[Video] Downloading video {i+1}/{len(response.videos)}")
                result = await vid.save(path=str(tmp_dir), filename=f"video_{i}")
                vid_path = result.get("video") if result else None
                if vid_path and Path(vid_path).exists():
                    data = Path(vid_path).read_bytes()
                    video_bytes_list.append(data)
                    logger.info(f"[Video] Video {i+1} OK ({len(data)//1024} KB)")
            return video_bytes_list
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    except Exception as e:
        logger.warning(f"[Video] Gemini video thất bại: {e} — sẽ dùng fallback")
        return []


async def analyze_url(url: str, prompt: str = "Phân tích nội dung này chi tiết bằng tiếng Việt.") -> str:
    """Phân tích URL (YouTube, bài viết, v.v.) bằng Gemini."""
    client = await get_client()
    try:
        full_prompt = f"{prompt}\n\n{url}"
        response = await client.generate_content(full_prompt)
        return response.text or "(Không có phản hồi)"
    except Exception as e:
        logger.error(f"Gemini URL analysis error: {e}")
        raise


async def generate_video_from_frames(
    frame_prompts: list[str],
    seconds_per_frame: float = 4.0,
    on_frame=None,
) -> bytes:
    """
    Tạo video MP4 từ danh sách mô tả cảnh.
    Mỗi prompt → 1 ảnh Gemini → ghép thành MP4 bằng ffmpeg.
    on_frame(i, total): callback async gọi sau mỗi frame.
    """
    import shutil
    import subprocess
    import asyncio

    tmp_dir = Path(tempfile.mkdtemp(prefix="autovid_"))
    try:
        frame_paths: list[str] = []
        total = len(frame_prompts)

        for i, prompt in enumerate(frame_prompts):
            logger.info(f"[Frames] Generating frame {i+1}/{total}: {prompt[:60]}...")
            try:
                image_list = await generate_image(prompt)
            except Exception as e:
                logger.warning(f"[Frames] Frame {i+1} failed: {e}, skipping")
                if on_frame:
                    await on_frame(i + 1, total, error=True)
                continue

            if not image_list:
                logger.warning(f"[Frames] Frame {i+1} returned no image, skipping")
                if on_frame:
                    await on_frame(i + 1, total, error=True)
                continue

            frame_path = tmp_dir / f"frame_{i:03d}.png"
            frame_path.write_bytes(image_list[0])
            frame_paths.append(str(frame_path))
            logger.info(f"[Frames] Frame {i+1}/{total} saved ({len(image_list[0])//1024} KB)")

            if on_frame:
                await on_frame(i + 1, total, error=False)

        if not frame_paths:
            raise RuntimeError("Không tạo được frame nào — kiểm tra lại Gemini cookies.")

        # Ghi concat list cho ffmpeg
        concat_file = tmp_dir / "concat.txt"
        with open(concat_file, "w") as f:
            for fp in frame_paths:
                f.write(f"file '{fp}'\n")
                f.write(f"duration {seconds_per_frame}\n")
            # ffmpeg concat cần lặp frame cuối
            f.write(f"file '{frame_paths[-1]}'\n")

        output_path = tmp_dir / "output.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-vf", (
                "scale=1280:720:force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,"
                "fps=24,format=yuv420p"
            ),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            str(output_path),
        ]
        proc = await asyncio.get_event_loop().run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, text=True)
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg thất bại:\n{proc.stderr[-400:]}")

        video_bytes = output_path.read_bytes()
        logger.info(f"[Frames] Video assembled: {len(video_bytes)//1024} KB from {len(frame_paths)} frames")
        return video_bytes

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def reset_client():
    """Reset GeminiClient (dùng khi gặp lỗi xác thực)."""
    global _client
    async with _lock:
        if _client:
            try:
                await _client.close()
            except Exception:
                pass
        _client = None
    return await get_client()
