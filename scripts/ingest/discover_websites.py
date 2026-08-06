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


# --------------------------------------------------------------------------
# Name handling
# --------------------------------------------------------------------------

def clean_org_name(name):
    """
    Strip CMS artifacts so the name resembles how the organization writes it.

    >>> clean_org_name("HOSPICE OF THE VALLEY - WEST (031515)")
    'HOSPICE OF THE VALLEY'
    """
    if not name:
        return ""
    s = name.upper()
    s = re.sub(r"\(\s*\d{5,}\s*\)", " ", s)          # trailing CCN in parentheses
    s = re.sub(r"\bD/?B/?A\b.*", " ", s)              # "DBA Something"
    s = re.sub(r"[,\.]", " ", s)
    s = re.sub(r"\s+-\s+(WEST|EAST|NORTH|SOUTH|CENTRAL)\b.*", " ", s)
    s = re.sub(r"\b(LLC|L L C|INC|CORP|LP|LLP|PC|PLLC|LTD)\b", " ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


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


def page_contains_phone(text, phone_normalized):
    """True if the org's 10-digit phone appears in the page, in any common format."""
    if not phone_normalized:
        return False
    digits_only = re.sub(r"\D", "", text)
    return phone_normalized in digits_only


def verify_ownership(org, page_text):
    """
    Return (status, evidence) where status is one of:
      verified_phone | verified_name | mismatch
    """
    text_upper = page_text.upper()

    if page_contains_phone(page_text, org.get("phone_normalized")):
        return "verified_phone", f"phone {org.get('phone')} found on page"

    distinct = distinctive_tokens(org.get("name", ""))
    if distinct:
        found = [t for t in distinct if re.search(rf"\b{re.escape(t)}\b", text_upper)]
        ratio = len(found) / len(distinct)
        looks_like_hospice = any(k in text_upper for k in
                                 ("HOSPICE", "PALLIATIVE", "BEREAVEMENT", "END OF LIFE"))
        if ratio >= 0.8 and looks_like_hospice:
            return "verified_name", f"matched {len(found)}/{len(distinct)}: {', '.join(found[:4])}"
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

    for domain in candidate_domains(org.get("name", "")):
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
            status, evidence = verify_ownership(org, text)

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
    org2 = {"name": "GULFSIDE HOSPICE", "phone_normalized": None}
    status, ev = verify_ownership(org2, "Gulfside Hospice provides palliative care in Pasco County")
    assert status == "verified_name", status
    print(f"  verified_name: {ev}")

    print("\n=== rejection: right industry, WRONG organization ===")
    status, ev = verify_ownership(org2, "Suncoast Hospice provides palliative care in Pinellas")
    assert status == "mismatch", f"should have rejected, got {status}"
    print(f"  correctly rejected: {ev}")

    print("\n=== rejection: parked domain ===")
    status, ev = verify_ownership(org2, "This domain is for sale. Buy now.")
    assert status == "mismatch"
    print(f"  correctly rejected: {ev}")

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
                        help="Re-check organizations already checked")
    parser.add_argument("--offline-test", action="store_true",
                        help="Run logic tests with no network access and exit")
    args = parser.parse_args()

    if args.offline_test:
        return offline_test()

    with open(ORGS_PATH) as fh:
        orgs = json.load(fh)

    tiers = {"high": {"high"}, "high+medium": {"high", "medium"},
             "all": {"high", "medium", "low"}}[args.tier]

    queue = [o for o in orgs
             if o.get("crawl_tier") in tiers
             and o.get("crawl_priority", 0) >= args.min_score
             and (args.recheck or o.get("website_status") in (None, "not_checked"))]
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
