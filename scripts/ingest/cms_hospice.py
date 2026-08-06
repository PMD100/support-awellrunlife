#!/usr/bin/env python3
"""
CMS Hospice ingest
==================

Turns the federal hospice provider dataset into our organization list.

WHY THIS SCRIPT EXISTS
----------------------
There is no public database of grief support groups. But there IS a near-complete
public database of the organizations most likely to run them: every Medicare-certified
hospice in the country. Nearly all of them offer bereavement services free to the
community, whether or not the deceased was ever their patient.

So we start from the known universe of providers rather than searching the open web.
This script produces that universe, filtered to our 25 launch metros. A later script
discovers each organization's website and finds the actual groups.

WHAT IT OUTPUTS
---------------
  data/organizations.json   One record per hospice in our metros
  data/coverage-report.md   Human-readable summary, including counties we may have missed

USAGE
-----
  python3 scripts/ingest/cms_hospice.py --inspect     Show the dataset's columns and exit
  python3 scripts/ingest/cms_hospice.py               Full run against live CMS data
  python3 scripts/ingest/cms_hospice.py --fixture F   Run against a local CSV (for testing)

DESIGN NOTE
-----------
CMS renames columns between releases ("County Name" became "County/Parish", "City"
became "City/Town"). So we never hard-code column names - we resolve them by pattern
and fail loudly with the actual header list if we can't. A confusing crash with the
real column names printed is far better than silently producing empty output.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, timezone, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from normalize import (  # noqa: E402
    normalize_phone, format_phone, normalize_county, normalize_org_name,
    clean_text, normalize_zip, normalize_state, slugify,
)

# CMS Provider Data Catalog - "Hospice - General Information"
CMS_DATASET_ID = "yc9t-dgbk"
CMS_METASTORE = (
    "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/"
    f"{CMS_DATASET_ID}?show-reference-ids=true"
)

USER_AGENT = "AWRLSupportBot/1.0 (+https://support.awellrunlife.com/bot)"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
METROS_PATH = os.path.join(REPO_ROOT, "data", "metros.json")
ORGS_PATH = os.path.join(REPO_ROOT, "data", "organizations.json")
REPORT_PATH = os.path.join(REPO_ROOT, "data", "coverage-report.md")

# Column patterns, in priority order. First match wins.
COLUMN_PATTERNS = {
    "ccn":       [r"^cms certification number", r"\bccn\b", r"^provider (id|number)"],
    "name":      [r"^facility name", r"^provider name", r"^legal business name", r"\bname\b"],
    "address":   [r"^address line 1", r"^address$", r"^provider address"],
    "address2":  [r"^address line 2"],
    "city":      [r"^city/town", r"^city$", r"^provider city"],
    "state":     [r"^state$", r"^provider state", r"^state abbreviation"],
    "zip":       [r"^zip ?code", r"^zip$", r"^provider zip"],
    "county":    [r"^county/parish", r"^county name", r"^county$"],
    "phone":     [r"^telephone number", r"^phone", r"^provider phone"],
    "ownership": [r"^ownership type", r"^type of ownership"],
    "cert_date": [r"^certification date", r"^date certified"],
}


def log(msg):
    print(msg, flush=True)


def http_get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def find_csv_url():
    """
    Ask the CMS metastore where the current CSV lives.

    The direct download URL contains a content hash that changes every release,
    so hard-coding it would break within months. The dataset ID is stable.
    """
    log(f"Looking up current CSV location for dataset {CMS_DATASET_ID}...")
    meta = json.loads(http_get(CMS_METASTORE).decode("utf-8"))

    candidates = []
    for dist in meta.get("distribution", []):
        data = dist.get("data", dist)
        url = data.get("downloadURL") or data.get("accessURL")
        fmt = (data.get("format") or data.get("mediaType") or "").lower()
        if url and ("csv" in fmt or url.lower().endswith(".csv")):
            candidates.append(url)

    if not candidates:
        raise RuntimeError(
            "No CSV distribution found in the CMS metastore response.\n"
            f"Response keys were: {sorted(meta.keys())}\n"
            "The API shape may have changed - inspect the response by hand."
        )

    log(f"  Found: {candidates[0]}")
    return candidates[0]


def resolve_columns(headers):
    """
    Map our field names onto the dataset's actual column names.

    Returns (mapping, missing). We tolerate missing optional columns but the
    caller decides whether the required ones are present.
    """
    mapping = {}
    lowered = [(h, h.strip().lower()) for h in headers]

    for field, patterns in COLUMN_PATTERNS.items():
        for pattern in patterns:
            match = next((orig for orig, low in lowered if re.search(pattern, low)), None)
            if match:
                mapping[field] = match
                break

    required = ["name", "city", "state", "county"]
    missing = [f for f in required if f not in mapping]
    return mapping, missing


def load_metros():
    with open(METROS_PATH) as fh:
        metros = json.load(fh)["metros"]

    # Build a lookup: (STATE, NORMALIZED COUNTY) -> metro_id
    # State-scoped because county names repeat across states (Lake County exists
    # in both Illinois and Indiana, in two different metros).
    county_index = {}
    collisions = []
    for metro in metros:
        if metro.get("virtual"):
            continue
        for state, counties in (metro.get("counties") or {}).items():
            for county in counties:
                key = (state.upper(), normalize_county(county))
                if key in county_index and county_index[key] != metro["id"]:
                    collisions.append((key, county_index[key], metro["id"]))
                county_index[key] = metro["id"]

    if collisions:
        log("WARNING: the same county is claimed by two metros:")
        for key, first, second in collisions:
            log(f"  {key} -> {first} AND {second}")

    return metros, county_index


def read_rows(source_bytes):
    text = source_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return reader.fieldnames or [], list(reader)


def build_organization(row, cols, metro_id):
    """Turn one CSV row into one organization record."""
    name = clean_text(row.get(cols["name"]))
    if not name:
        return None

    state = normalize_state(row.get(cols["state"]))
    city = clean_text(row.get(cols["city"]))
    phone_digits = normalize_phone(row.get(cols.get("phone", ""), ""))

    address_parts = [
        clean_text(row.get(cols.get("address", ""), "")),
        clean_text(row.get(cols.get("address2", ""), "")),
    ]
    street = " ".join(p for p in address_parts if p) or None

    return {
        "org_id": f"cms-{clean_text(row.get(cols.get('ccn',''), '')) or slugify(name)}",
        "name": name,
        "name_normalized": normalize_org_name(name),
        "org_type": "hospice",
        "street": street,
        "city": city,
        "state": state,
        "zip": normalize_zip(row.get(cols.get("zip", ""), "")),
        "county": normalize_county(row.get(cols["county"])),
        "metro_id": metro_id,
        "phone": format_phone(phone_digits),
        "phone_normalized": phone_digits,
        "ownership_type": clean_text(row.get(cols.get("ownership", ""), "")),
        "certification_date": clean_text(row.get(cols.get("cert_date", ""), "")),
        # Filled in by the website discovery step, which runs next.
        "website": None,
        "website_status": "not_checked",
        "bereavement_page": None,
        "source": "CMS Hospice - General Information",
        "source_dataset_id": CMS_DATASET_ID,
        "first_seen": date.today().isoformat(),
        "last_checked": date.today().isoformat(),
    }


def write_report(stats, unmatched_counties, metros, total_rows, csv_url):
    lines = []
    lines.append("# CMS Hospice Ingest - Coverage Report\n")
    lines.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n")
    lines.append(f"**Source:** `{csv_url}`\n")
    lines.append(f"**Rows in dataset:** {total_rows:,}")
    lines.append(f"**Matched to our metros:** {sum(stats.values()):,}\n")

    lines.append("## Organizations per metro\n")
    lines.append("| Metro | Priority | Hospices found |")
    lines.append("|---|---|---|")
    by_id = {m["id"]: m for m in metros}
    for metro_id, count in sorted(stats.items(), key=lambda kv: -kv[1]):
        metro = by_id.get(metro_id, {})
        lines.append(f"| {metro.get('display', metro_id)} | {metro.get('priority','-')} | {count} |")

    empty = [m for m in metros
             if not m.get("virtual") and stats.get(m["id"], 0) == 0]
    if empty:
        lines.append("\n### Metros with ZERO matches\n")
        lines.append("These almost certainly indicate a county-list error, not an absence of hospices.\n")
        for m in empty:
            lines.append(f"- **{m['display']}** - counties configured: "
                         f"{', '.join(sorted(c for cs in m.get('counties',{}).values() for c in cs))}")

    lines.append("\n## Possible gaps: unmatched counties in our states\n")
    lines.append("Counties in states we cover that did NOT map to any metro, ranked by hospice count. ")
    lines.append("A high count near one of our metros suggests a missing county. Many will legitimately ")
    lines.append("be rural areas outside any metro - that is expected.\n")
    lines.append("| State | County | Hospices |")
    lines.append("|---|---|---|")
    for (state, county), count in unmatched_counties.most_common(40):
        lines.append(f"| {state} | {county} | {count} |")

    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Ingest CMS hospice data into our metros.")
    parser.add_argument("--inspect", action="store_true",
                        help="Print the dataset's columns and a sample row, then exit.")
    parser.add_argument("--fixture", metavar="PATH",
                        help="Read from a local CSV instead of the live CMS API.")
    args = parser.parse_args()

    # ---- Load source ------------------------------------------------------
    if args.fixture:
        log(f"Reading fixture: {args.fixture}")
        with open(args.fixture, "rb") as fh:
            raw = fh.read()
        csv_url = f"fixture:{args.fixture}"
    else:
        csv_url = find_csv_url()
        log("Downloading CSV...")
        raw = http_get(csv_url)
        log(f"  {len(raw):,} bytes")

    headers, rows = read_rows(raw)
    log(f"Parsed {len(rows):,} rows, {len(headers)} columns")

    cols, missing = resolve_columns(headers)

    if args.inspect:
        log("\n=== COLUMNS IN DATASET ===")
        for h in headers:
            log(f"  {h}")
        log("\n=== RESOLVED MAPPING ===")
        for field in COLUMN_PATTERNS:
            log(f"  {field:12s} -> {cols.get(field, '*** NOT FOUND ***')}")
        if rows:
            log("\n=== FIRST ROW ===")
            for k, v in list(rows[0].items())[:20]:
                log(f"  {k}: {v}")
        return 0

    if missing:
        log("\nFATAL: could not find required columns: " + ", ".join(missing))
        log("Actual columns in the dataset were:")
        for h in headers:
            log(f"  {h}")
        log("\nFix: add a matching pattern to COLUMN_PATTERNS at the top of this file.")
        return 1

    log("Column mapping:")
    for field in COLUMN_PATTERNS:
        log(f"  {field:12s} -> {cols.get(field, '(not present)')}")

    # ---- Match to metros --------------------------------------------------
    metros, county_index = load_metros()
    our_states = {s for m in metros for s in m.get("states", [])}

    organizations = []
    stats = Counter()
    unmatched_counties = Counter()
    skipped_no_name = 0

    for row in rows:
        state = normalize_state(row.get(cols["state"]))
        county = normalize_county(row.get(cols["county"]))
        if not state or not county:
            continue

        metro_id = county_index.get((state, county))
        if not metro_id:
            if state in our_states:
                unmatched_counties[(state, county)] += 1
            continue

        org = build_organization(row, cols, metro_id)
        if not org:
            skipped_no_name += 1
            continue
        organizations.append(org)
        stats[metro_id] += 1

    # ---- Deduplicate ------------------------------------------------------
    # Same hospice can appear more than once (multiple CCNs at one address).
    seen = {}
    deduped = []
    for org in organizations:
        key = org["phone_normalized"] or f"{org['name_normalized']}|{org['city']}|{org['state']}"
        if key in seen:
            continue
        seen[key] = True
        deduped.append(org)

    duplicates_removed = len(organizations) - len(deduped)
    deduped.sort(key=lambda o: (o["metro_id"], o["name"]))

    # ---- Write ------------------------------------------------------------
    os.makedirs(os.path.dirname(ORGS_PATH), exist_ok=True)
    with open(ORGS_PATH, "w") as fh:
        json.dump(deduped, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    stats_after = Counter(o["metro_id"] for o in deduped)
    write_report(stats_after, unmatched_counties, metros, len(rows), csv_url)

    # ---- Summary ----------------------------------------------------------
    log("\n" + "=" * 60)
    log(f"  Rows in dataset:        {len(rows):,}")
    log(f"  Matched to our metros:  {len(organizations):,}")
    log(f"  Duplicates removed:     {duplicates_removed:,}")
    log(f"  Rows without a name:    {skipped_no_name:,}")
    log(f"  ORGANIZATIONS WRITTEN:  {len(deduped):,}")
    log("=" * 60)
    log(f"\nTop metros:")
    for metro_id, count in stats_after.most_common(10):
        log(f"  {metro_id:20s} {count:4d}")

    empty = [m["id"] for m in metros if not m.get("virtual") and stats_after.get(m["id"], 0) == 0]
    if empty:
        log(f"\nWARNING: {len(empty)} metros matched nothing: {', '.join(empty)}")
        log("Check their county lists in data/metros.json against the coverage report.")

    log(f"\nWrote {ORGS_PATH}")
    log(f"Wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
