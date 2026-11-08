"""Things that listen to the stream. Each pushes text to `on_text(text, source)`."""
from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Awaitable, Callable

log = logging.getLogger("bot.sources")

OnText = Callable[[str, str], Awaitable[None]]


# --------------------------------------------------------------------------- chat
async def run_chat(username: str, trusted: list[str], on_text: OnText) -> None:
    """Watch TikTok live chat. Only messages from `trusted` users are scanned
    (default: the streamer) so trolls can't feed the bot fake codes. Use ["*"] for anyone.
    Reconnects forever, so you can start the bot before the streamer goes live.
    """
    from TikTokLive import TikTokLiveClient
    from TikTokLive.events import CommentEvent, ConnectEvent

    username = username.lstrip("@")
    allowed = {u.lstrip("@").lower() for u in trusted} or {username.lower()}
    anyone = "*" in allowed
    delay = 5.0

    while True:
        client = TikTokLiveClient(unique_id=f"@{username}")

        @client.on(ConnectEvent)
        async def _connected(_e: ConnectEvent) -> None:
            log.info("Connected to @%s's live chat", username)

        @client.on(CommentEvent)
        async def _comment(e: CommentEvent) -> None:
            uid = (getattr(e.user, "unique_id", "") or "").lower()
            if anyone or uid in allowed:
                await on_text(e.comment, "chat")

        try:
            task = await client.start()
            delay = 5.0
            await task
            log.info("Chat connection ended; reconnecting")
        except asyncio.CancelledError:
            await client.disconnect()
            raise
        except Exception as exc:  # noqa: BLE001  (offline, rate-limited, network blip...)
            log.info("Chat not connected (%s: %s); retrying in %.0fs", type(exc).__name__, exc, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 30.0)


# ------------------------------------------------------------------ stream URL
async def resolve_stream_url(username: str) -> str:
    """Ask yt-dlp for the live stream's direct (HLS/FLV) URL."""
    import yt_dlp

    def _go() -> str:
        opts = {"quiet": True, "no_warnings": True, "format": "best"}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.tiktok.com/@{username.lstrip('@')}/live", download=False)
            return info["url"]

    return await asyncio.to_thread(_go)


async def _stream_source(username: str, override: str | None) -> str:
    return override or await resolve_stream_url(username)


def _require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg not found on PATH (needed for video/audio watching)")
    return path


# --------------------------------------------------------------------------- OCR
def _split_jpegs(buf: bytearray) -> list[bytes]:
    frames = []
    while True:
        start = buf.find(b"\xff\xd8")
        if start < 0:
            buf.clear()
            break
        end = buf.find(b"\xff\xd9", start + 2)
        if end < 0:
            del buf[:start]
            break
        frames.append(bytes(buf[start : end + 2]))
        del buf[: end + 2]
    return frames


async def run_ocr(
    username: str,
    on_text: OnText,
    keywords: list[str],
    fps: float = 2.0,
    region: list[float] | None = None,
    stream_url: str | None = None,
) -> None:
    """Read on-screen text. A frame is only reported if it contains one of `keywords`
    (e.g. "code") so random UI text isn't treated as a code. Empty list = report all text.
    `region` = [x, y, w, h] as fractions of the frame to crop to (faster, less noise).
    """
    import numpy as np
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR
    import io

    ffmpeg = _require_ffmpeg()
    engine = RapidOCR()
    kws = [k.lower() for k in keywords]
    latest: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)

    def ocr_frame(jpeg: bytes) -> str:
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        if region:
            w, h = img.size
            x, y, rw, rh = region
            img = img.crop((int(x * w), int(y * h), int((x + rw) * w), int((y + rh) * h)))
        result, _ = engine(np.array(img))
        return "\n".join(r[1] for r in (result or []))

    async def consumer() -> None:
        while True:
            jpeg = await latest.get()
            text = await asyncio.to_thread(ocr_frame, jpeg)
            if text and (not kws or any(k in text.lower() for k in kws)):
                await on_text(text, "ocr")

    consumer_task = asyncio.create_task(consumer())
    try:
        while True:
            try:
                url = await _stream_source(username, stream_url)
                log.info("OCR: watching video stream")
                pace = ["-re"] if stream_url else []  # test files: play at real-time speed
                proc = await asyncio.create_subprocess_exec(
                    ffmpeg, "-loglevel", "error", *pace, "-i", url,
                    "-vf", f"fps={fps}", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "3", "-",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                buf = bytearray()
                assert proc.stdout
                while chunk := await proc.stdout.read(1 << 16):
                    buf += chunk
                    for jpeg in _split_jpegs(buf):
                        if latest.full():  # drop stale frame; we only care about the newest
                            latest.get_nowait()
                        latest.put_nowait(jpeg)
                await proc.wait()
                while not latest.empty():  # let the final frame get OCR'd before we stop
                    await asyncio.sleep(0.1)
                await asyncio.sleep(0.5)
                log.info("OCR: video stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.info("OCR: stream not available (%s: %s)", type(exc).__name__, exc)
            if stream_url:  # a fixed test input; don't loop forever
                return
            await asyncio.sleep(10)
    finally:
        consumer_task.cancel()


# ------------------------------------------------------------------------- audio
async def run_audio(
    username: str,
    on_text: OnText,
    model_size: str = "base",
    window_s: float = 6.0,
    step_s: float = 3.0,
    stream_url: str | None = None,
) -> None:
    """Transcribe the streamer's voice with a sliding window (so a code spoken across a
    chunk boundary is still caught). Spoken codes are error-prone; treat as best effort."""
    import numpy as np
    from faster_whisper import WhisperModel

    ffmpeg = _require_ffmpeg()
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    rate = 16000
    win, step = int(window_s * rate), int(step_s * rate)

    def transcribe(pcm: bytes) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segs, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=True)
        return " ".join(s.text for s in segs).strip()

    while True:
        try:
            url = await _stream_source(username, stream_url)
            log.info("ASR: listening to stream audio")
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-loglevel", "error", *(["-re"] if stream_url else []), "-i", url,
                "-vn", "-ac", "1", "-ar", str(rate),
                "-f", "s16le", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            buf = bytearray()
            since_last = 0
            assert proc.stdout
            while chunk := await proc.stdout.read(rate * 2 // 2):
                buf += chunk
                since_last += len(chunk) // 2
                if len(buf) // 2 > win:
                    del buf[: len(buf) - win * 2]
                if since_last >= step and len(buf) // 2 >= rate * 2:
                    since_last = 0
                    text = await asyncio.to_thread(transcribe, bytes(buf))
                    if text:
                        await on_text(text, "audio")
            await proc.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("ASR: stream not available (%s: %s)", type(exc).__name__, exc)
        if stream_url:
            return
        await asyncio.sleep(10)
