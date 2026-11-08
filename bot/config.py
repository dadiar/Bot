from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .redeemer import RedeemConfig


@dataclass
class Config:
    streamer: str
    mode: str = "dry-run"  # dry-run | confirm | auto
    max_attempts: int = 5  # hard cap on codes submitted per run (protects your account)
    min_seconds_between_attempts: float = 1.0

    # sources
    chat_enabled: bool = True
    trusted_chat_users: list[str] = field(default_factory=list)  # [] = streamer only, ["*"] = anyone
    ocr_enabled: bool = True
    ocr_fps: float = 2.0
    ocr_keywords: list[str] = field(default_factory=lambda: ["code", "promo", "redeem", "coupon"])
    ocr_region: list[float] | None = None
    audio_enabled: bool = False
    audio_keywords: list[str] = field(default_factory=lambda: ["code", "promo", "redeem", "coupon"])
    whisper_model: str = "base"

    # detector
    code_pattern: str = r"[A-Z0-9][A-Z0-9-]{5,19}"
    require_digit: bool = True
    require_letter: bool = False
    min_length: int = 6
    max_length: int = 20
    submit_case: str = "upper"
    extra_stopwords: list[str] = field(default_factory=list)

    redeem: RedeemConfig = field(default_factory=lambda: RedeemConfig(url=""))


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    redeem_raw = raw.pop("redeem", {}) or {}
    cfg = Config(**raw, redeem=RedeemConfig(**redeem_raw))
    if cfg.mode not in {"dry-run", "confirm", "auto"}:
        raise ValueError("mode must be dry-run, confirm or auto")
    if not cfg.redeem.url:
        raise ValueError("redeem.url is required")
    return cfg
