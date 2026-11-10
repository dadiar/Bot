"""Finds redeemable codes in text coming from chat, OCR'd video frames, or speech."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

DEFAULT_PATTERN = r"[A-Z0-9][A-Z0-9-]{5,19}"

# Words/brands that look like codes but never are. Extend via config.
DEFAULT_STOPWORDS = {
    "TOPSTEP", "TOPSTEPX", "TIKTOK", "FOLLOW", "FOLLOWERS", "SUBSCRIBE", "GIVEAWAY",
    "FREE", "EVAL", "EVALUATION", "ACCOUNT", "ACCOUNTS", "REDEEM", "PROMO", "COUPON",
    "CHECKOUT", "FUTURES", "TRADING", "TRADER", "COMBINE", "FUNDED", "LIVESTREAM",
}

_SPOKEN_DIGITS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_NATO = {
    "alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E",
    "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I", "juliet": "J", "kilo": "K",
    "lima": "L", "mike": "M", "november": "N", "oscar": "O", "papa": "P", "quebec": "Q",
    "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
    "whiskey": "W", "xray": "X", "yankee": "Y", "zulu": "Z",
}
_LETTER_WORDS = {  # how ASR tends to write spelled-out letters
    "ay": "A", "bee": "B", "be": "B", "see": "C", "sea": "C", "dee": "D", "eff": "F",
    "gee": "G", "aitch": "H", "jay": "J", "kay": "K", "ell": "L", "em": "M", "en": "N",
    "pea": "P", "pee": "P", "queue": "Q", "cue": "Q", "are": "R", "ess": "S", "tee": "T",
    "you": "U", "vee": "V", "ex": "X", "why": "Y", "zed": "Z",
}


def normalize_spoken(text: str, min_run: int = 4) -> str:
    """Turn 'A B C one two three' into 'ABC123' so spelled-out codes can be matched.

    Only runs of at least `min_run` single characters are collapsed; ordinary
    words are left alone. Spoken codes are inherently error-prone, treat as best effort.
    """
    out: list[str] = []
    run: list[tuple[str, str]] = []  # (original word, single-char replacement)

    def flush() -> None:
        nonlocal run
        if len(run) >= min_run:
            out.append("".join(conv for _, conv in run))
        else:
            out.extend(orig for orig, _ in run)  # too short to be a code: keep the words
        run = []

    for raw in re.findall(r"[A-Za-z0-9]+", text):
        w = raw.lower()
        if w in _SPOKEN_DIGITS:
            run.append((raw, _SPOKEN_DIGITS[w]))
        elif w in _NATO:
            run.append((raw, _NATO[w]))
        elif w in _LETTER_WORDS:
            run.append((raw, _LETTER_WORDS[w]))
        elif len(raw) == 1:
            run.append((raw, raw.upper()))
        else:
            flush()
            out.append(raw)
    flush()
    return " ".join(out)


@dataclass
class Candidate:
    code: str
    source: str  # "chat" | "ocr" | "audio"
    context: str = ""
    seen_at: float = field(default_factory=time.time)


class CodeDetector:
    def __init__(
        self,
        pattern: str = DEFAULT_PATTERN,
        require_digit: bool = True,
        require_letter: bool = False,
        min_length: int = 6,
        max_length: int = 20,
        submit_case: str = "upper",  # upper | lower | keep
        stopwords: set[str] | None = None,
        dedupe_window_s: float = 600.0,
    ) -> None:
        self._re = re.compile(rf"(?<![A-Za-z0-9-])(?:{pattern})(?![A-Za-z0-9-])", re.IGNORECASE)
        self.require_digit = require_digit
        self.require_letter = require_letter
        self.min_length = min_length
        self.max_length = max_length
        self.submit_case = submit_case
        self.stopwords = {w.upper() for w in (DEFAULT_STOPWORDS | (stopwords or set()))}
        self.dedupe_window_s = dedupe_window_s
        self._seen: dict[str, float] = {}

    # -- extraction ---------------------------------------------------------
    def extract(self, text: str) -> list[str]:
        found: list[str] = []
        for m in self._re.finditer(text):
            code = m.group(0).strip("-")
            if not (self.min_length <= len(code) <= self.max_length):
                continue
            if code.upper() in self.stopwords:
                continue
            if self.require_digit and not any(c.isdigit() for c in code):
                continue
            if self.require_letter and not any(c.isalpha() for c in code):
                continue
            code = self.apply_case(code)
            if code not in found:
                found.append(code)
        return found

    def apply_case(self, code: str) -> str:
        if self.submit_case == "upper":
            return code.upper()
        if self.submit_case == "lower":
            return code.lower()
        return code

    # -- dedupe -------------------------------------------------------------
    def _is_dup(self, code: str, now: float) -> bool:
        key = code.upper()
        for prev, ts in list(self._seen.items()):
            if now - ts > self.dedupe_window_s:
                del self._seen[prev]
                continue
            if prev == key:
                return True
            # Same length and one character off => almost certainly an OCR
            # misread of a code we already saw; don't burn another attempt on it.
            if len(prev) == len(key) and sum(a != b for a, b in zip(prev, key)) <= 1:
                return True
        return False

    def offer(self, text: str, source: str) -> list[Candidate]:
        """Extract codes from `text` and return only ones not seen recently."""
        now = time.time()
        fresh: list[Candidate] = []
        for code in self.extract(text):
            if self._is_dup(code, now):
                continue
            self._seen[code.upper()] = now
            fresh.append(Candidate(code=code, source=source, context=text[:200], seen_at=now))
        return fresh

    def forget(self, code: str) -> None:
        """Allow a code to be offered again (e.g. after a failed browser attempt)."""
        self._seen.pop(code.upper(), None)
