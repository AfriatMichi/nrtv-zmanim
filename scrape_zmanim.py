#!/usr/bin/env python3
"""
Scrape the prayer times off https://nrtv-production.up.railway.app/ the way a
visitor sees them: a real Chromium renders the React app, we read the painted
DOM, click the שבת tab and read it again. Nothing about how the site computes
its zmanim is reproduced or guessed here.

Output:
    data/zmanim.json    the current zmanim, overwritten in place
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

# Hebrew output must survive a non-UTF-8 console (Windows cp1252).
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

URL = "https://nrtv-production.up.railway.app/"
TZ = ZoneInfo("Asia/Jerusalem")

ROOT = Path(__file__).resolve().parent
EXTRACTOR = (ROOT / "extract.js").read_text(encoding="utf-8")
DATA_DIR = ROOT / "data"

# The app paints progressively; give the zmanim time to land.
LOAD_TIMEOUT_MS = 45_000
TAB_SWITCH_TIMEOUT_MS = 10_000
POLL_INTERVAL_MS = 250

# The הודעות block is a carousel: it rotates every ~8s and React keeps only
# the active slide in the DOM, so a single read sees just one announcement.
# Watch it through a full rotation to collect them all.
CAROUSEL_MAX_MS = 45_000
CAROUSEL_INTERVAL_MS = 1_000


def wait_until_painted(page: Page) -> None:
    """Block until the DOM actually contains clock times, not just a shell."""
    page.wait_for_function(
        r"() => /\d{1,2}:\d{2}/.test(document.body.innerText)"
        " && document.body.innerText.includes('זמני תפילות')",
        timeout=LOAD_TIMEOUT_MS,
    )


def collect_announcements(page: Page) -> list[str]:
    """Watch the announcements carousel through one full rotation.

    Returns every distinct line, in the order the slides first appeared.
    Stops early once the carousel wraps back to the slide it started on.
    """
    slides: list[str] = []
    first: str | None = None
    saw_another = False
    waited = 0

    while waited < CAROUSEL_MAX_MS:
        lines = page.evaluate(EXTRACTOR).get("announcements") or []
        slide = "\n".join(lines)

        if slide:
            if slide not in slides:
                slides.append(slide)
            if first is None:
                first = slide
            elif slide != first:
                saw_another = True
            elif saw_another:
                break  # wrapped around - we have seen the whole carousel

        page.wait_for_timeout(CAROUSEL_INTERVAL_MS)
        waited += CAROUSEL_INTERVAL_MS

    # The starting point in the rotation varies run to run, so sort the
    # slides into a stable order - otherwise the array would reshuffle every
    # time and look like a change worth committing.
    slides.sort()
    return [line for slide in slides for line in slide.split("\n") if line.strip()]


# "🌅  אשמורת הבוקר: 04:45 בבוקר" -> ("אשמורת הבוקר", "04:45")
_TIMED_LINE = re.compile(r"^(?P<name>.+?):\s*(?P<time>\d{1,2}:\d{2})")
_LEADING_JUNK = re.compile(r"^[^\w֐-׿]+")


def parse_selichot(announcements: list[str]) -> list[dict]:
    """Pull the selichot minyanim out of the announcement text.

    They are published as free text in the הודעות carousel rather than as rows
    in the prayer table, so they need their own pass. Seasonal: outside
    Elul/Tishrei the slide is simply absent and this returns [].
    """
    out: list[dict] = []
    for line in announcements:
        if "סליחות" not in line and "אשמורת" not in line:
            continue
        m = _TIMED_LINE.match(line.strip())
        if not m:
            continue
        name = _LEADING_JUNK.sub("", m.group("name")).strip()
        if name:
            out.append({"name": name, "time": m.group("time")})
    return out


def read_tab(page: Page, tab: str, previous_text: str) -> dict:
    """Click a tab (חול / שבת) and return the snapshot once the rows changed.

    We poll the prayer block's own text rather than the whole body: the page
    renders a live countdown that ticks every second, so body text always
    differs and would make any change-detection fire immediately.
    """
    page.get_by_role("button", name=tab, exact=True).click()

    deadline = TAB_SWITCH_TIMEOUT_MS
    snapshot = page.evaluate(EXTRACTOR)
    while deadline > 0 and snapshot.get("prayersText", "") == previous_text:
        page.wait_for_timeout(POLL_INTERVAL_MS)
        deadline -= POLL_INTERVAL_MS
        snapshot = page.evaluate(EXTRACTOR)

    if snapshot.get("prayersText", "") == previous_text:
        print(f"warning: the '{tab}' tab did not change the prayer times", file=sys.stderr)

    return snapshot


def scrape() -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="he-IL",
            timezone_id="Asia/Jerusalem",
            # The site is responsive and the NARROW layout is the richer one:
            # it renders all 10 זמני היום rows (the wide layout shows only 5)
            # and its חול/שבת tabs are real <button> elements.
            viewport={"width": 430, "height": 1400},
        )
        page = context.new_page()
        page.goto(URL, wait_until="networkidle", timeout=LOAD_TIMEOUT_MS)
        wait_until_painted(page)

        # Default tab is חול - read it as-is, without clicking anything.
        weekday = page.evaluate(EXTRACTOR)
        announcements = collect_announcements(page)

        shabbat: dict | None = None
        try:
            shabbat = read_tab(page, "שבת", previous_text=weekday.get("prayersText", ""))
        except PlaywrightTimeout:
            print("warning: could not open the שבת tab", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - the שבת tab is a nice-to-have
            print(f"warning: שבת tab failed: {exc}", file=sys.stderr)

        html_len = page.evaluate("() => document.documentElement.outerHTML.length")
        context.close()
        browser.close()

    now = datetime.now(TZ)
    return {
        "source": URL,
        "scraped_at": now.isoformat(timespec="seconds"),
        "synagogue": weekday.get("synagogue"),
        "date": {
            "gregorian": now.date().isoformat(),
            "weekday": weekday.get("weekday"),
            "hebrew": weekday.get("hebrewDate"),
        },
        "next_minyan": weekday.get("nextMinyan"),
        "announcements": announcements,
        "selichot": parse_selichot(announcements),
        "prayers": {
            "chol": {
                "day_label": weekday.get("prayers", {}).get("dayLabel"),
                "items": weekday.get("prayers", {}).get("items", []),
            },
            "shabbat": {
                "day_label": (shabbat or {}).get("prayers", {}).get("dayLabel"),
                "items": (shabbat or {}).get("prayers", {}).get("items", []),
            },
        },
        "day_times": weekday.get("dayTimes", []),
        "shiurim": weekday.get("shiurim", []),
        "_meta": {"rendered_html_bytes": html_len},
    }


def write(payload: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    (DATA_DIR / "zmanim.json").write_text(text, encoding="utf-8")


def main() -> int:
    payload = scrape()

    if not payload["prayers"]["chol"]["items"]:
        print("error: no prayer times found - the page layout probably changed", file=sys.stderr)
        return 1

    write(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
