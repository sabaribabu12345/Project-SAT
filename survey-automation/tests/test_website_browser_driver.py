from __future__ import annotations

import asyncio

import pytest

from apps.api.settings import Settings
from apps.api.website_browser_driver import PlaywrightBrowserDriver

_TEST_HTML = """
<!DOCTYPE html>
<html>
<body>
  <label for="enroll">Total Undergraduate Enrollment</label>
  <input id="enroll" type="text" />

  <label for="inst_type">Institution Type</label>
  <select id="inst_type">
    <option value="">--</option>
    <option value="public">Public</option>
    <option value="private">Private</option>
  </select>

  <fieldset>
    <legend>Are responses posted online?</legend>
    <input type="radio" name="posted" value="yes" id="posted_yes"><label for="posted_yes">Yes</label>
    <input type="radio" name="posted" value="no" id="posted_no"><label for="posted_no">No</label>
  </fieldset>

  <button type="button" onclick="document.title='clicked'">Next</button>
</body>
</html>
"""


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def local_html_file(tmp_path):
    path = tmp_path / "form.html"
    path.write_text(_TEST_HTML, encoding="utf-8")
    return path


def test_playwright_driver_round_trip_against_local_html(local_html_file) -> None:
    """Validates the real PlaywrightBrowserDriver mechanics (connect_over_cdp,
    label extraction JS, fill/select/check, next-button click) against a real
    headless Chromium — not analyst login, just the CDP round trip itself."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright not installed")

    async def scenario():
        playwright = await async_playwright().start()
        try:
            # Simulates the "analyst already has a browser open" precondition:
            # launch a real (headless) Chromium exposing a CDP port, exactly
            # like the analyst's manually-launched --remote-debugging-port=9222
            # browser, then have our driver connect to it independently.
            browser2 = await playwright.chromium.launch(headless=True, args=["--remote-debugging-port=9377"])
            page2 = await browser2.new_page()
            await page2.goto(local_html_file.as_uri())

            settings = Settings(browser_remote_debugging_url="http://localhost:9377")
            driver = PlaywrightBrowserDriver(settings)
            await driver.connect()

            current_url = await driver.current_url()
            assert current_url.endswith("form.html")

            screenshot = await driver.screenshot()
            assert len(screenshot) > 0

            controls = await driver.list_form_controls()
            by_type = {c.control_type: c for c in controls}
            assert "textbox" in by_type
            assert "dropdown" in by_type
            assert "radio" in by_type

            await driver.fill_text(by_type["textbox"].control_id, "39435")
            await driver.select_dropdown_option(by_type["dropdown"].control_id, "public")
            yes_option = next(o for o in by_type["radio"].options if o.value == "yes")
            await driver.check_option(yes_option.control_id)

            enroll_value = await page2.eval_on_selector(by_type["textbox"].control_id, "el => el.value")
            inst_value = await page2.eval_on_selector(by_type["dropdown"].control_id, "el => el.value")
            radio_checked = await page2.eval_on_selector(yes_option.control_id, "el => el.checked")
            assert enroll_value == "39435"
            assert inst_value == "public"
            assert radio_checked is True

            clicked = await driver.click_next()
            assert clicked is True
            assert await page2.title() == "clicked"

            await driver.close()
            await browser2.close()
        finally:
            await playwright.stop()

    _run(scenario())
