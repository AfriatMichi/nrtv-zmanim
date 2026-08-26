/*
 * In-page extractor for https://nrtv-production.up.railway.app/
 *
 * The site is a React SPA whose DOM carries NO classes and NO ids - every node
 * is a bare <div>. So we cannot use CSS selectors. Instead we read the page the
 * way a human does: locate the Hebrew section heading, then collect the
 * label/value rows that live under it.
 *
 * This file must evaluate to a single arrow function - Playwright hands it
 * straight to page.evaluate().
 */
() => {
  const SECTION_HEADERS = [
    'המניין הבא',
    'הודעות',
    'זמני תפילות',
    'שיעורי תורה',
    'לעילוי נשמת',
    'זמני היום',
  ];

  const root = document.getElementById('root') || document.body;

  // Text belonging directly to an element, ignoring text inside its children.
  const ownText = (el) =>
    [...el.childNodes]
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent.trim())
      .filter(Boolean)
      .join(' ');

  const all = () => [...root.querySelectorAll('*')];

  const findByOwnText = (text) => all().find((el) => ownText(el) === text) || null;

  // A value cell is either a clock time or one of the site's textual times
  // ("מיד לאחר מנחה", "20 דקות לפני השקיעה", ...).
  const isTimeValue = (s) =>
    /^\d{1,2}:\d{2}(:\d{2})?$/.test(s) ||
    /^(מיד|כ?־?\s*\d+\s*דק|לפני|אחרי|עם )/.test(s);

  /*
   * Walk up from a heading as far as possible WITHOUT swallowing a different
   * section's heading. The result is that section's own container.
   */
  const sectionFor = (headerText) => {
    const h = findByOwnText(headerText);
    if (!h) return null;
    let el = h;
    while (el.parentElement && el.parentElement !== document.body) {
      const p = el.parentElement;
      const swallowsOther = SECTION_HEADERS.some(
        (s) => s !== headerText && [...p.querySelectorAll('*')].some((x) => ownText(x) === s)
      );
      if (swallowsOther) break;
      el = p;
    }
    return el;
  };

  /*
   * A "row" is any element with exactly two element children where the second
   * one is a leaf holding a time value. Covers prayer rows, day-times rows and
   * shiur rows (whose first child holds title + rabbi on two lines).
   */
  const harvest = (container) => {
    if (!container) return [];
    const rows = [];
    const seen = new Set();
    for (const el of container.querySelectorAll('*')) {
      const kids = [...el.children];
      if (kids.length !== 2) continue;
      if (kids[1].children.length) continue; // value must be a leaf
      const time = kids[1].innerText.trim();
      const label = kids[0].innerText.trim();
      if (!time || !label || !isTimeValue(time)) continue;
      const key = label + '|' + time;
      if (seen.has(key)) continue;
      seen.add(key);
      const parts = label.split('\n').map((s) => s.trim()).filter(Boolean);
      const row = { name: parts[0], time };
      if (parts.length > 1) row.subtitle = parts.slice(1).join(' ');
      rows.push(row);
    }
    return rows;
  };

  // ---- header -------------------------------------------------------------
  const headerLines = [...root.querySelectorAll('*')]
    .map(ownText)
    .filter(Boolean);
  const synagogue = headerLines.find((t) => t.includes('בית כנסת')) || null;
  const dateLine =
    all().map((el) => el.innerText && el.innerText.trim()).find(
      (t) => t && /^יום .+·.+\d{4}$/.test(t.split('\n')[0])
    ) || null;
  let weekday = null;
  let hebrewDate = null;
  if (dateLine) {
    const [d, h] = dateLine.split('\n')[0].split('·').map((s) => s.trim());
    weekday = d || null;
    hebrewDate = h || null;
  }

  // ---- next minyan --------------------------------------------------------
  let nextMinyan = null;
  const nextHeader = findByOwnText('המניין הבא');
  if (nextHeader && nextHeader.parentElement) {
    const sibs = [...nextHeader.parentElement.children];
    const i = sibs.indexOf(nextHeader);
    const name = sibs[i + 1] ? sibs[i + 1].innerText.trim() : null;
    const time = sibs[i + 2] ? sibs[i + 2].innerText.trim() : null;
    const countdown = sibs[i + 3] ? sibs[i + 3].innerText.replace(/\s+/g, ' ').trim() : null;
    if (name && time) nextMinyan = { name, time, countdown };
  }

  // ---- announcements ------------------------------------------------------
  const announcements = [];
  const annHeader = findByOwnText('הודעות');
  if (annHeader && annHeader.nextElementSibling) {
    annHeader.nextElementSibling.innerText
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
      .forEach((s) => announcements.push(s));
  }

  // ---- prayer times (active tab) -----------------------------------------
  const prayersSection = sectionFor('זמני תפילות');
  let dayLabel = null;
  if (prayersSection) {
    const heading = findByOwnText('זמני תפילות');
    const headRow = heading ? heading.parentElement : null;
    const after = headRow && headRow.parentElement ? headRow.parentElement.children[1] : null;
    if (after && !after.querySelector('*')) dayLabel = after.innerText.trim();
    else if (after) dayLabel = ownText(after) || null;
  }
  const activeTab =
    [...root.querySelectorAll('button')]
      .filter((b) => ['חול', 'שבת'].includes(b.innerText.trim()))
      .find((b) => b.getAttribute('aria-selected') === 'true' || b.dataset.state === 'active');

  return {
    synagogue,
    weekday,
    hebrewDate,
    nextMinyan,
    announcements,
    activeTab: activeTab ? activeTab.innerText.trim() : null,
    prayers: { dayLabel, items: harvest(prayersSection) },
    // innerText of the prayer-times block only. Used to detect that the tab
    // switch actually landed - the page's live countdown sits outside it, so
    // this string is stable until the schedule itself changes.
    prayersText: prayersSection ? prayersSection.innerText.trim() : '',
    dayTimes: harvest(sectionFor('זמני היום')),
    shiurim: harvest(sectionFor('שיעורי תורה')),
  };
}
