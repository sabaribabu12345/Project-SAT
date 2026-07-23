from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from apps.api.settings import Settings

DEFAULT_NEXT_BUTTON_LABELS: list[str] = [
    "Next",
    "Continue",
    "Save & Continue",
    "Save and Continue",
    "Next Page",
    "Submit",
]


@dataclass(frozen=True)
class FormControlOption:
    control_id: str
    value: str
    label_text: str


@dataclass(frozen=True)
class FormControlInfo:
    control_id: str
    label_text: str
    control_type: str  # textbox/textarea/dropdown/radio/checkbox/date/number
    group_name: str = ""
    options: list[FormControlOption] = field(default_factory=list)


class WebsiteBrowserDriver(Protocol):
    async def current_url(self) -> str: ...

    async def screenshot(self) -> bytes: ...

    async def list_form_controls(self) -> list[FormControlInfo]: ...

    async def fill_text(self, control_id: str, value: str) -> None: ...

    async def select_dropdown_option(self, control_id: str, option_value: str) -> None: ...

    async def check_option(self, control_id: str) -> None: ...

    async def click_next(self, button_text: str | None = None) -> bool: ...

    async def close(self) -> None: ...


_LABEL_EXTRACTION_JS = """
() => {
  function labelForElement(el) {
    if (el.id) {
      const byFor = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (byFor && byFor.textContent.trim()) return byFor.textContent.trim();
    }
    const closestLabel = el.closest('label');
    if (closestLabel && closestLabel.textContent.trim()) return closestLabel.textContent.trim();
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) return ariaLabel.trim();
    const ariaLabelledby = el.getAttribute('aria-labelledby');
    if (ariaLabelledby) {
      const ref = document.getElementById(ariaLabelledby);
      if (ref && ref.textContent.trim()) return ref.textContent.trim();
    }
    const placeholder = el.getAttribute('placeholder');
    if (placeholder) return placeholder.trim();
    let sib = el.previousElementSibling;
    while (sib) {
      const text = sib.textContent && sib.textContent.trim();
      if (text) return text;
      sib = sib.previousElementSibling;
    }
    return '';
  }

  function groupLabelForElement(el) {
    const fieldset = el.closest('fieldset');
    if (fieldset) {
      const legend = fieldset.querySelector('legend');
      if (legend && legend.textContent.trim()) return legend.textContent.trim();
    }
    return '';
  }

  const controls = [];
  let autoIndex = 0;

  document.querySelectorAll('input, select, textarea').forEach(el => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    if (tag === 'input' && ['hidden', 'submit', 'button', 'file', 'image', 'reset'].includes(type)) return;
    if (el.disabled) return;

    if (!el.id) {
      el.setAttribute('data-wa-id', `wa-auto-${autoIndex++}`);
    }
    const controlId = el.id ? `#${CSS.escape(el.id)}` : `[data-wa-id="${el.getAttribute('data-wa-id')}"]`;

    if (tag === 'input' && (type === 'radio' || type === 'checkbox')) {
      controls.push({
        control_id: controlId,
        label_text: labelForElement(el),
        control_type: type,
        group_name: el.getAttribute('name') || '',
        group_label: groupLabelForElement(el),
        option_value: el.value || '',
      });
      return;
    }

    let controlType = 'textbox';
    if (tag === 'textarea') controlType = 'textarea';
    else if (tag === 'select') controlType = 'dropdown';
    else if (type === 'date') controlType = 'date';
    else if (type === 'number') controlType = 'number';

    const options = [];
    if (tag === 'select') {
      el.querySelectorAll('option').forEach(opt => {
        options.push({ value: opt.value, label_text: opt.textContent.trim() });
      });
    }

    controls.push({
      control_id: controlId,
      label_text: labelForElement(el),
      control_type: controlType,
      group_name: '',
      group_label: '',
      options,
    });
  });

  return controls;
}
"""


