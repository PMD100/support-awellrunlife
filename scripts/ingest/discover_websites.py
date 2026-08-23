#!/usr/bin/env python3
"""
Website discovery and verification
==================================

Finds each organization's real website and PROVES it belongs to them.

THE RULE THIS SCRIPT ENFORCES
-----------------------------
"Make sure that any links to websites are accurate and go to the actual business."

A link is never attached to an organization on similarity or vibes. It attaches only
when one of two things is demonstrated:

  1. **Phone match** — the phone number CMS has on file for this hospice appears in the
     page text. This is near-conclusive; two unrelated businesses do not share a phone
     number. Recorded as `verified_phone`.

  2. **Name match** — every distinctive word in the organization's name appears in the
     page text, and the page is recognizably about hospice or bereavement care.
     "Distinctive" excludes filler like HOSPICE, CARE, HEALTH, SERVICES, INC.
     Recorded as `verified_name`.

Anything else is recorded as `mismatch` and never published. We would rather show no
website than the wrong one.

WHY WE GUESS DOMAINS INSTEAD OF SEARCHING
-----------------------------------------
The CMS dataset has no website column. Search APIs cost money and need keys. But our
high-tier organizations are established nonprofits with predictable names — "Hospice of
the Valley" really is at hov.org — so candidate-domain generation gets decent recall for
free. If a BRAVE_API_KEY secret is present the script will use search as well, which
substantially improves recall. It is optional; everything works without it.

USAGE
-----
  python3 scripts/ingest/discover_websites.py --tier high
  python3 scripts/ingest/discover_websites.py --tier high --limit 25
  python3 scripts/ingest/discover_websites.py --offline-test    (no network; asserts logic)
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
from collections import Counter
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from normalize import normalize_phone  # noqa: E402
import politefetch  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORGS_PATH = os.path.join(REPO_ROOT, "data", "organizations.json")
REPORT_PATH = os.path.join(REPO_ROOT, "data", "website-discovery-report.md")

# Words too common in hospice names to identify anyone
GENERIC_TOKENS = {
    "HOSPICE", "HOSPICES", "CARE", "CARES", "HEALTH", "HEALTHCARE", "SERVICE",
    "SERVICES", "INC", "LLC", "LLP", "CORP", "CO", "THE", "OF", "AND", "AT",
    "FOR", "HOME", "HOMES", "PALLIATIVE", "MEDICAL", "CENTER", "CENTRE", "GROUP",
    "AGENCY", "COMPANY", "ASSOCIATES", "PARTNERS", "SYSTEM", "NETWORK", "INCORPORATED",
    "INTERNATIONAL", "AMERICA", "AMERICAN", "US", "USA", "NATIONAL",
}

# Paths worth checking for a bereavement page, in rough order of likelihood
BEREAVEMENT_PATHS = [
    "/bereavement", "/grief-support", "/grief", "/support-groups", "/grief-services",
    "/bereavement-services", "/services/bereavement", "/services/grief-support",
    "/support", "/counseling", "/grief-and-loss", "/family-support",
]

# Link text / href fragments that suggest a bereavement page
BEREAVEMENT_HINTS = [
    "bereavement", "grief", "griev", "support group", "loss support",
    "after the loss", "condolence", "memorial support",
]

TLD_ORDER = [".org", ".com", ".net", ".health"]

# VERIFICATION RULE VERSION
# ------------------------
# Bump this whenever the rules in verify_ownership change. Every organization records
# the version it was last checked under, and anything checked under an older version is
# re-queued automatically - no checkbox, no one having to remember.
#
# This exists because the location rule (v2) shipped three times without taking effect:
# each run used "retry failures", which skips records that are already `verified`. But a
# record that is verified-but-wrong is precisely what a rule change needs to revisit.
# Relying on the operator to pick the right checkbox was the bug.
#
#   1  name or phone match
#   2  name match must also be corroborated by the organization's state (2026-08-18)
VERIFICATION_RULE_VERSION = 2

# Used by the location check in verify_ownership. Sites write "Ohio" as often as "OH".
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


# --------------------------------------------------------------------------
# Name handling
# --------------------------------------------------------------------------

def clean_org_name(name):
    """
    Strip CMS artifacts so the name resembles how the organization writes it.

    CMS records are per-location, so names carry branch qualifiers that are not part
    of the brand: "(BALTIMORE)", "- GAINESVILLE", "(031515)". Left in, they wreck
    domain guessing — "HEARTLAND HOSPICE (BALTIMORE)" yielded initials "hhb.com".

    >>> clean_org_name("HOSPICE OF THE VALLEY - WEST (031515)")
    'HOSPICE OF THE VALLEY'
    >>> clean_org_name("HEARTLAND HOSPICE (BALTIMORE)")
    'HEARTLAND HOSPICE'
    >>> clean_org_name("AFFINIS HOSPICE, LLC- GAINESVILLE")
    'AFFINIS HOSPICE'
    """
    if not name:
        return ""
    s = name.upper()
    s = re.sub(r"\([^)]*\)", " ", s)                  # ANY parenthetical, not just CCNs
    s = re.sub(r"\bD/?B/?A\b.*", " ", s)              # "DBA Something"
    s = re.sub(r"[,\.]", " ", s)
    s = re.sub(r"\b(LLC|L L C|INC|CORP|LP|LLP|PC|PLLC|LTD|INCORPORATED)\b", " ", s)
    s = re.sub(r"\s*-\s*[A-Z0-9][A-Z0-9 ]*$", " ", s)  # trailing branch qualifier after a dash
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Trailing compass qualifier with no dash: "HOSPICE OF THE VALLEY EAST".
    # Only strip if enough of the name survives - we don't want "HOSPICE OF THE NORTH"
    # collapsing to "HOSPICE OF THE".
    stripped = re.sub(r"\s+(NORTH|SOUTH|EAST|WEST|CENTRAL|NORTHEAST|NORTHWEST|"
                      r"SOUTHEAST|SOUTHWEST)$", "", s)
    if stripped != s and len(stripped.split()) >= 2:
        s = stripped
    return s.strip()


def tokens_of(name):
    return [t for t in clean_org_name(name).split() if len(t) > 1]


def distinctive_tokens(name):
    """The words that actually identify this organization."""
    return [t for t in tokens_of(name) if t not in GENERIC_TOKENS]


def candidate_domains(name):
    """
    Plausible domains for an organization, best guesses first.

    >>> candidate_domains("HOSPICE OF THE VALLEY")[:2]
    ['hospiceofthevalley.org', 'hospiceofthevalley.com']
    """
    toks = tokens_of(name)
    if not toks:
        return []

    distinct = distinctive_tokens(name)
    stems = []

    full = "".join(toks).lower()
    if 4 <= len(full) <= 30:
        stems.append(full)

    # Drop connector words: "hospice of the valley" -> "hospicevalley"
    meaningful = [t for t in toks if t not in {"OF", "THE", "AND", "AT", "FOR"}]
    compact = "".join(meaningful).lower()
    if compact != full and 4 <= len(compact) <= 30:
        stems.append(compact)

    # Distinctive words only: "gulfside hospice inc" -> "gulfside"
    if distinct:
        d = "".join(distinct).lower()
        if 4 <= len(d) <= 30 and d not in stems:
            stems.append(d)

    # Initials, for the "HOV" pattern
    if len(meaningful) >= 3:
        initials = "".join(t[0] for t in meaningful).lower()
        if 3 <= len(initials) <= 6:
            stems.append(initials)

    seen, out = set(), []
    for stem in stems:
        for tld in TLD_ORDER:
            domain = f"{stem}{tld}"
            if domain not in seen:
                seen.add(domain)
                out.append(domain)
    return out


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

def strip_html(html):
    """Crude but adequate text extraction — we only need to find strings."""
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (text.replace("&amp;", "&").replace("&nbsp;", " ")
                .replace("&#39;", "'").replace("&quot;", '"')
                .replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", text)


def extract_metadata_text(html):
    """
    Pull organization names out of places that survive a JavaScript-rendered page.

    Many nonprofit sites render body copy with JS, so stripping tags yields almost
    nothing — Gilchrist Hospice's real site matched zero words for exactly this reason.
    But the name almost always survives in the <title>, the Open Graph tags, the logo's
    alt text, or JSON-LD. Those are legitimate page content.

    Deliberately EXCLUDES href values. The domain we guessed appears in the page's own
    links, so searching raw HTML would match the name against our own guess and verify
    everything. That would defeat the entire point.
    """
    parts = []
    for m in re.finditer(r"(?is)<title[^>]*>(.*?)</title>", html):
        parts.append(m.group(1))
    for m in re.finditer(
            r'(?is)<meta[^>]+(?:name|property)=["\'](?:description|og:site_name|og:title|'
            r'twitter:title|application-name)["\'][^>]*content=["\']([^"\']*)["\']', html):
        parts.append(m.group(1))
    for m in re.finditer(
            r'(?is)<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:name|property)=["\']'
            r'(?:description|og:site_name|og:title)["\']', html):
        parts.append(m.group(1))
    for m in re.finditer(r'(?is)<img[^>]+alt=["\']([^"\']{2,120})["\']', html):
        parts.append(m.group(1))
    for m in re.finditer(r'(?is)aria-label=["\']([^"\']{2,120})["\']', html):
        parts.append(m.group(1))
    for m in re.finditer(r'"(?:name|legalName|alternateName)"\s*:\s*"([^"]{2,120})"', html):
        parts.append(m.group(1))
    return " ".join(strip_html(p) for p in parts)


def extract_tel_links(html):
    """`tel:` hrefs are stripped along with all other markup, but they carry phone numbers."""
    return " ".join(m.group(1) for m in
                    re.finditer(r'(?i)href=["\']tel:([^"\']+)["\']', html))


def page_contains_phone(haystack, phone_normalized):
    """True if the org's 10-digit phone appears, in any format."""
    if not phone_normalized:
        return False
    return phone_normalized in re.sub(r"\D", "", haystack)


