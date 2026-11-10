"""Drives a real, already-logged-in browser to enter a code as fast as possible.

The browser is launched once with a persistent profile (so you log in by hand a
single time). One tab is parked on each account's redeem page with the code box
already located. When a code arrives it is filled + submitted in every tab at the
same time, so whichever account the code belongs to gets claimed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import BrowserContext, Locator, Page, async_playwright

log = logging.getLogger("bot.redeemer")

DEFAULT_CODE_INPUT = (
    'input[name*="code" i], input[id*="code" i], input[placeholder*="code" i], '
    'input[name*="promo" i], input[placeholder*="promo" i]'
)


@dataclass
class AccountTab:
    """One account type (e.g. "50k flex") = one browser tab on its redeem page.
    Any selector left empty falls back to the shared one in RedeemConfig."""
    name: str
    url: str
    code_input: str = ""
    submit_button: str = ""
    success_selector: str = ""
    error_selector: str = ""


@dataclass
class RedeemConfig:
    url: str = ""  # used when `accounts` is empty (single tab)
    accounts: list[AccountTab] = field(default_factory=list)
    code_input: str = DEFAULT_CODE_INPUT
    submit_button: str = ""  # empty => press Enter in the input
    success_selector: str = ""  # CSS or "text=..." that appears on success
    error_selector: str = ""  # CSS or "text=..." that appears on failure
    result_timeout_s: float = 6.0
    profile_dir: str = "profile"
    headless: bool = False
    browser_executable: str = ""  # optional path to a Chrome/Chromium binary
    screenshot_dir: str = "logs/screens"

    def tabs(self) -> list[AccountTab]:
        """Every tab to open, with shared selectors filled in where a tab left them empty."""
        raw = self.accounts or [AccountTab(name="default", url=self.url)]
        return [
            AccountTab(
                name=a.name,
                url=a.url,
                code_input=a.code_input or self.code_input,
                submit_button=a.submit_button or self.submit_button,
                success_selector=a.success_selector or self.success_selector,
                error_selector=a.error_selector or self.error_selector,
            )
            for a in raw
        ]


@dataclass
class RedeemResult:
    code: str
    outcome: str  # "success" | "failure" | "submitted" | "error"
    detail: str = ""
    elapsed_ms: float = 0.0
    account: str = "default"


class _Tab:
    """A single parked tab. All the per-page logic lives here."""

    def __init__(self, spec: AccountTab, page: Page, cfg: RedeemConfig) -> None:
        self.spec = spec
        self.page = page
        self.cfg = cfg
        self.lock = asyncio.Lock()

    def _input(self) -> Locator:
        return self.page.locator(self.spec.code_input).first

    async def prepare(self, timeout_s: float = 20.0) -> bool:
        try:
            if not self.page.url.startswith(self.spec.url.split("?")[0]):
                await self.page.goto(self.spec.url, wait_until="domcontentloaded")
            await self._input().wait_for(state="visible", timeout=timeout_s * 1000)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[%s] code box not found on %s (%s). Logged in? Is code_input right? "
                "Run `python -m bot check`.", self.spec.name, self.page.url, type(exc).__name__,
            )
            return False

    async def _stale_indicator(self) -> bool:
        for sel in (self.spec.success_selector, self.spec.error_selector):
            if sel and await self.page.locator(sel).count():
                return True
        return False

    async def ensure_ready(self) -> bool:
        try:
            if await self._input().is_visible() and not await self._stale_indicator():
                return True
        except Exception:  # noqa: BLE001
            pass
        await self.page.goto(self.spec.url, wait_until="domcontentloaded")
        return await self.prepare()

    async def redeem(self, code: str) -> RedeemResult:
        name = self.spec.name
        async with self.lock:
            t0 = time.perf_counter()
            try:
                if not await self.ensure_ready():
                    return RedeemResult(code, "error", "page/code box not ready", account=name)
                box = self._input()
                await box.fill(code)
                if self.spec.submit_button:
                    await self.page.locator(self.spec.submit_button).first.click()
                else:
                    await box.press("Enter")
                submit_ms = (time.perf_counter() - t0) * 1000
                outcome, detail = await self._wait_outcome()
                await self._screenshot(code, outcome)
                if outcome == "failure":
                    await self.ensure_ready()  # reset so the next code starts clean
                return RedeemResult(code, outcome, detail, submit_ms, account=name)
            except Exception as exc:  # noqa: BLE001
                log.exception("[%s] redeem crashed", name)
                return RedeemResult(code, "error", f"{type(exc).__name__}: {exc}", account=name)

    async def _wait_outcome(self) -> tuple[str, str]:
        ok, bad = self.spec.success_selector, self.spec.error_selector
        if not ok and not bad:
            await asyncio.sleep(1.0)
            return "submitted", "no success/error selector set; check the screenshot"
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
            safe = "".join(c if c.isalnum() else "_" for c in self.spec.name)
            await self.page.screenshot(path=str(d / f"{int(time.time())}_{safe}_{code}_{outcome}.png"))
        except Exception:  # noqa: BLE001
            pass


class Redeemer:
    def __init__(self, cfg: RedeemConfig) -> None:
        self.cfg = cfg
        self._pw = None
        self._ctx: BrowserContext | None = None
        self.tabs: list[_Tab] = []

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

        specs = self.cfg.tabs()
        pages = list(self._ctx.pages)
        while len(pages) < len(specs):
            pages.append(await self._ctx.new_page())
        self.tabs = [_Tab(s, p, self.cfg) for s, p in zip(specs, pages)]
        # Park every tab on its page in parallel.
        await asyncio.gather(*(t.prepare() for t in self.tabs))

    async def close(self) -> None:
        # On Ctrl+C the browser driver receives the signal too and may already be gone;
        # that's fine, there is nothing left to clean up.
        for closer in (self._ctx and self._ctx.close, self._pw and self._pw.stop):
            if closer:
                try:
                    await closer()
                except Exception:  # noqa: BLE001
                    pass
        self._ctx = self._pw = None

    @property
    def page(self) -> Page:
        """The first tab (used by `login`/`check`)."""
        assert self.tabs, "Redeemer.start() not called"
        return self.tabs[0].page

    async def prepare(self) -> bool:
        results = await asyncio.gather(*(t.prepare() for t in self.tabs))
        return all(results)

    async def redeem(self, code: str) -> list[RedeemResult]:
        """Fire one code into every account tab at once."""
        return list(await asyncio.gather(*(t.redeem(code) for t in self.tabs)))
