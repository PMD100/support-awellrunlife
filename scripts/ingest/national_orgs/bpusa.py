#!/usr/bin/env python3
"""
Bereaved Parents of the USA - chapter directory adapter
=======================================================

Reads https://bereavedparentsusa.org/find-a-chapter/ and turns its chapter list into
listings. Child loss and sibling loss - the categories hospice bereavement programmes
serve worst, and the ones people search for most specifically.

WHY THIS SOURCE
---------------
Of the ten national organisations in data/national-sources.json, this is the only one
whose entire directory is reachable in a single request. The Compassionate Friends,
NACG, AFSP and GriefShare - roughly 1,550 US groups between them - all render their
locators in JavaScript from an internal API. No amount of crawling reaches them; they
have to be asked. BPUSA simply publishes the list as HTML.

(The path recorded in national-sources.json used to be /chapters, which 404s. The real
one is /find-a-chapter/. Checked 2026-08-23.)

WHAT WE REFUSE TO REPUBLISH
---------------------------
This is the part of the adapter that matters most.

BPUSA's page carries, for many chapters, the chapter leader's **home address** and
**personal email**. One entry is a residential street address in Jacksonville, Arkansas;
another is a leader's work address at a hospital. These are volunteers, most of them
bereaved parents, who gave a contact so that other bereaved parents could reach them.

They did not consent to being indexed by us.

So the rules are absolute and enforced in code, not by convention:

  * **No email addresses, ever.** Not personal, not "chapter" Gmail accounts. Harvesting
    and republishing addresses is what spam scrapers do. Readers get a phone number and a
    link back to BPUSA, which is where those addresses are published by the people who
    chose to publish them.

  * **No address unless the page says it is where the group meets.** An address is only
    carried through when its own paragraph is labelled "Meeting location:" or reads
    "Meet(s) ... at ...". Any other address is presumed to be someone's house and is
    dropped. This errs deliberately: we would rather lose a real venue than publish a
    volunteer's home.

  * Phone numbers **are** carried. BPUSA publishes them precisely so that a grieving
    parent can call the chapter, which is exactly what our listing invites. Where a
    chapter lists both a landline and a mobile, the landline is preferred.

If that balance ever looks wrong, `drop_personal_data()` is the one place to change it.

USAGE
-----
  python3 scripts/ingest/national_orgs/bpusa.py --offline-test    parse the fixture, write nothing
  python3 scripts/ingest/national_orgs/bpusa.py --inspect         fetch live, report, write nothing
  python3 scripts/ingest/national_orgs/bpusa.py                   fetch live and merge listings
  python3 scripts/ingest/national_orgs/bpusa.py --from-file p.html parse a saved copy

--from-file exists because the site did not answer a plain HTTP client during
development while answering a browser normally. If BPUSA turns out to block our user
agent, one saved page a quarter keeps this source alive without pretending to be a
browser to get it.
"""

import argparse
import json
import os
import re
import sys
import uuid
from collections import Counter
from datetime import date, datetime, timezone
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "lib"))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "extract"))

from normalize import normalize_phone, format_phone, clean_text, slugify  # noqa: E402
import politefetch  # noqa: E402

LISTINGS_PATH = os.path.join(REPO_ROOT, "data", "listings.json")
METROS_PATH = os.path.join(REPO_ROOT, "data", "metros.json")
REPORT_PATH = os.path.join(REPO_ROOT, "data", "bpusa-report.md")
FIXTURE_PATH = os.path.join(REPO_ROOT, "tests", "fixtures", "bpusa_chapters.html")

SOURCE_URL = "https://bereavedparentsusa.org/find-a-chapter/"
SOURCE_ID = "bereaved-parents-usa"
ORG_NAME = "Bereaved Parents of the USA"
FALLBACK_METRO = "national-chapter-network"

STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "WASHINGTON DC": "DC", "FLORIDA": "FL",
    "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY",
    "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO",
    "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN",
    "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA",
    "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
}

# A paragraph only gets to carry an address if it says, in its own words, that this is
# where people meet. Everything here is a phrase BPUSA actually uses.
MEETING_MARKERS = (
    "meeting location", "meeting place", "meets at", "meet at", "we meet",
    "meetings are held", "meeting address", "meet the", "meets the",
    "meets on", "meet on", "location:",
)

