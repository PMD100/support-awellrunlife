#!/usr/bin/env python3
"""
NACG adapter — National Alliance for Children's Grief
=====================================================

Ingests the NACG member directory: 652 grief support centers, camps, and counseling
services for children, teens, and families.

WHY THIS SOURCE FIRST
---------------------
We intended to start with AFSP, then Compassionate Friends. Both turned out to be
JavaScript-rendered — their HTML contains a search widget and no data — so getting at
them means reverse-engineering an internal API, which is a different thing from crawling
public pages and, in TCF's case, sits awkwardly against their stated usage restriction.

NACG is the opposite: a plain paginated server-rendered listing at
`/find-support/page/N/`, 55 pages, everything in the HTML. robots.txt permits it,
there is a sitemap, and the directory covers children's and teen grief — a category
almost no competitor handles well and hospices barely touch.

WHAT WE KEEP AND WHAT WE DROP
-----------------------------
Not all 652 entries are support groups. The directory mixes peer support groups with
individual counseling practices, camps, school programs, and plain resource pages.
**Only entries tagged "Peer Support Groups" become listings.** A grieving parent
searching for a group should not be sent to a private therapy practice.

Camps are retained separately as `is_camp` — genuinely valuable for bereaved children,
but seasonal rather than a recurring group, so they must never be presented as one.

FIRST RUN IS RECONNAISSANCE
---------------------------
This parser is written against the rendered structure, but the raw HTML markup is
unknown until we fetch it. Run with `--inspect` first: it fetches one page, reports
which extraction strategy matched how many entries, and dumps a sample. Adjust, then
run for real. Guessing at markup and writing 652 bad records is the failure to avoid.

USAGE
-----
  python3 scripts/ingest/national_orgs/nacg.py --inspect
  python3 scripts/ingest/national_orgs/nacg.py --pages 3      (a small real run)
  python3 scripts/ingest/national_orgs/nacg.py
"""

import argparse
import html as html_module
import json
import os
import re
import sys
import uuid
from collections import Counter
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "lib"))
from normalize import (  # noqa: E402
    normalize_phone, format_phone, clean_text, normalize_state, slugify,
)
import politefetch  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
LISTINGS_PATH = os.path.join(REPO_ROOT, "data", "listings.json")
METROS_PATH = os.path.join(REPO_ROOT, "data", "metros.json")
REPORT_PATH = os.path.join(REPO_ROOT, "data", "nacg-report.md")

BASE = "https://nacg.org/find-support/"
SOURCE_ID = "nacg"
ORG_NAME = "National Alliance for Children's Grief"

# Service tags NACG applies. Only the first makes something a listing.
TAG_PEER_SUPPORT = "Peer Support Groups"
TAG_CAMPS = "Camps"
ALL_TAGS = [
    "Anticipatory Grief", "Camps", "Individual Counseling", "Other",
    "Peer Support Groups", "Resources", "School-Based",
    "Virtual Services/Support (In my state only)",
    "Virtual Services/Support (Outside of my state)",
]

AGE_TAG_MAP = {
    "preschool": "children", "elementary": "children",
    "teen": "teens", "young adult": "young_adults",
    "adult": "adults", "caregiver": "families",
}


def strip_tags(markup):
    """
    Text content of an HTML fragment.

    Uses html.unescape rather than hand-rolled replacements - the first inspection run
    surfaced "Resilience Counseling &#038; Play Therapy", a numeric entity my manual
    list didn't cover. unescape handles every named and numeric entity.
    """
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", markup)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_module.unescape(text)
    return re.sub(r"[ \t]+", " ", text)


def extract_page_coordinates(page_html):
    """
    Map links sit in a single block at the bottom of the page, not inside each entry,
    which is why the first run recovered coordinates for only 1 of 25 blocks. Pull them
    all out once and key them by provider name.
    """
    coords = {}
    for m in re.finditer(
            r'(?is)<a[^>]+href=["\']https?://maps\.google\.com/\?q=(-?[\d.]+),(-?[\d.]+)["\'][^>]*>(.*?)</a>',
            page_html):
        label = clean_text(strip_tags(m.group(3)))
        if label:
            # The link label is "<name> <street address>" run together, so the whole
            # string is stored and matched by prefix - see lookup_coordinates.
            coords[label.upper()] = (float(m.group(1)), float(m.group(2)))
    return coords