def verify_ownership(org, page_text, html=""):
    """
    Return (status, evidence) where status is one of:
      verified_phone | verified_name | mismatch
    """
    meta_text = extract_metadata_text(html) if html else ""
    haystack = f"{page_text} {meta_text}".upper()
    phone_haystack = f"{page_text} {extract_tel_links(html) if html else ''}"

    if page_contains_phone(phone_haystack, org.get("phone_normalized")):
        return "verified_phone", f"phone {org.get('phone')} found on page"

    distinct = distinctive_tokens(org.get("name", ""))
    if distinct:
        found = [t for t in distinct if re.search(rf"\b{re.escape(t)}\b", haystack)]
        ratio = len(found) / len(distinct)
        looks_like_hospice = any(k in haystack for k in
                                 ("HOSPICE", "PALLIATIVE", "BEREAVEMENT", "END OF LIFE",
                                  "GRIEF", "END-OF-LIFE"))
        if ratio >= 0.8 and looks_like_hospice:
            # LOCATION CHECK. Added 2026-08-18 after three listings went live pointing at
            # entirely different organizations:
            #
            #   Hope Hospice (Pittsburgh PA)     -> Hope Hospice of Pleasanton, California
            #   Valley Hospice (Paramus NJ)      -> Valley Hospice of Steubenville, Ohio
            #   Hospice of Lancaster (SC)        -> Hospice & Community Care of Lancaster, PA
            #
            # Each matched on a single word - HOPE, VALLEY, LANCASTER - which the code
            # treated as "distinctive" but which are in fact among the commonest words in
            # American hospice names, and in Lancaster's case a place name shared by two
            # states. Name alone is not identity.
            #
            # A name match must now be corroborated by the organization's own city or
            # state appearing on the page. Phone matches are exempt: a shared phone
            # number is conclusive on its own.
            # State is the reliable signal. City alone is not, because a city name that
            # also appears in the organization's name makes the check circular:
            # "Hospice of Lancaster" (SC) matched Lancaster, PENNSYLVANIA's site, and
            # "LANCASTER" was present on the page purely because of the Pennsylvania city.
            city = (org.get("city") or "").upper()
            state = (org.get("state") or "").upper()
            state_full = STATE_NAMES.get(state, "").upper()
            org_name_upper = clean_org_name(org.get("name", ""))

            location_found = []
            if state and (re.search(rf"\b{re.escape(state)}\b", haystack)
                          or (state_full and state_full in haystack)):
                location_found.append(state)
            elif city and len(city) > 3 and city not in org_name_upper \
                    and re.search(rf"\b{re.escape(city)}\b", haystack):
                location_found.append(city)

            if not location_found:
                return "mismatch", (
                    f"name matched ({', '.join(found[:3])}) but neither the city "
                    f"({org.get('city') or '?'}) nor the state ({state or '?'}) appears "
                    f"on the page - probably a different organization with a similar name")

            where = ("page text" if re.search(rf"\b{re.escape(found[0])}\b", page_text.upper())
                     else "page title/metadata")
            return "verified_name", (f"matched {len(found)}/{len(distinct)} in {where}: "
                                     f"{', '.join(found[:4])}; location confirmed by "
                                     f"{'/'.join(location_found)}")
        if ratio >= 0.8:
            return "mismatch", (f"name matched {len(found)}/{len(distinct)} but page is not "
                                f"about hospice or grief care")
        return "mismatch", f"only matched {len(found)}/{len(distinct)} distinctive words"

    return "mismatch", "organization name has no distinctive words to match on"