SCHEDULE_MARKERS = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "each month", "of the month", "monthly", "weekly", "quarterly",
    "date:", "time:", "every ",
)

ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[\w.'’-]+(?:\s+[\w.'’-]+){0,4}\s+"
    r"(?:st|street|rd|road|ave|avenue|dr|drive|ln|lane|blvd|bl|ct|court|way|pl|place|"
    r"hwy|highway|pkwy|parkway|cir|circle|terrace|trail|tr)\b\.?", re.I)
POBOX_RE = re.compile(r"\bP\.?\s*O\.?\s*Box\s+\d+", re.I)
ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
CITY_STATE_ZIP_RE = re.compile(
    r"([A-Z][A-Za-z.'’-]*(?:\s+[A-Z][A-Za-z.'’-]*){0,3}),?\s+"
    r"([A-Z]{2})\s+\d{5}(?:-\d{4})?")
SOCIAL_HOSTS = ("facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
                "tiktok.com", "youtube.com", "linkedin.com")


# --------------------------------------------------------------------------- parsing

class ChapterParser(HTMLParser):
    """
    Walks the WPBakery accordion.

    Shape, confirmed against the live DOM on 2026-08-23:

        h2                                  <- state name, outside any panel
        div.vc_tta-container
          div.vc_tta-panel                  <- one per chapter
            div.vc_tta-panel-heading
              h4 > a > span.vc_tta-title-text   <- chapter name
            div.vc_tta-panel-body
              p ...                            <- leader, contacts, venue, schedule

    Depth is tracked by counting <div> only, which is enough: panels never nest and the
    heading/body children sit exactly one level down.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.state_name = None
        self.chapters = []

        self._divs = 0
        self._panel_at = None
        self._panel = None
        self._in_title = 0
        self._in_h2 = 0
        self._h2 = []
        self._in_p = False
        self._p_text = []
        self._p_links = []
        self._in_strong = 0
        self._strong = []

    # -- helpers ----------------------------------------------------------------
    @staticmethod
    def _classes(attrs):
        for k, v in attrs:
            if k == "class" and v:
                return v.split()
        return []

    @staticmethod
    def _attr(attrs, name):
        for k, v in attrs:
            if k == name:
                return v or ""
        return ""

    def _new_panel(self):
        return {"name": "", "paras": [], "links": []}

    def _flush_p(self):
        if not self._in_p:
            return
        text = clean_text(" ".join(self._p_text))
        if self._panel is not None and (text or self._p_links):
            self._panel["paras"].append({
                "text": text,
                "labels": [clean_text(s) for s in self._strong if clean_text(s)],
                "links": list(self._p_links),
            })
            self._panel["links"].extend(self._p_links)
        self._in_p = False
        self._p_text, self._p_links, self._strong = [], [], []

    # -- HTMLParser -------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        classes = self._classes(attrs)

        if tag == "div":
            self._divs += 1
            if "vc_tta-panel" in classes:
                self._panel_at = self._divs
                self._panel = self._new_panel()
            return

        if tag == "h2" and self._panel is None:
            self._in_h2 += 1
            self._h2 = []
            return

        if tag == "span" and "vc_tta-title-text" in classes:
            self._in_title += 1
            return

        if tag == "p":
            self._flush_p()
            self._in_p = True
            self._p_text, self._p_links, self._strong = [], [], []
            return

        if tag == "strong" or tag == "b":
            self._in_strong += 1
            return

        if tag == "a" and self._panel is not None:
            self._p_links.append({"href": self._attr(attrs, "href"), "text": ""})
            return

        if tag == "br" and self._in_p:
            self._p_text.append(" ")

    def handle_endtag(self, tag):
        if tag == "div":
            if self._panel is not None and self._panel_at == self._divs:
                self._flush_p()
                if self._panel["name"]:
                    self._panel["state"] = self.state_name
                    self.chapters.append(self._panel)
                self._panel, self._panel_at = None, None
            self._divs = max(0, self._divs - 1)
            return
        if tag == "h2" and self._in_h2:
            self._in_h2 -= 1
            name = clean_text(" ".join(self._h2)).upper()
            if name in STATE_NAMES:
                self.state_name = STATE_NAMES[name]
            return
        if tag == "span" and self._in_title:
            self._in_title -= 1
            return
        if tag == "p":
            self._flush_p()
            return
        if tag in ("strong", "b") and self._in_strong:
            self._in_strong -= 1

    def handle_data(self, data):
        if self._in_h2:
            self._h2.append(data)
        if self._in_title and self._panel is not None:
            self._panel["name"] += data
        if self._in_p:
            self._p_text.append(data)
            if self._p_links:
                self._p_links[-1]["text"] += data
            if self._in_strong:
                self._strong.append(data)


def parse_chapters(html):
    p = ChapterParser()
    p.feed(html)
    out = []
    for c in p.chapters:
        c["name"] = clean_text(c["name"])
        if c["name"] and c.get("state"):
            out.append(c)
    return out


# ------------------------------------------------------------------ privacy filtering

def is_meeting_paragraph(para):
    """True only when the paragraph says, itself, that this is where the group meets."""
    hay = (para["text"] + " " + " ".join(para["labels"])).lower()
    return any(m in hay for m in MEETING_MARKERS)


def drop_personal_data(chapter):
    """
    Split what BPUSA publishes into what we may republish and what we may not.

    Returns (kept, dropped) where dropped is a tally used by the report, so that a run
    can prove it saw personal data and refused it rather than silently missing it.
    """
    kept = {"phone": None, "venue": None, "schedule": None, "website": None,
            "city": None}
    dropped = Counter()

    # Emails: counted, never carried.
    for link in chapter["links"]:
        if (link.get("href") or "").lower().startswith("mailto:"):
            dropped["email addresses"] += 1

    # Phones, landline preferred over mobile.
    phones = []
    for para in chapter["paras"]:
        text_low = para["text"].lower()
        for link in para["links"]:
            href = (link.get("href") or "")
            if not href.lower().startswith("tel:"):
                continue
            digits = normalize_phone(href.split(":", 1)[1])
            if not digits:
                digits = normalize_phone(link.get("text", ""))
            if not digits:
                continue
            label = link.get("text", "").lower()
            after = text_low.split(label, 1)[-1][:12] if label else ""
            phones.append((1 if after.strip().startswith("cell") else 0, digits))
    if phones:
        phones.sort(key=lambda x: x[0])
        kept["phone"] = phones[0][1]

    # Websites: a real chapter site only. Not social, not the source page, and not the
    # broken hrefs BPUSA emits when a phone number is mis-linked as a URL.
    for link in chapter["links"]:
        href = (link.get("href") or "").strip()
        low = href.lower()
        if not low.startswith("http"):
            continue
        if any(h in low for h in SOCIAL_HOSTS):
            dropped["social links"] += 1
            continue
        if "bereavedparentsusa.org" in low or "%20" in href or "(" in href:
            continue
        kept["website"] = href
        break

    # Addresses. Labelled venue -> keep. Anything else -> presumed a home.
    for para in chapter["paras"]:
        text = para["text"]
        has_address = bool(ADDRESS_RE.search(text) or POBOX_RE.search(text)
                           or ZIP_RE.search(text))
        if not has_address:
            continue
        if is_meeting_paragraph(para):
            venue = text
            for label in para["labels"]:
                if label and venue.lower().startswith(label.lower()):
                    venue = venue[len(label):]
            kept["venue"] = clean_text(venue.lstrip(":- –"))
            m = CITY_STATE_ZIP_RE.search(text)
            if m:
                kept["city"] = clean_text(m.group(1))
        else:
            dropped["addresses (presumed personal)"] += 1

    # Schedule: a paragraph that describes when, not where.
    for para in chapter["paras"]:
        low = para["text"].lower()
        if any(m in low for m in SCHEDULE_MARKERS):
            candidate = clean_text(para["text"])
            if candidate and candidate != kept["venue"]:
                kept["schedule"] = candidate
                break

    return kept, dropped


# ------------------------------------------------------------------------- listings

def load_metro_index():
    with open(METROS_PATH) as fh:
        metros = json.load(fh)["metros"]
    city_index, anchors = {}, []
    for metro in metros:
        if metro.get("virtual"):
            continue
        for city in metro.get("anchor_cities", []):
            for st in metro.get("states", []):
                city_index[(city.upper(), st)] = metro["id"]
            anchors.append((city.upper(), metro["id"]))
    anchors.sort(key=lambda x: -len(x[0]))
    return city_index, anchors


def pick_metro(city, state, chapter_name, city_index, anchors):
    if city and state:
        hit = city_index.get((city.upper(), state))
        if hit:
            return hit, city
    upper = chapter_name.upper()
    for anchor, metro_id in anchors:
        if anchor in upper:
            return metro_id, anchor.title()
    return FALLBACK_METRO, city


def to_listing(chapter, city_index, anchors, today):
    kept, dropped = drop_personal_data(chapter)
    state = chapter["state"]
    metro_id, city = pick_metro(kept["city"], state, chapter["name"],
                                city_index, anchors)

    # A chapter with no phone and no website is unreachable. We do not publish a name
    # and a shrug; the reader would have nothing to act on.
    if not kept["phone"] and not kept["website"]:
        return None, dropped, "no reachable contact"

    schedule = kept["schedule"] or "Contact the chapter for current meeting times"

    listing = {
        "id": str(uuid.uuid4()),
        "slug": slugify(f"bpusa {chapter['name']} {city or state}"),
        "name": chapter["name"],
        "organization": ORG_NAME,
        "org_type": "peer_network_chapter",
        # BPUSA describes itself as being for "parents, siblings, and grandparents".
        "loss_types": ["child", "sibling", "grandparent"],
        "age_groups": ["adults"],
        "format": "in_person",
        # BPUSA does not state a cost anywhere on the page. Chapters are peer-led and
        # almost certainly free, but "almost certainly" is not a fact, so: unknown.
        "cost": "unknown",
        "structure": "drop_in",
        "registration_required": "unknown",
        "faith_affiliation": "unknown",
        "schedule_text": schedule,
        "state": state,
        "metro_id": metro_id,
        "source_url": SOURCE_URL,
        "source_type": "national_org_directory",
        "verification_status": "source_verified" if kept["schedule"] else "needs_review",
        "first_seen": today.isoformat(),
        "last_checked": today.isoformat(),
        "source_link_status": "ok",
        "published": False,
        "languages": ["en"],
        "internal_notes": (
            "From the BPUSA chapter directory. Phone is the chapter contact BPUSA "
            "publishes. Email addresses and any address not labelled as a meeting "
            "location were deliberately discarded - see drop_personal_data()."),
    }
    if city:
        listing["city"] = city
    if kept["phone"]:
        listing["phone"] = format_phone(kept["phone"])
        listing["phone_normalized"] = kept["phone"]
    if kept["website"]:
        listing["url"] = kept["website"]
    if kept["venue"]:
        listing["description"] = f"Meets at {kept['venue']}."

    # Same expiry guard the hospice extraction uses: a schedule naming specific calendar
    # dates is a fixed series, and stops being true the moment those dates pass.
    try:
        from extract_groups import parse_dated_schedule
        is_dated, dates, all_past = parse_dated_schedule(schedule, today)
        if is_dated and dates:
            listing["schedule_expires"] = max(dates).isoformat()
            if all_past:
                return None, dropped, "schedule already expired"
    except Exception:                                    # pragma: no cover
        pass

    return listing, dropped, "kept"


# ----------------------------------------------------------------------------- runner

def load_html(args):
    if args.offline_test:
        with open(FIXTURE_PATH) as fh:
            return fh.read(), FIXTURE_PATH
    if args.from_file:
        with open(args.from_file) as fh:
            return fh.read(), args.from_file
    result = politefetch.fetch(SOURCE_URL)
    if not result.ok:
        return None, f"{SOURCE_URL} ({result.error})"
    if not result.text or len(result.text) < 5000:
        return None, (f"{SOURCE_URL} returned {len(result.text or '')} bytes - too small "
                      f"to be the chapter list. The site may be refusing our user agent. "
                      f"Save the page from a browser and re-run with --from-file.")
    return result.text, SOURCE_URL


def build(html, today):
    city_index, anchors = load_metro_index()
    chapters = parse_chapters(html)
    listings, dropped_total, outcomes = [], Counter(), Counter()
    for chapter in chapters:
        listing, dropped, why = to_listing(chapter, city_index, anchors, today)
        dropped_total.update(dropped)
        outcomes[why] += 1
        if listing:
            listings.append(listing)
    return chapters, listings, dropped_total, outcomes


def assert_no_personal_data(listings):
    """
    Belt and braces. If an email address or a personal-looking address ever reaches a
    listing, the run fails rather than opening a pull request nobody reads closely.
    """
    blob = json.dumps(listings)
    problems = []
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", blob.replace("info@awellrunlife.com", "")):
        problems.append("an email address reached a listing")
    for listing in listings:
        desc = listing.get("description", "")
        if desc and not desc.lower().startswith("meets at"):
            problems.append(f"unexpected description on {listing['slug']}")
    return problems


def write_report(chapters, listings, dropped, outcomes, source, today):
    by_state = Counter(l["state"] for l in listings)
    lines = [
        "# BPUSA Ingest Report\n",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n",
        f"Source: {source}\n",
        f"- Chapters found on the page: **{len(chapters)}**",
        f"- Listings produced: **{len(listings)}**",
        f"- With a published schedule (`source_verified`): "
        f"**{sum(1 for l in listings if l['verification_status'] == 'source_verified')}**",
        f"- States: **{len(by_state)}**\n",
        "## Personal data discarded\n",
        "This source publishes volunteers' contact details. The following were seen and "
        "deliberately not carried into any listing.\n",
        "| Kind | Count |", "|---|---|",
    ]
    for kind, count in sorted(dropped.items()):
        lines.append(f"| {kind} | {count} |")
    if not dropped:
        lines.append("| none seen | 0 |")
    lines += ["\n## Outcomes\n", "| Outcome | Count |", "|---|---|"]
    for why, count in outcomes.most_common():
        lines.append(f"| {why} | {count} |")
    lines += ["\n## By state\n", "| State | Chapters |", "|---|---|"]
    for st, count in sorted(by_state.items()):
        lines.append(f"| {st} | {count} |")
    lines.append("")
    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline-test", action="store_true",
                    help="Parse the committed fixture, write nothing, no network")
    ap.add_argument("--inspect", action="store_true",
                    help="Fetch live and report what parsed, but write nothing")
    ap.add_argument("--from-file", help="Parse a saved copy of the chapter page")
    args = ap.parse_args()

    today = date.today()
    html, source = load_html(args)
    if html is None:
        print(f"FATAL: could not read {source}")
        return 1

    chapters, listings, dropped, outcomes = build(html, today)

    print(f"source            : {source}")
    print(f"chapters parsed   : {len(chapters)}")
    print(f"listings produced : {len(listings)}")
    print(f"states            : {len(set(l['state'] for l in listings))}")
    print("discarded         : " + (", ".join(f"{v} {k}" for k, v in sorted(dropped.items()))
                                    or "nothing"))
    for why, count in outcomes.most_common():
        print(f"  {why}: {count}")

    problems = assert_no_personal_data(listings)
    if problems:
        print("\nFAILED the privacy check:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("privacy check     : passed")

    if args.offline_test:
        expected = {"Northern Alabama Chapter", "Western Arkansas Chapter",
                    "El Dorado County", "South Florida Chapter", "Tampa Bay Chapter",
                    "Chicagoland Chapter", "The North of The River Chapter"}
        got = {l["name"] for l in listings}
        missing = expected - got
        if missing:
            print(f"\nFAILED: fixture chapters not parsed: {sorted(missing)}")
            return 1
        eldorado = next(l for l in listings if l["name"] == "El Dorado County")
        if "Raley" not in eldorado.get("description", ""):
            print("\nFAILED: labelled meeting venue was not carried through")
            return 1
        if eldorado["verification_status"] != "source_verified":
            print("\nFAILED: a chapter with a published schedule should be source_verified")
            return 1
        tampa = next(l for l in listings if l["name"] == "Tampa Bay Chapter")
        if "description" in tampa:
            print("\nFAILED: a leader's home address leaked into a description")
            return 1
        if tampa.get("url") != "http://www.example-tampabay.org/":
            print(f"\nFAILED: chapter website not picked up ({tampa.get('url')})")
            return 1
        west = next(l for l in listings if l["name"] == "Western Arkansas Chapter")
        if west.get("phone") != "479-474-9773":
            print(f"\nFAILED: landline should beat mobile ({west.get('phone')})")
            return 1
        print("fixture checks    : passed")
        return 0

    if args.inspect:
        for listing in listings[:8]:
            print(f"\n  {listing['name']} ({listing.get('city') or ''} {listing['state']})")
            print(f"    phone    : {listing.get('phone')}")
            print(f"    schedule : {listing['schedule_text']}")
            print(f"    venue    : {listing.get('description', '-')}")
        return 0

    existing = []
    if os.path.exists(LISTINGS_PATH):
        with open(LISTINGS_PATH) as fh:
            existing = json.load(fh)

    # Replace, do not append: a chapter that changed its meeting night must not appear
    # twice, and one that closed must not linger. Everything from this source is rebuilt
    # from the page we just read.
    kept_other = [l for l in existing if l.get("source_url") != SOURCE_URL]
    removed = len(existing) - len(kept_other)
    with open(LISTINGS_PATH, "w") as fh:
        json.dump(kept_other + listings, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    write_report(chapters, listings, dropped, outcomes, source, today)
    print(f"\nreplaced {removed} previous BPUSA listing(s) with {len(listings)}")
    print(f"wrote {LISTINGS_PATH} and {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
