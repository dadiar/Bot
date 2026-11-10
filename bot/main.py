"""CLI:  python -m bot run | login | check | test-code CODE"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
import time
from pathlib import Path

from .config import Config, load_config
from .detector import Candidate, CodeDetector, normalize_spoken
from .redeemer import Redeemer
from . import sources

log = logging.getLogger("bot")


def setup_logging() -> None:
    Path("logs").mkdir(exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO, format=fmt, datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler("logs/bot.log")],
    )


def make_detector(cfg: Config) -> CodeDetector:
    return CodeDetector(
        pattern=cfg.code_pattern, require_digit=cfg.require_digit, require_letter=cfg.require_letter,
        min_length=cfg.min_length, max_length=cfg.max_length, submit_case=cfg.submit_case,
        stopwords=set(cfg.extra_stopwords),
    )


async def _confirm(code: str) -> bool:
    prompt = f"\a>>> Redeem {code}? [Y/n] "
    ans = await asyncio.to_thread(input, prompt)
    return ans.strip().lower() in ("", "y", "yes")


async def run(cfg: Config, args: argparse.Namespace) -> None:
    detector = make_detector(cfg)
    redeemer = Redeemer(cfg.redeem)
    queue: asyncio.Queue[Candidate] = asyncio.Queue()
    attempts = 0
    manual = 0

    async def on_text(text: str, source: str) -> None:
        if source == "audio":
            if not any(k in text.lower() for k in cfg.audio_keywords):
                return
            text = normalize_spoken(text)
        for cand in detector.offer(text, source):
            print(f"\a*** CODE DETECTED via {source}: {cand.code}   (context: {cand.context[:80]!r})", flush=True)
            queue.put_nowait(cand)

    if cfg.mode != "dry-run":
        await redeemer.start()
    else:
        log.info("DRY-RUN: codes will be detected and printed but NOT submitted")

    async def worker() -> None:
        nonlocal attempts, manual
        while True:
            cand = await queue.get()
            if cfg.mode == "dry-run":
                continue
            is_manual = cand.source == "manual"
            if is_manual:
                if manual >= cfg.max_manual_entries:
                    log.warning("max_manual_entries (%d) reached; ignoring %s", cfg.max_manual_entries, cand.code)
                    continue
                manual += 1
            else:
                if attempts >= cfg.max_attempts:
                    log.warning("max_attempts (%d) reached; ignoring %s", cfg.max_attempts, cand.code)
                    continue
                if cfg.mode == "confirm" and not await _confirm(cand.code):
                    continue
                attempts += 1
            lag = (time.time() - cand.seen_at) * 1000
            results = await redeemer.redeem(cand.code)
            for res in results:
                log.info("REDEEM %s [%s] -> %s (%s) [detect->submit %.0f ms, fill+submit %.0f ms]",
                         res.code, res.account, res.outcome.upper(), res.detail, lag, res.elapsed_ms)
            won = [r.account for r in results if r.outcome == "success"]
            if won:
                print(f"\n*** SUCCESS — {cand.code} claimed on: {', '.join(won)}\n", flush=True)
                if cfg.stop_after_success:
                    return
            elif not is_manual and all(r.outcome in ("failure", "error") for r in results):
                detector.forget(cand.code)  # allow a corrected re-read to try again
            await asyncio.sleep(cfg.min_seconds_between_attempts)

    def start_manual_reader() -> None:
        """You type a code (e.g. your guess) + Enter; it is fired into every tab at once.
        Daemon thread so Ctrl+C never hangs waiting for a line."""
        loop = asyncio.get_running_loop()

        def read() -> None:
            for line in sys.stdin:
                code = detector.apply_case(line.strip())
                if code:
                    loop.call_soon_threadsafe(queue.put_nowait, Candidate(code=code, source="manual"))

        threading.Thread(target=read, daemon=True).start()
        print(">>> Type a code and press Enter to fire it into all tabs.", flush=True)

    page_url = cfg.stream_url or sources.tiktok_live_url(cfg.streamer)
    yt_id = sources.youtube_video_id(page_url) if "youtu" in page_url else None
    log.info("Watching %s", page_url)

    tasks = [asyncio.create_task(worker())]
    # Typing assist only in auto mode (confirm mode already uses the terminal), and only
    # from a real terminal so it can't be fed a list of guesses from a file or pipe.
    if cfg.mode == "auto" and sys.stdin.isatty():
        start_manual_reader()
    if cfg.chat_enabled:
        if yt_id:
            tasks.append(asyncio.create_task(sources.run_youtube_chat(yt_id, cfg.trusted_chat_users, on_text)))
        elif "tiktok" in page_url:
            tasks.append(asyncio.create_task(
                sources.run_chat(cfg.streamer, cfg.trusted_chat_users, on_text)))
    if cfg.ocr_enabled:
        tasks.append(asyncio.create_task(sources.run_ocr(
            page_url, on_text, cfg.ocr_keywords, cfg.ocr_fps, cfg.ocr_region, args.test_input)))
    if cfg.audio_enabled:
        tasks.append(asyncio.create_task(sources.run_audio(
            page_url, on_text, cfg.whisper_model, stream_url=args.test_input)))
    log.info("Running in %s mode. Ctrl+C to stop.", cfg.mode.upper())
    try:
        # Runs until Ctrl+C (or the first success, if stop_after_success is on).
        await tasks[0]
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await redeemer.close()


async def login(cfg: Config) -> None:
    """Open the browser with the persistent profile so you can log in once by hand."""
    cfg.redeem.headless = False
    r = Redeemer(cfg.redeem)
    await r.start()
    print(f"\n{len(r.tabs)} tab(s) opened. Log in to the site in the browser window,")
    print("then come back here and press Enter to save the session and close it.")
    await asyncio.to_thread(input)
    await r.close()


async def check(cfg: Config) -> None:
    r = Redeemer(cfg.redeem)
    await r.start()
    for tab in r.tabs:
        ready = await tab.prepare(timeout_s=10)
        shot = f"logs/check_{''.join(c if c.isalnum() else '_' for c in tab.spec.name)}.png"
        await tab.page.screenshot(path=shot)
        print(f"[{tab.spec.name}] {tab.page.url}\n    code box found: {ready}   screenshot: {shot}")
    await asyncio.sleep(3)
    await r.close()


async def test_code(cfg: Config, code: str) -> None:
    r = Redeemer(cfg.redeem)
    await r.start()
    for res in await r.redeem(code):
        print(res)
    await r.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="bot")
    p.add_argument("command", choices=["run", "login", "check", "test-code", "scan-text"])
    p.add_argument("arg", nargs="?", help="code (test-code) or text (scan-text)")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("--mode", choices=["dry-run", "confirm", "auto"], help="override config mode")
    p.add_argument("--test-input", help="video/audio file or URL to use instead of the live stream")
    args = p.parse_args()

    setup_logging()
    cfg = load_config(args.config)
    if args.mode:
        cfg.mode = args.mode
    try:
        if args.command == "run":
            asyncio.run(run(cfg, args))
        elif args.command == "login":
            asyncio.run(login(cfg))
        elif args.command == "check":
            asyncio.run(check(cfg))
        elif args.command == "test-code":
            asyncio.run(test_code(cfg, args.arg or sys.exit("usage: test-code CODE")))
        elif args.command == "scan-text":
            det = make_detector(cfg)
            print(det.extract(args.arg or ""))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