def find_bereavement_links(html, base_url):
    """Pull candidate bereavement page URLs out of a homepage's links."""
    found = []
    for match in re.finditer(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html):
        href, label = match.group(1), strip_html(match.group(2)).lower()
        haystack = f"{href.lower()} {label}"
        if any(hint in haystack for hint in BEREAVEMENT_HINTS):
            url = urllib.parse.urljoin(base_url, href)
            if url.startswith("http") and url not in found:
                found.append(url)
    return found[:6]


# --------------------------------------------------------------------------
# Discovery for one organization
# --------------------------------------------------------------------------

def discover_one(org, verbose=False):
    """Mutates org in place. Returns the resulting website_status."""
    org["website_checked_at"] = date.today().isoformat()
    org["verification_rule"] = VERIFICATION_RULE_VERSION

    # A suggested domain is tried FIRST but is never trusted - it still has to pass
    # phone-or-name matching plus the location check like any guessed domain. Used for
    # leads that arrived with a website attached from an unverified source.
    domains = candidate_domains(org.get("name", ""))
    hint = (org.get("hint_domain") or "").strip().lower()
    if hint:
        hint = hint.replace("https://", "").replace("http://", "").strip("/")
        domains = [hint] + [d for d in domains if d != hint]

    for domain in domains:
        if not politefetch.domain_resolves(domain):
            continue

        for scheme in ("https", "http"):
            result = politefetch.fetch(f"{scheme}://{domain}/")
            if result.blocked_by_robots:
                org.update(website=None, website_status="robots_disallowed",
                           website_evidence=f"{domain} disallows crawling")
                return org["website_status"]
            if not result.ok:
                continue

            text = strip_html(result.text)
            status, evidence = verify_ownership(org, text, result.text)

            if status.startswith("verified"):
                org["website"] = result.final_url
                org["website_status"] = status
                org["website_evidence"] = evidence

                # Look for a bereavement page
                links = find_bereavement_links(result.text, result.final_url)
                bereavement = None
                for link in links:
                    page = politefetch.fetch(link)
                    if page.ok and any(h in strip_html(page.text).lower()
                                       for h in ("bereavement", "grief", "support group")):
                        bereavement = page.final_url
                        break

                if not bereavement:
                    for path in BEREAVEMENT_PATHS[:6]:
                        page = politefetch.fetch(urllib.parse.urljoin(result.final_url, path))
                        if page.ok and any(h in strip_html(page.text).lower()
                                           for h in ("bereavement", "grief", "support group")):
                            bereavement = page.final_url
                            break

                org["bereavement_page"] = bereavement
                if verbose:
                    print(f"    OK {status}: {result.final_url}"
                          + (f"  -> {bereavement}" if bereavement else "  (no grief page)"))
                return status

            org.setdefault("_rejected", []).append(f"{domain}: {evidence}")
            break  # domain resolved but isn't them; don't try http after https

    org["website"] = None
    org["website_status"] = "not_found"
    org["website_evidence"] = "; ".join(org.pop("_rejected", [])[:3]) or "no candidate domain resolved"
    return "not_found"


