# Live code bot

Watches a YouTube live. When the streamer shows a code on screen (or types it in chat, or says it),
the bot reads it and claims it **instantly in all 4 of your account tabs at once**
(2k flex, 2k pro, 50k flex, 50k pro). Whichever account the code is for gets claimed.

It runs on **your computer**, because it uses your logged-in browser.

## Setup (once)

You need Python and ffmpeg installed. Then:

```bash
git clone -b ccr-aa7e36e8-2p1a4x https://github.com/dadiar/Bot
cd Bot
pip install -r requirements.txt
playwright install chromium
cp config.example.yaml config.yaml
```

Open `config.yaml` and paste in:
- the YouTube live link
- the redeem page link for each of your 4 accounts

Then log in once:

```bash
python -m bot login
```

A browser opens with your 4 tabs. Log in to TopStep, then press Enter in the terminal.
Your login is saved, so you don't do this again.

## Every stream

```bash
python -m bot run
```

Leave it running. That's it. When a code appears you'll see:

```
*** CODE DETECTED via ocr: FREE4829XQ
REDEEM FREE4829XQ [2k flex]  -> FAILURE (Invalid code)
REDEEM FREE4829XQ [2k pro]   -> FAILURE (Invalid code)
REDEEM FREE4829XQ [50k flex] -> SUCCESS
REDEEM FREE4829XQ [50k pro]  -> FAILURE (Invalid code)
*** SUCCESS — FREE4829XQ claimed on: 50k flex
```

**When he makes you guess the code:** type your guess in the terminal and press Enter.
The bot fires it into all 4 tabs instantly. You do the guessing, the bot does the fast typing.

Stop with **Ctrl+C**.

## If something doesn't work

| Problem | Fix |
|---|---|
| "code box not found" | Run `python -m bot check`. It screenshots each tab into `logs/`. If you're logged out, run `login` again. If the box isn't found, set `code_input` in `config.yaml` (right-click the box → Inspect). |
| Can't open the YouTube stream | `pip install -U yt-dlp` (YouTube changes often). |
| It detects random text as a code | Set `ocr_region` to the part of the screen where he shows codes. |
| It's not sure if a claim worked | Run `python -m bot test-code FAKE123` to see what a failure looks like, then set `success_selector` / `error_selector`. |

Every attempt is screenshotted to `logs/screens/` and logged to `logs/bot.log`.

## Good to know
- YouTube lives have a few seconds of delay. The bot sees the same picture you do; it wins by reacting in under a second.
- Only the streamer's and moderators' chat messages count, so trolls can't trigger it with fake codes.
- It never submits the same code twice. It's capped at `max_attempts` codes per run so a misread can't spam TopStep and get your account flagged.
- Check TopStep's terms. Automating the site is your call, on your own account.
- Tested here against mock redeem pages and a fake stream video. I couldn't reach YouTube or TopStep from my environment, so the first real run is the real test.

## Also works with TikTok
Use `streamer: their_username` instead of `stream_url`.

## Tests
`pytest -q`