def lookup_coordinates(name, coords):
    """
    Match a provider to its map link.

    The link label concatenates name and street address, so an exact key lookup finds
    almost nothing - the second inspection run attached coordinates to 1 provider out of
    12 despite all 12 being present on the page. Match by prefix instead.
    """
    if not name:
        return None
    key = name.upper()
    if key in coords:
        return coords[key]
    for label, geo in coords.items():
        if label.startswith(key):
            return geo
    return None


def dedupe_entries(entries):
    """
    NACG renders each provider twice: a visible card and a hidden "View More" modal.
    The modal repeats the name, phone, website and email but omits city and state -
    hence 25 blocks for ~12 providers, with city/state at 11/25.

    Collapse them, preferring whichever copy actually carries a location.
    """
    best = {}
    for e in entries:
        key = (e["name"].upper(), e.get("phone_normalized") or "")
        current = best.get(key)
        if current is None:
            best[key] = e
            continue
        # Merge: keep any field the incumbent is missing
        for field in ("city", "state", "street", "lat", "lng", "email", "website"):
            if not current.get(field) and e.get(field):
                current[field] = e[field]
        if len(e["tags"]) > len(current["tags"]):
            current["tags"] = e["tags"]
    return list(best.values())


def fetch_page(n):
    url = BASE if n == 1 else f"{BASE}page/{n}/"
    return url, politefetch.fetch(url)


def split_entries(html):
    """
    Split a directory page into per-entry HTML blocks.

    Each entry begins with a heading containing the provider name. We split on
    heading tags and keep chunks that carry a tel: link, since every real entry has
    a phone number and navigation headings do not.
    """
    chunks = re.split(r"(?i)(?=<h[23][^>]*>)", html)
    return [c for c in chunks if re.search(r'href=["\']tel:', c, re.I)]


def parse_entry(block):
    """Extract one provider from an entry block. Returns None if it isn't one."""
    text = strip_tags(block)

    heading = re.search(r"(?is)<h[23][^>]*>(.*?)</h[23]>", block)
    name = clean_text(strip_tags(heading.group(1))) if heading else None
    if not name or len(name) < 3:
        return None
    name = re.sub(r"\s*×\s*$", "", name).strip()

    phone_m = re.search(r'href=["\']tel:([^"\']+)["\']', block, re.I)
    phone_digits = normalize_phone(phone_m.group(1)) if phone_m else None

    email_m = re.search(r'href=["\']mailto:([^"\'?]+)', block, re.I)
    email = clean_text(email_m.group(1)) if email_m else None

    # The provider's own website: an external http link that isn't nacg.org,
    # a mail/tel link, a social network, or a maps link.
    website = None
    for m in re.finditer(r'href=["\'](https?://[^"\']+)["\']', block, re.I):
        url = m.group(1)
        low = url.lower()
        if any(bad in low for bad in ("nacg.org", "childrengrieve", "maps.google",
                                      "facebook.com", "twitter.com", "instagram.com",
                                      "linkedin.com", "addtoany.com", "wufoo.com")):
            continue
        website = url
        break

    # Address: NACG renders "Address:" then the street, then ", City, ST"
    street = city = state = None
    addr = re.search(r"Address:\s*(.+?)(?:Contact|$)", text, re.S)
    if addr:
        raw = clean_text(addr.group(1).replace("\n", " "))
        m = re.match(r"^(.*?),\s*([A-Za-z .'-]+),\s*([A-Z]{2})\b", raw or "")
        if m:
            street, city, state = (clean_text(m.group(1)), clean_text(m.group(2)),
                                   normalize_state(m.group(3)))
        else:
            street = raw

    # Standalone two-letter state code, used when the address didn't include one
    if not state:
        m = re.search(r"\b([A-Z]{2})\b\s*(?:View More|$)", text)
        if m:
            state = normalize_state(m.group(1))

    # Lat/lng from the map links at the bottom of the page
    lat = lng = None
    geo = re.search(r"maps\.google\.com/\?q=(-?[\d.]+),(-?[\d.]+)", block)
    if geo:
        lat, lng = float(geo.group(1)), float(geo.group(2))

    tags = [t for t in ALL_TAGS if t.lower() in text.lower()]
    # NACG exposes age groups only as a search filter, never on the entry itself -
    # the first inspection run returned ages=[] for all 25 blocks. Rather than guess,
    # we leave this empty and fall back to the audience NACG exists to serve.
    ages = []

    return {
        "name": name, "phone_normalized": phone_digits, "email": email,
        "website": website, "street": street, "city": city, "state": state,
        "lat": lat, "lng": lng, "tags": tags, "age_groups": ages,
        "is_national": "National Provider" in text,
    }


