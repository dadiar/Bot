"""Drives a real Chromium against a local mock "redeem" page."""
import asyncio
import os
import textwrap

import pytest

from bot.redeemer import RedeemConfig, Redeemer

CHROMIUM = "/opt/pw-browsers/chromium"
pytestmark = pytest.mark.skipif(not os.path.exists(CHROMIUM), reason="no chromium available")

PAGE = textwrap.dedent("""
    <html><body>
      <h1>Redeem</h1>
      <form id="f" onsubmit="return go(event)">
        <input name="promoCode" placeholder="Enter code">
        <button id="apply" type="submit">Apply</button>
      </form>
      <div id="out"></div>
      <script>
        function go(e) {
          e.preventDefault();
          const v = document.querySelector('input').value;
          setTimeout(() => {
            document.getElementById('out').innerHTML = v === 'GOOD1234'
              ? '<p class="ok">Account redeemed!</p>'
              : '<p class="err">Invalid code</p>';
          }, 80);
        }
      </script>
    </body></html>
""")


@pytest.fixture
def cfg(tmp_path):
    page = tmp_path / "redeem.html"
    page.write_text(PAGE)
    return RedeemConfig(
        url=page.as_uri(), success_selector=".ok", error_selector=".err",
        profile_dir=str(tmp_path / "profile"), headless=True,
        browser_executable=CHROMIUM, screenshot_dir=str(tmp_path / "shots"),
    )


def run(coro):
    return asyncio.run(coro)


def test_bad_then_good_code(cfg):
    async def go():
        r = Redeemer(cfg)
        await r.start()
        try:
            bad = await r.redeem("NOPE0000")
            good = await r.redeem("GOOD1234")  # form must have been reset after the failure
            return bad, good
        finally:
            await r.close()

    bad, good = run(go())
    assert bad.outcome == "failure" and "Invalid" in bad.detail
    assert good.outcome == "success"
    assert good.elapsed_ms < 1000  # fill+submit is fast (excludes waiting for the site)


def test_click_submit_button(cfg):
    cfg.submit_button = "#apply"

    async def go():
        r = Redeemer(cfg)
        await r.start()
        try:
            return await r.redeem("GOOD1234")
        finally:
            await r.close()

    assert run(go()).outcome == "success"


def test_unverified_when_no_result_selectors(cfg):
    cfg.success_selector = cfg.error_selector = ""

    async def go():
        r = Redeemer(cfg)
        await r.start()
        try:
            return await r.redeem("GOOD1234")
        finally:
            await r.close()

    assert run(go()).outcome == "submitted"


def test_missing_input_reports_error(cfg):
    cfg.code_input = "#does-not-exist"

    async def go():
        r = Redeemer(cfg)
        await r.start()  # logs a warning, doesn't raise
        try:
            return await r.redeem("GOOD1234")
        finally:
            await r.close()

    res = run(go())
    assert res.outcome == "error"
