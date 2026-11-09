# Live-stream code-sniper → auto-redeem (YouTube + TikTok)

Watches a YouTube or TikTok LIVE and, the moment a redeem code appears, types it into the redeem page for you
in a browser that is already logged in and waiting.

```
 TikTok live ──┬─ chat (streamer's messages) ──┐
               ├─ video frames → OCR ──────────┼─► code detector ─► browser (fill + submit)
               └─ audio → Whisper (optional) ──┘     (dedupe, filters)   already logged in
```

Run it **on your own computer** (it needs a visible browser you can log in with).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
# ffmpeg must be on your PATH (needed for the video/audio sources)
cp config.example.yaml config.yaml                        # then edit it
```

1. Set `stream_url` (e.g. the YouTube live link) or `streamer` (TikTok username), and `redeem.url`, in `config.yaml`.
2. `python -m bot login` — a browser opens. Log in to the site by hand, go to the page with the code box,
   press Enter in the terminal. Your session is saved in `profile/`.
3. `python -m bot check` — confirms the code box is found. If not, set `redeem.code_input`
   (right-click the box in your browser → Inspect → copy a CSS selector).
4. `python -m bot test-code SOMEFAKECODE` — submits a fake code so you can see what a *failure* looks like,
   then set `success_selector` / `error_selector` so the bot can tell the outcomes apart.
5. `python -m bot run` — starts in **dry-run**: it prints every code it would have submitted but submits nothing.
   Watch a real live this way first and tighten `code_pattern` until it only fires on real codes.
6. When you trust it: `python -m bot run --mode auto` (or `confirm` to approve each one with Enter).

Tip: `python -m bot scan-text "the code is FREE4829XQ"` shows what the detector extracts from any text.

## YouTube notes
- Video/OCR goes through `yt-dlp`. YouTube changes often: if it fails to resolve the stream, run
  `pip install -U yt-dlp` first. YouTube may also ask for cookies/sign-in on some networks or streams
  (see the yt-dlp docs on `--cookies-from-browser`).
- Chat uses `pytchat` (no API key). Only the channel owner and moderators are trusted by default.
- YouTube live streams have their own delay (often 3–20 s). The bot sees the same delayed picture you would,
  so its advantage is reaction time, not seeing it earlier.
- If the code is only ever shown on screen, the OCR source is the one that matters. Set `ocr_region`
  to the part of the screen where the code appears to make it faster and more accurate.

## What makes it fast
- The browser is parked on the redeem page with the box located, so a code costs only fill + Enter (~40 ms in testing).
- Chat is near-instant. On-screen OCR adds roughly 0.5–1.5 s. Spoken codes are slowest and least reliable.

## Safety rails (on by default)
- Only the **streamer's own** chat messages are trusted (anyone else could post fake codes).
- A code is never submitted twice, and a code 1 character different from one already seen is skipped (OCR misreads).
- `max_attempts` hard cap per run, delay between attempts, stops after the first success.
- Screenshot of every attempt in `logs/screens/`, full log in `logs/bot.log`.

## Be aware
- **Not tested against the real TikTok/YouTube streams or the real redeem site** (the dev sandbox couldn't reach them). Tested here: the detector,
  the browser flow against a mock page, and ffmpeg → OCR → detect → redeem on a synthetic video. Expect to tweak selectors.
- TikTok's and YouTube's unofficial live APIs change often. If chat or the stream stops working, `pip install -U TikTokLive pytchat yt-dlp`.
- Automating a website or scraping TikTok may be against their terms. Read the redeem site's terms, use your own
  account only, and keep `max_attempts` low. Hammering wrong codes can get an account flagged.
- Don't run it headless: if the site shows a captcha or logs you out, you need to see it.
- OCR/ASR can mix up `0/O`, `1/I`, `5/S`, `8/B`. If your codes have an obvious format, encode it in `code_pattern`.

## Tests
```bash
pytest -q
```
