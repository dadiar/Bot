"""End-to-end: synthetic 'stream' video -> ffmpeg -> OCR -> detector."""
import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from bot import sources
from bot.detector import CodeDetector

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and Path(FONT).exists()), reason="needs ffmpeg + DejaVu font"
)


def make_video(path: Path) -> None:
    def dt(text, y, cond):
        return (f"drawtext=fontfile={FONT}:text='{text}':fontsize=40:fontcolor=white:"
                f"x=30:y={y}:enable='{cond}'")
    vf = ",".join([
        dt("chat is moving fast", 200, "lt(t,2)"),
        dt("TODAYS CODE\\: FREE4829XQ", 600, "gte(t,2)"),
    ])
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x202030:s=720x1280:d=6:r=25",
         "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def test_code_shown_on_screen_is_detected_once(tmp_path):
    video = tmp_path / "stream.mp4"
    make_video(video)
    det, got = CodeDetector(), []

    async def on_text(text, source):
        got.extend(c.code for c in det.offer(text, source))

    asyncio.run(sources.run_ocr("streamer", on_text, ["code"], fps=2, stream_url=str(video)))
    # Shown for ~4s => seen in many frames, but must be submitted exactly once.
    assert got == ["FREE4829XQ"]


def test_frame_without_keyword_is_ignored(tmp_path):
    video = tmp_path / "stream.mp4"
    make_video(video)
    got = []

    async def on_text(text, source):
        got.append(text)

    # Keyword that never appears => nothing reported even though text is on screen.
    asyncio.run(sources.run_ocr("streamer", on_text, ["giveaway"], fps=2, stream_url=str(video)))
    assert got == []