def _group_raw_controls(raw_controls: list[dict]) -> list[FormControlInfo]:
    """Convert the flat per-input JS output into FormControlInfo rows, merging
    radio/checkbox inputs that share a `name` into a single grouped control."""
    groups: dict[str, dict] = {}
    result: list[FormControlInfo] = []

    for raw in raw_controls:
        control_type = raw.get("control_type", "textbox")
        if control_type in ("radio", "checkbox"):
            group_name = raw.get("group_name") or raw.get("control_id")
            group = groups.setdefault(
                group_name,
                {"control_type": control_type, "group_label": "", "options": []},
            )
            if raw.get("group_label") and not group["group_label"]:
                group["group_label"] = raw["group_label"]
            group["options"].append(
                FormControlOption(
                    control_id=raw["control_id"],
                    value=raw.get("option_value", ""),
                    label_text=raw.get("label_text", ""),
                )
            )
            continue

        options = [
            FormControlOption(control_id="", value=opt.get("value", ""), label_text=opt.get("label_text", ""))
            for opt in raw.get("options", [])
        ]
        result.append(
            FormControlInfo(
                control_id=raw["control_id"],
                label_text=raw.get("label_text", ""),
                control_type=control_type,
                options=options,
            )
        )

    for group_name, group in groups.items():
        label = group["group_label"] or " / ".join(
            option.label_text for option in group["options"] if option.label_text
        )
        result.append(
            FormControlInfo(
                control_id="",
                label_text=label,
                control_type=group["control_type"],
                group_name=group_name,
                options=group["options"],
            )
        )

    return result


class PlaywrightBrowserDriver:
    """Drives an already-open, already-logged-in browser over CDP.

    Re-resolves the active page on every call (rather than caching a Page
    handle) so a manual tab switch by the analyst doesn't leave this pointed
    at a stale tab.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._playwright = None
        self._browser = None

    async def connect(self) -> None:
        from playwright.async_api import async_playwright

        cdp_url = (self._settings.browser_remote_debugging_url or "").strip()
        if not cdp_url:
            raise RuntimeError(
                "BROWSER_REMOTE_DEBUGGING_URL is not set. Launch Chrome/Edge with "
                "--remote-debugging-port=9222 --remote-allow-origins=* (see /website-ops "
                "for the exact command), then set BROWSER_REMOTE_DEBUGGING_URL."
            )
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)

    async def goto(self, url: str) -> None:
        page = await self._active_page()
        await page.goto(url, wait_until="load")

    async def _active_page(self):
        if self._browser is None:
            raise RuntimeError("Browser is not connected. Call connect() first.")
        contexts = self._browser.contexts
        if not contexts:
            raise RuntimeError("No browser context available — is a page open in the connected browser?")
        pages = contexts[-1].pages
        if not pages:
            raise RuntimeError("No open page found in the connected browser context.")
        return pages[-1]

    async def current_url(self) -> str:
        page = await self._active_page()
        return page.url

    async def screenshot(self) -> bytes:
        page = await self._active_page()
        return await page.screenshot(type="png")

    async def list_form_controls(self) -> list[FormControlInfo]:
        page = await self._active_page()
        raw_controls = await page.evaluate(_LABEL_EXTRACTION_JS)
        return _group_raw_controls(raw_controls)

    async def fill_text(self, control_id: str, value: str) -> None:
        page = await self._active_page()
        await page.fill(control_id, value)

    async def select_dropdown_option(self, control_id: str, option_value: str) -> None:
        page = await self._active_page()
        await page.select_option(control_id, value=option_value)

    async def check_option(self, control_id: str) -> None:
        page = await self._active_page()
        await page.check(control_id)

    async def click_next(self, button_text: str | None = None) -> bool:
        page = await self._active_page()
        labels = ([button_text] if button_text else []) + DEFAULT_NEXT_BUTTON_LABELS
        for label in labels:
            if not label:
                continue
            pattern = re.compile(re.escape(label), re.IGNORECASE)
            for locator in (page.get_by_role("button", name=pattern), page.locator(f"text={label}")):
                try:
                    count = await locator.count()
                except Exception:  # noqa: BLE001
                    count = 0
                if not count:
                    continue
                try:
                    await locator.first.click(timeout=3000)
                    await page.wait_for_load_state("load", timeout=8000)
                    return True
                except Exception:  # noqa: BLE001
                    continue
        return False

    async def close(self) -> None:
        # Deliberately do NOT call self._browser.close() here: this is a CDP
        # connection to a browser the analyst launched and is manually driving
        # (including login). Only detach the Playwright driver connection —
        # never terminate or close tabs on the analyst's real browser.
        if self._playwright is not None:
            await self._playwright.stop()
