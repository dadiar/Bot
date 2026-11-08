"""Drives a real, already-logged-in browser to enter a code as fast as possible.

The browser is launched once with a persistent profile (so you log in by hand a
single time), parked on the redeem page with the input already located. When a
code arrives the only work left is fill + submit.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import BrowserContext, Locator, Page, async_playwright

log = logging.getLogger("bot.redeemer")


@dataclass
class RedeemConfig:
    url: str
    code_input: str = (
        'input[name*="code" i], input[id*="code" i], input[placeholder*="code" i], '
        'input[name*="promo" i], input[placeholder*="promo" i]'
    )
    submit_button: str = ""  # empty => press Enter in the input
    success_selector: str = ""  # CSS or "text=..." that appears on success
    error_selector: str = ""  # CSS or "text=..." that appears on failure
    result_timeout_s: float = 6.0
    profile_dir: str = "profile"
    headless: bool = False
    browser_executable: str = ""  # optional path to a Chrome/Chromium binary
    screenshot_dir: str = "logs/screens"


@dataclass
class RedeemResult:
    code: str
    outcome: str  # "success" | "failure" | "submitted" | "error"
    detail: str = ""
    elapsed_ms: float = 0.0


class Redeemer:
    def __init__(self, cfg: RedeemConfig) -> None:
        self.cfg = cfg
        self._pw = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        self._pw = await async_playwright().start()
        kwargs: dict = dict(
            user_data_dir=str(Path(self.cfg.profile_dir).resolve()),
            headless=self.cfg.headless,
            viewport={"width": 1280, "height": 800},
        )
        if self.cfg.browser_executable:
            kwargs["executable_path"] = self.cfg.browser_executable
            kwargs["args"] = ["--no-sandbox"]
        self._ctx = await self._pw.chromium.launch_persistent_context(**kwargs)
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        await self.prepare()

    async def close(self) -> None:
        if self._ctx:
            await self._ctx.close()
        if self._pw:
            await self._pw.stop()

    @property
    def page(self) -> Page:
        assert self._page is not None, "Redeemer.start() not called"
        return self._page

    # -- readiness ----------------------------------------------------------
    def _input(self) -> Locator:
        return self.page.locator(self.cfg.code_input).first

    async def prepare(self, timeout_s: float = 20.0) -> bool:
        """Park on the redeem page with the code input visible. Returns readiness."""
        try:
            if not self.page.url.startswith(self.cfg.url.split("?")[0]):
                await self.page.goto(self.cfg.url, wait_until="domcontentloaded")
            await self._input().wait_for(state="visible", timeout=timeout_s * 1000)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Code input not found on %s (%s). Are you logged in? Is `code_input` right? "
                "Run `python -m bot check` to debug.", self.page.url, type(exc).__name__,
            )
            return False

    async def _stale_indicator(self) -> bool:
        """True if a previous attempt's success/error message is still on the page,
        which would otherwise be mistaken for the result of the next attempt."""
        for sel in (self.cfg.success_selector, self.cfg.error_selector):
            if sel and await self.page.locator(sel).count():
                return True
        return False

    async def _ensure_ready(self) -> bool:
        try:
            if await self._input().is_visible() and not await self._stale_indicator():
                return True
        except Exception:  # noqa: BLE001
            pass
        await self.page.goto(self.cfg.url, wait_until="domcontentloaded")
        return await self.prepare()

    # -- the hot path -------------------------------------------------------
    async def redeem(self, code: str) -> RedeemResult:
        async with self._lock:
            t0 = time.perf_counter()
            try:
                if not await self._ensure_ready():
                    return RedeemResult(code, "error", "redeem page/input not ready")
                box = self._input()
                await box.fill(code)
                if self.cfg.submit_button:
                    await self.page.locator(self.cfg.submit_button).first.click()
                else:
                    await box.press("Enter")
                submit_ms = (time.perf_counter() - t0) * 1000
                outcome, detail = await self._wait_outcome()
                await self._screenshot(code, outcome)
                result = RedeemResult(code, outcome, detail, submit_ms)
                if outcome == "failure":
                    # Reset the form now (inside the lock) so the next code starts clean.
                    await self._ensure_ready()
                return result
            except Exception as exc:  # noqa: BLE001
                log.exception("redeem crashed")
                return RedeemResult(code, "error", f"{type(exc).__name__}: {exc}")

    async def _wait_outcome(self) -> tuple[str, str]:
        ok, bad = self.cfg.success_selector, self.cfg.error_selector
        if not ok and not bad:
            await asyncio.sleep(1.0)  # give the page a moment so the screenshot is useful
            return "submitted", "no success/error selector configured; check the screenshot"
        deadline = time.monotonic() + self.cfg.result_timeout_s
        while time.monotonic() < deadline:
            if ok and await self.page.locator(ok).count():
                return "success", ok
            if bad and await self.page.locator(bad).count():
                return "failure", (await self.page.locator(bad).first.inner_text())[:200]
            await asyncio.sleep(0.05)
        return "submitted", "timed out waiting for success/error indicator"

    async def _screenshot(self, code: str, outcome: str) -> None:
        try:
            d = Path(self.cfg.screenshot_dir)
            d.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(d / f"{int(time.time())}_{code}_{outcome}.png"))
        except Exception:  # noqa: BLE001
            pass