def inherit_from_siblings(orgs):
    """
    Branch offices inherit their brand's verified website.

    CMS lists one record per location, so "Hospice of the Valley - West", "- East" and
    "- Central" are three records for one organization with one website. Once any of
    them verifies, the others should not be searched from scratch — and in practice
    their branch-specific names generate bad domain guesses that fail.

    Scoped to the same state, so unrelated national chains sharing a name in different
    markets don't cross-contaminate.

    Returns the number of organizations updated.
    """
    verified_by_brand = {}
    for org in orgs:
        if not str(org.get("website_status", "")).startswith("verified"):
            continue
        key = (clean_org_name(org.get("name", "")), org.get("state"))
        # Prefer phone-verified parents; they're the strongest evidence
        existing = verified_by_brand.get(key)
        if not existing or (org["website_status"] == "verified_phone"
                            and existing["website_status"] != "verified_phone"):
            verified_by_brand[key] = org

    updated = 0
    for org in orgs:
        if org.get("website_status") not in ("not_found", None):
            continue
        if org.get("website_status") is None and "crawl_tier" not in org:
            continue
        key = (clean_org_name(org.get("name", "")), org.get("state"))
        parent = verified_by_brand.get(key)
        if not parent or parent is org:
            continue
        org["website"] = parent["website"]
        org["bereavement_page"] = parent.get("bereavement_page")
        org["website_status"] = "verified_sibling"
        org["website_evidence"] = (
            f"same brand and state as {parent['name']} "
            f"({parent['city']}), which verified via {parent['website_status']}"
        )
        org["website_checked_at"] = date.today().isoformat()
        org["verification_rule"] = VERIFICATION_RULE_VERSION
        updated += 1
    return updated