def load_metro_index():
    with open(METROS_PATH) as fh:
        metros = json.load(fh)["metros"]
    city_index, state_index = {}, {}
    for metro in metros:
        if metro.get("virtual"):
            continue
        for city in metro.get("anchor_cities", []):
            city_index[(city.upper(), metro["states"][0])] = metro["id"]
        for st in metro.get("states", []):
            state_index.setdefault(st, []).append(metro["id"])
    return city_index, state_index


def to_listing(entry, source_url, city_index):
    """Convert a parsed entry into a schema-conforming listing, or None."""
    if TAG_PEER_SUPPORT not in entry["tags"]:
        return None
    if not entry["city"] or not entry["state"]:
        return None

    metro_id = city_index.get((entry["city"].upper(), entry["state"]))
    if not metro_id:
        return None
    if not entry["phone_normalized"] and not entry["website"]:
        return None

    listing = {
        "id": str(uuid.uuid4()),
        "slug": slugify(f"{entry['name']} {entry['city']}"),
        "name": "Children's Grief Peer Support Group",
        "organization": entry["name"],
        "org_type": "nonprofit",
        "loss_types": ["general"],
        "age_groups": entry["age_groups"] or ["children", "teens"],
        "format": "in_person",
        # NACG does not publish cost or schedule. We do NOT invent either.
        "cost": "unknown",
        "structure": "unknown",
        "registration_required": "unknown",
        "faith_affiliation": "unknown",
        "schedule_text": "Contact the organization for current meeting times",
        "city": entry["city"], "state": entry["state"], "metro_id": metro_id,
        "source_url": source_url,
        "source_type": "national_org_directory",
        # Listed by NACG, but no schedule or cost confirmed - flagged accordingly.
        "verification_status": "needs_review",
        "first_seen": date.today().isoformat(),
        "last_checked": date.today().isoformat(),
        "source_link_status": "ok",
        "published": False,
        "languages": ["en"],
        "internal_notes": (f"From NACG member directory. Service tags: "
                           f"{', '.join(entry['tags'])}. Schedule and cost not published "
                           f"by NACG - must be confirmed before this can leave needs_review."),
    }
    if entry["street"]:
        listing["street"] = entry["street"]
    if entry["phone_normalized"]:
        listing["phone"] = format_phone(entry["phone_normalized"])
        listing["phone_normalized"] = entry["phone_normalized"]
    if entry["email"]:
        listing["email"] = entry["email"]
    if entry["website"]:
        listing["url"] = entry["website"]
    if entry["lat"]:
        listing["lat"], listing["lng"] = entry["lat"], entry["lng"]
    if TAG_CAMPS in entry["tags"]:
        listing["internal_notes"] += " Also runs camps (seasonal, not a recurring group)."
    return listing