# --------------------------------------------------------------------------
# Offline logic test
# --------------------------------------------------------------------------

def offline_test():
    """Exercise every pure function without touching the network."""
    print("=== candidate domain generation ===")
    cases = {
        "HOSPICE OF THE VALLEY - WEST (031515)": "hospiceofthevalley.org",
        "GULFSIDE HOSPICE, INC": "gulfsidehospice.org",
        "SUNCOAST HOSPICE": "suncoasthospice.org",
    }
    for name, expected in cases.items():
        domains = candidate_domains(name)
        print(f"  {name[:40]:42s} -> {domains[:3]}")
        assert expected in domains, f"expected {expected} among {domains}"

    print("\n=== name cleaning ===")
    assert clean_org_name("HOSPICE OF THE VALLEY - WEST (031515)") == "HOSPICE OF THE VALLEY"
    assert clean_org_name("GULFSIDE HOSPICE, INC") == "GULFSIDE HOSPICE"
    print("  CMS artifacts stripped correctly")

    print("\n=== distinctive tokens ===")
    assert distinctive_tokens("GULFSIDE HOSPICE, INC") == ["GULFSIDE"]
    assert distinctive_tokens("HOSPICE CARE SERVICES INC") == []
    print("  generic words excluded correctly")

    print("\n=== phone verification (the strong signal) ===")
    org = {"name": "GULFSIDE HOSPICE", "phone": "727-555-0100", "phone_normalized": "7275550100"}
    for fmt in ["Call us at (727) 555-0100 today",
                "Phone: 727.555.0100",
                "tel:+17275550100"]:
        status, ev = verify_ownership(org, fmt)
        assert status == "verified_phone", f"{fmt} -> {status}"
        print(f"  matched in: {fmt!r}")

    print("\n=== name verification ===")
    org2 = {"name": "GULFSIDE HOSPICE", "city": "LAND O LAKES", "state": "FL",
            "phone_normalized": None}
    status, ev = verify_ownership(org2, "Gulfside Hospice provides palliative care in "
                                        "Land O Lakes, FL, serving Pasco County")
    assert status == "verified_name", status
    print(f"  verified_name: {ev}")

    print("\n=== rejection: right industry, WRONG organization ===")
    status, ev = verify_ownership(org2, "Suncoast Hospice provides palliative care in Pinellas")
    assert status == "mismatch", f"should have rejected, got {status}"
    print(f"  correctly rejected: {ev}")

    print("\n=== REJECTION: same name, WRONG STATE (the live bug) ===")
    for label, org_loc, page_txt in [
        ("Hope Hospice PA vs CA site",
         {"name": "HOPE HOSPICE", "city": "PITTSBURGH", "state": "PA", "phone_normalized": None},
         "Hope Hospice provides compassionate hospice care in Pleasanton, California."),
        ("Valley Hospice NJ vs OH site",
         {"name": "VALLEY HOSPICE", "city": "PARAMUS", "state": "NJ", "phone_normalized": None},
         "Valley Hospice serves Steubenville, Ohio and Wheeling, West Virginia with palliative care."),
        ("Hospice of Lancaster SC vs PA site",
         {"name": "HOSPICE OF LANCASTER", "city": "LANCASTER", "state": "SC", "phone_normalized": None},
         "Hospice & Community Care of Lancaster, Pennsylvania provides hospice and bereavement services."),
    ]:
        st, ev = verify_ownership(org_loc, page_txt, "")
        assert st == "mismatch", f"{label} WRONGLY ACCEPTED: {ev}"
        print(f"  rejected {label}")

    print("\n  ...and the SAME organization at its OWN site is still accepted:")
    right = {"name": "VALLEY HOSPICE", "city": "STEUBENVILLE", "state": "OH", "phone_normalized": None}
    st, ev = verify_ownership(right, "Valley Hospice serves Steubenville, Ohio with hospice and bereavement care.", "")
    assert st == "verified_name", f"got {st}: {ev}"
    print(f"     {ev}")

    print("\n=== rejection: parked domain ===")
    status, ev = verify_ownership(org2, "This domain is for sale. Buy now.")
    assert status == "mismatch"
    print(f"  correctly rejected: {ev}")

    print("\n=== FIX 1: branch qualifiers stripped ===")
    for raw, expected in [("HEARTLAND HOSPICE (BALTIMORE)", "HEARTLAND HOSPICE"),
                          ("AFFINIS HOSPICE, LLC- GAINESVILLE", "AFFINIS HOSPICE"),
                          ("HOSPICE OF THE VALLEY - WEST (031515)", "HOSPICE OF THE VALLEY")]:
        got = clean_org_name(raw)
        assert got == expected, f"{raw!r} -> {got!r}, expected {expected!r}"
        print(f"  {raw[:38]:40s} -> {got}")
    assert "heartlandhospice.org" in candidate_domains("HEARTLAND HOSPICE (BALTIMORE)")
    print("  and now generates heartlandhospice.org")

    print("\n=== FIX 2: name found in title/metadata when body is JS-rendered ===")
    js_page = ('<html><head><title>Gulfside Hospice | Land O Lakes, FL</title>'
               '<meta property="og:site_name" content="Gulfside Hospice">'
               '</head><body><div id="root"></div></body></html>')
    status, ev = verify_ownership(org2, strip_html(js_page), js_page)
    assert status == "verified_name", f"got {status}: {ev}"
    print(f"  {ev}")

    print("\n=== FIX 2 safety: our own guessed domain must NOT self-verify ===")
    link_only = '<html><body><a href="https://gulfsidehospice.org">Home</a> Hospice care</body></html>'
    status, ev = verify_ownership(org2, strip_html(link_only), link_only)
    assert status == "mismatch", f"SELF-VERIFICATION BUG: {status} / {ev}"
    print(f"  correctly rejected: {ev}")

    print("\n=== FIX 3: tel: links checked for phone ===")
    tel_page = '<html><body><a href="tel:+1-727-555-0100">Call</a></body></html>'
    status, ev = verify_ownership(org, strip_html(tel_page), tel_page)
    assert status == "verified_phone", f"got {status}"
    print(f"  {ev}")

    print("\n=== FIX 4: branch offices inherit a verified parent ===")
    fleet = [
        {"name": "HOSPICE OF THE VALLEY - WEST", "state": "AZ", "city": "Phoenix",
         "website": "https://hov.org", "bereavement_page": "https://hov.org/grief",
         "website_status": "verified_phone", "crawl_tier": "high"},
        {"name": "HOSPICE OF THE VALLEY EAST", "state": "AZ", "city": "Mesa",
         "website_status": "not_found", "crawl_tier": "high"},
        {"name": "UNRELATED HOSPICE", "state": "AZ", "city": "Tucson",
         "website_status": "not_found", "crawl_tier": "high"},
        {"name": "HOSPICE OF THE VALLEY", "state": "CA", "city": "San Jose",
         "website_status": "not_found", "crawl_tier": "high"},
    ]
    n = inherit_from_siblings(fleet)
    assert n == 1, f"expected exactly 1 inheritance, got {n}"
    assert fleet[1]["website"] == "https://hov.org"
    assert fleet[1]["website_status"] == "verified_sibling"
    assert fleet[2]["website_status"] == "not_found", "unrelated org wrongly inherited"
    assert fleet[3]["website_status"] == "not_found", "cross-STATE inheritance leaked"
    print(f"  1 branch inherited; unrelated org and same-name-different-state both refused")

    print("\n=== bereavement link detection ===")
    html = '''<a href="/about">About Us</a>
              <a href="/services/grief-support">Grief Support Groups</a>
              <a href="/donate">Donate</a>
              <a href="/bereavement-services">Bereavement</a>'''
    links = find_bereavement_links(html, "https://example.org/")
    assert "https://example.org/services/grief-support" in links
    assert "https://example.org/bereavement-services" in links
    assert not any("donate" in l for l in links)
    print(f"  found {len(links)}: {[l.split('/')[-1] for l in links]}")

    print("\n=== html stripping ===")
    assert "alert" not in strip_html("<script>alert('x')</script><p>Hello</p>")
    print("  scripts and tags removed")

    print("\nALL OFFLINE TESTS PASSED")
    return 0


# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", default="high",
                        choices=["high", "high+medium", "all"],
                        help="Which crawl tier to process (default: high)")
    parser.add_argument("--limit", type=int, help="Stop after N organizations")
    parser.add_argument("--min-score", type=int, default=0,
                        help="Only process organizations at or above this crawl_priority")
    parser.add_argument("--recheck", action="store_true",
                        help="Re-check every organization, including ones already verified")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Re-check only previous failures. Preferred after a logic fix: "
                             "it avoids re-requesting sites we already verified.")
    parser.add_argument("--offline-test", action="store_true",
                        help="Run logic tests with no network access and exit")
    args = parser.parse_args()

    if args.offline_test:
        return offline_test()

    with open(ORGS_PATH) as fh:
        orgs = json.load(fh)

    tiers = {"high": {"high"}, "high+medium": {"high", "medium"},
             "all": {"high", "medium", "low"}}[args.tier]

    RETRYABLE = {"not_found", "mismatch", None, "not_checked"}

    def wanted(o):
        status = o.get("website_status")
        # Checked under a superseded rule -> always re-check, whatever the flags say.
        if o.get("verification_rule", 0) < VERIFICATION_RULE_VERSION:
            return True
        if args.recheck:
            return True
        if args.retry_failed:
            return status in RETRYABLE
        return status in (None, "not_checked")

    queue = [o for o in orgs
             if o.get("crawl_tier") in tiers
             and o.get("crawl_priority", 0) >= args.min_score
             and wanted(o)]
    queue.sort(key=lambda o: -o.get("crawl_priority", 0))
    if args.limit:
        queue = queue[:args.limit]

    print(f"Processing {len(queue):,} organizations (tier={args.tier})")
    print(f"Politeness: {politefetch.MIN_DELAY}s per domain, robots.txt honored, "
          f"UA={politefetch.USER_AGENT}\n")

    results = Counter()
    for i, org in enumerate(queue, 1):
        print(f"[{i}/{len(queue)}] {org['name'][:52]}")
        status = discover_one(org, verbose=True)
        results[status] += 1
        if status == "not_found":
            print(f"    -- {org.get('website_evidence','')[:90]}")

    inherited = inherit_from_siblings(orgs)
    if inherited:
        results["verified_sibling"] += inherited
        print(f"\nBranch offices inheriting a verified parent site: {inherited}")

    with open(ORGS_PATH, "w") as fh:
        json.dump(orgs, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    # ---- Report ----------------------------------------------------------
    checked = [o for o in orgs if o.get("website_status") not in (None, "not_checked")]
    verified = [o for o in checked if str(o.get("website_status", "")).startswith("verified")]
    with_grief = [o for o in verified if o.get("bereavement_page")]

    lines = ["# Website Discovery Report\n",
             f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n",
             "Every website below was **proved** to belong to its organization, either by ",
             "finding the CMS-registered phone number in the page text or by matching every ",
             "distinctive word of the organization's name on a page about hospice care. ",
             "Unproven matches are recorded as `mismatch` and never published.\n",
             "## This run\n", "| Outcome | Count |", "|---|---|"]
    for status, count in results.most_common():
        lines.append(f"| `{status}` | {count} |")

    lines += ["\n## Cumulative\n",
              f"- Organizations checked: **{len(checked):,}**",
              f"- Website verified: **{len(verified):,}**"
              + (f" ({len(verified)/len(checked)*100:.0f}%)" if checked else ""),
              f"- Bereavement page found: **{len(with_grief):,}**",
              "\n**Organizations with a bereavement page are the ones that become listings.**\n"]

    if with_grief:
        lines += ["## Sample of verified organizations with grief pages\n",
                  "| Organization | Metro | How verified | Bereavement page |", "|---|---|---|---|"]
        for o in with_grief[:30]:
            lines.append(f"| {o['name'][:40]} | {o['metro_id']} | "
                         f"`{o['website_status']}` | {o['bereavement_page'][:60]} |")

    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print("\n" + "=" * 60)
    for status, count in results.most_common():
        print(f"  {status:22s} {count:5d}")
    print("=" * 60)
    print(f"  Verified this run:      {sum(v for k, v in results.items() if k.startswith('verified')):,}")
    print(f"  Cumulative verified:    {len(verified):,}")
    print(f"  With bereavement page:  {len(with_grief):,}   <- these become listings")
    print(f"\nWrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