def inspect():
    url, page = fetch_page(1)
    print(f"Fetched {url}: status={page.status} bytes={len(page.text):,}\n")
    if not page.ok:
        print(f"FAILED: {page.error}")
        return 1

    blocks = split_entries(page.text)
    print(f"Entry blocks found: {len(blocks)}")
    if not blocks:
        print("\nNo blocks matched. Raw HTML sample around the first tel: link:")
        m = re.search(r'href=["\']tel:', page.text)
        if m:
            print(page.text[max(0, m.start() - 1500):m.start() + 500])
        return 1

    raw = [p for p in (parse_entry(b) for b in blocks) if p]
    coords = extract_page_coordinates(page.text)
    parsed = dedupe_entries(raw)
    for e in parsed:
        if not e.get("lat"):
            geo = lookup_coordinates(e["name"], coords)
            if geo:
                e["lat"], e["lng"] = geo
    print(f"Raw blocks parsed:  {len(raw)}  (card + hidden modal per provider)")
    print(f"After dedupe:       {len(parsed)}  <- actual providers")
    print(f"Coordinates on page:{len(coords)}\n")

    fields = ["name", "phone_normalized", "city", "state", "website", "email", "lat"]
    print("Field completeness:")
    for f in fields:
        got = sum(1 for p in parsed if p.get(f))
        print(f"  {f:20s} {got}/{len(parsed)}")

    print("\nService tag distribution:")
    for tag, n in Counter(t for p in parsed for t in p["tags"]).most_common():
        print(f"  {tag:52s} {n}")

    print("\nFirst three parsed entries:")
    for p in parsed[:3]:
        print(f"\n  {p['name']}")
        print(f"    {p['street']} | {p['city']}, {p['state']}")
        print(f"    phone={format_phone(p['phone_normalized'])} email={p['email']}")
        print(f"    web={p['website']}")
        print(f"    tags={p['tags']}")
        print(f"    ages={p['age_groups']}")

    pages = re.findall(r"/find-support/page/(\d+)/", page.text)
    print(f"\nTotal pages detected: {max(int(p) for p in pages) if pages else '?'}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", action="store_true",
                    help="Fetch one page, report what parsed, change nothing")
    ap.add_argument("--pages", type=int, help="Limit number of pages (default: all)")
    args = ap.parse_args()

    if args.inspect:
        return inspect()

    city_index, _ = load_metro_index()

    url, first = fetch_page(1)
    if not first.ok:
        print(f"FATAL: could not fetch {url}: {first.error}")
        return 1
    nums = re.findall(r"/find-support/page/(\d+)/", first.text)
    total = max(int(n) for n in nums) if nums else 1
    if args.pages:
        total = min(total, args.pages)

    print(f"NACG directory: {total} pages, 1 request/sec\n")

    all_entries, pages_ok = [], 0
    for n in range(1, total + 1):
        page_url, page = (url, first) if n == 1 else fetch_page(n)
        if not page.ok:
            print(f"  page {n}: FAILED ({page.error})")
            continue
        coords = extract_page_coordinates(page.text)
        entries = dedupe_entries(
            [p for p in (parse_entry(b) for b in split_entries(page.text)) if p])
        for e in entries:
            e["_source_url"] = page_url
            if not e.get("lat"):
                geo = lookup_coordinates(e["name"], coords)
                if geo:
                    e["lat"], e["lng"] = geo
        all_entries.extend(entries)
        pages_ok += 1
        if n % 10 == 0 or n == total:
            print(f"  page {n}/{total}: {len(all_entries)} entries so far")

    stats = Counter()
    listings = []
    for e in all_entries:
        if TAG_PEER_SUPPORT not in e["tags"]:
            stats["not a peer support group"] += 1
            continue
        listing = to_listing(e, e["_source_url"], city_index)
        if listing:
            listings.append(listing)
            stats["kept"] += 1
        else:
            stats["outside our metros or missing contact"] += 1

    existing = []
    if os.path.exists(LISTINGS_PATH):
        with open(LISTINGS_PATH) as fh:
            existing = json.load(fh)
    seen = {(l.get("organization"), l.get("city")) for l in existing}
    new = [l for l in listings if (l["organization"], l["city"]) not in seen]

    with open(LISTINGS_PATH, "w") as fh:
        json.dump(existing + new, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    by_metro = Counter(l["metro_id"] for l in listings)
    lines = ["# NACG Ingest Report\n",
             f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n",
             f"- Pages fetched: **{pages_ok}/{total}**",
             f"- Directory entries parsed: **{len(all_entries)}**",
             f"- Tagged 'Peer Support Groups': **{stats['kept'] + stats['outside our metros or missing contact']}**",
             f"- Inside our metros with contact details: **{len(listings)}**",
             f"- New (not already present): **{len(new)}**\n",
             "## Why entries were dropped\n", "| Reason | Count |", "|---|---|"]
    for reason, n in stats.most_common():
        if reason != "kept":
            lines.append(f"| {reason} | {n} |")
    lines += ["\n## By metro\n", "| Metro | Groups |", "|---|---|"]
    for metro, n in by_metro.most_common():
        lines.append(f"| {metro} | {n} |")
    lines += ["\n## Important caveat\n",
              "NACG publishes **no schedule and no cost** for these providers. Every listing "
              "is therefore written with `cost: unknown`, a schedule of \"Contact the "
              "organization for current meeting times\", and "
              "`verification_status: needs_review`. They will display with the "
              "*Unconfirmed — call ahead* badge and **cannot count toward a metro's "
              "publish threshold** until someone confirms the details.\n",
              "That is the correct outcome, not a shortfall. Inventing a meeting time we "
              "were never given is the one error this project cannot afford.\n"]

    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print("\n" + "=" * 60)
    for reason, n in stats.most_common():
        print(f"  {reason:38s} {n:5d}")
    print("=" * 60)
    print(f"  Added {len(new)} new listings. Total: {len(existing) + len(new)}")
    print(f"\nWrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
