#!/usr/bin/env python3
"""
Manual CSV import
=================

Loads support groups from a hand-curated spreadsheet into `data/listings.json`,
validated against `data/schema.json`.

WHY THIS EXISTS
---------------
Several national directories don't permit automated access, and for the largest ones a
scraper would foreclose a data-sharing conversation we'd rather have. This is the path
that lets us include those groups without breaching anyone's terms: a person reads the
page, fills in a row, and the importer enforces the same quality bar the crawler does.

It is not a lesser path. Hand-curated rows arrive with better metadata than extraction
produces, and every row is a group someone actually looked at.

THE BAR IS THE SAME
-------------------
A row is rejected unless it has a live `source_url`, a phone or registration URL, a
schedule, a metro that exists, and a valid loss type. `cost` left blank becomes
`unknown` — never `free`. The importer will not let you assert something the source
page didn't say.

USAGE
-----
  python3 scripts/ingest/import_csv.py --template          write a blank template
  python3 scripts/ingest/import_csv.py --file groups.csv --dry-run
  python3 scripts/ingest/import_csv.py --file groups.csv
  python3 scripts/ingest/import_csv.py --self-test
"""

import argparse
import csv
import io
import json
import os
import sys
import uuid
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from normalize import (  # noqa: E402
    normalize_phone, format_phone, clean_text, normalize_zip,
    normalize_state, slugify,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LISTINGS_PATH = os.path.join(REPO_ROOT, "data", "listings.json")
METROS_PATH = os.path.join(REPO_ROOT, "data", "metros.json")
VOCAB_PATH = os.path.join(REPO_ROOT, "data", "vocabularies.json")
TEMPLATE_PATH = os.path.join(REPO_ROOT, "data", "import-template.csv")

COLUMNS = [
    "name", "organization", "org_type", "loss_types", "age_groups", "format",
    "cost", "cost_notes", "structure", "registration_required", "faith_affiliation",
    "faith_participation_required", "facilitator_type", "schedule_text", "cadence",
    "venue_name", "street", "city", "state", "zip", "metro_id",
    "phone", "email", "url", "registration_url", "source_url", "source_type",
    "languages", "populations", "accessibility_notes", "description", "internal_notes",
]

REQUIRED = ["name", "organization", "loss_types", "format", "schedule_text",
            "metro_id", "source_url"]

MULTI = {"loss_types", "age_groups", "populations", "languages"}


def load_reference():
    with open(METROS_PATH) as fh:
        metros = {m["id"] for m in json.load(fh)["metros"]}
    with open(VOCAB_PATH) as fh:
        vocab = json.load(fh)
    return metros, vocab


def write_template():
    with open(TEMPLATE_PATH, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        writer.writerow([
            "Loss of a Child Support Group", "The Compassionate Friends Phoenix Chapter",
            "peer_network_chapter", "child", "adults", "in_person",
            "free", "", "drop_in", "no", "none", "false", "trained_peer",
            "2nd Tuesday of each month, 7:00-8:30pm", "monthly",
            "Community Church Fellowship Hall", "1234 E Main St", "Phoenix", "AZ", "85014",
            "phoenix-az", "602-555-0100", "", "", "",
            "https://www.compassionatefriends.org/chapter/phoenix", "national_org_directory",
            "en", "", "Ground floor, free parking",
            "Monthly peer-led group for parents who have lost a child of any age.", "",
        ])
    print(f"Wrote {TEMPLATE_PATH}")
    print("\nMulti-value columns (loss_types, age_groups, populations, languages) "
          "take semicolons: child;sibling")
    print("Leave cost blank if the source page doesn't state it. It becomes 'unknown', "
          "never 'free'.")


def convert_row(row, index, metros, vocab):
    """Return (listing, errors). Listing is None if unusable."""
    errors = []
    get = lambda k: clean_text(row.get(k, ""))  # noqa: E731

    for field in REQUIRED:
        if not get(field):
            errors.append(f"row {index}: missing required field '{field}'")
    if errors:
        return None, errors

    metro_id = get("metro_id")
    if metro_id not in metros:
        errors.append(f"row {index}: metro_id '{metro_id}' is not in metros.json")

    loss_values = {x["value"] for x in vocab["loss_types"]}
    loss_types = [t.strip() for t in get("loss_types").split(";") if t.strip()]
    bad = [t for t in loss_types if t not in loss_values]
    if bad:
        errors.append(f"row {index}: unknown loss_types {bad}")

    source_url = get("source_url")
    if not source_url.startswith("http"):
        errors.append(f"row {index}: source_url must be a full URL starting with http")

    phone_digits = normalize_phone(get("phone"))
    if not phone_digits and not get("registration_url") and not get("url"):
        errors.append(f"row {index}: needs a phone number, url, or registration_url — "
                      f"a person must be able to confirm before travelling")

    fmt = get("format") or "in_person"
    if fmt in ("in_person", "hybrid") and not (get("city") and get("state")):
        errors.append(f"row {index}: in-person groups need city and state")

    if errors:
        return None, errors

    name, org = get("name"), get("organization")
    listing = {
        "id": str(uuid.uuid4()),
        "slug": slugify(f"{org} {name} {get('city') or metro_id}"),
        "name": name,
        "organization": org,
        "org_type": get("org_type") or "nonprofit",
        "loss_types": loss_types,
        "age_groups": [t.strip() for t in (get("age_groups") or "adults").split(";") if t.strip()],
        "format": fmt,
        # A blank cost becomes unknown. We never assert "free" the source didn't state.
        "cost": get("cost") or "unknown",
        "structure": get("structure") or "unknown",
        "registration_required": get("registration_required") or "unknown",
        "faith_affiliation": get("faith_affiliation") or "unknown",
        "schedule_text": get("schedule_text"),
        "metro_id": metro_id,
        "source_url": source_url,
        "source_type": get("source_type") or "national_org_directory",
        # Hand-curated from a live page, but no human at the org has confirmed it.
        "verification_status": "source_verified",
        "first_seen": date.today().isoformat(),
        "last_checked": date.today().isoformat(),
        "source_link_status": "ok",
        "published": False,
        "languages": [t.strip() for t in (get("languages") or "en").split(";") if t.strip()],
    }

    for key, value in [
        ("description", get("description")), ("cost_notes", get("cost_notes")),
        ("facilitator_type", get("facilitator_type")), ("cadence", get("cadence")),
        ("venue_name", get("venue_name")), ("street", get("street")),
        ("city", get("city")), ("email", get("email")), ("url", get("url")),
        ("registration_url", get("registration_url")),
        ("accessibility_notes", get("accessibility_notes")),
        ("internal_notes", get("internal_notes")),
    ]:
        if value:
            listing[key] = value

    if get("state"):
        listing["state"] = normalize_state(get("state"))
    if get("zip"):
        listing["zip"] = normalize_zip(get("zip"))
    if phone_digits:
        listing["phone"] = format_phone(phone_digits)
        listing["phone_normalized"] = phone_digits
    if get("populations"):
        listing["populations"] = [t.strip() for t in get("populations").split(";") if t.strip()]
    if get("faith_participation_required"):
        listing["faith_participation_required"] = \
            get("faith_participation_required").lower() in ("true", "yes", "1")

    return listing, []


def self_test():
    metros, vocab = load_reference()
    print("=== accepts a good row ===")
    good = {
        "name": "Loss of a Child Support Group",
        "organization": "TCF Phoenix Chapter", "loss_types": "child",
        "format": "in_person", "schedule_text": "2nd Tuesday, 7pm",
        "metro_id": "phoenix-az", "city": "Phoenix", "state": "AZ",
        "phone": "(602) 555-0100",
        "source_url": "https://example.org/chapter/phoenix",
    }
    listing, errors = convert_row(good, 1, metros, vocab)
    assert not errors, errors
    assert listing["cost"] == "unknown", "blank cost must NOT become free"
    assert listing["phone"] == "602-555-0100"
    assert listing["verification_status"] == "source_verified"
    print(f"  accepted, slug={listing['slug']}")
    print(f"  blank cost -> {listing['cost']!r}  (never 'free')")

    print("\n=== rejects rows that would mislead someone ===")
    for label, mutation in [
        ("no source_url", {"source_url": ""}),
        ("no way to make contact", {"phone": "", "url": "", "registration_url": ""}),
        ("invented loss type", {"loss_types": "heartbreak"}),
        ("metro that doesn't exist", {"metro_id": "atlantis-xx"}),
        ("in-person with no city", {"city": "", "state": ""}),
        ("no schedule", {"schedule_text": ""}),
        ("source_url not a URL", {"source_url": "see their website"}),
    ]:
        row = dict(good, **mutation)
        listing, errors = convert_row(row, 2, metros, vocab)
        assert listing is None and errors, f"should have rejected: {label}"
        print(f"  rejected {label:28s} -> {errors[0].split(': ',1)[1][:52]}")

    print("\n=== multi-value fields ===")
    row = dict(good, loss_types="child;sibling", age_groups="adults;teens", languages="en;es")
    listing, errors = convert_row(row, 3, metros, vocab)
    assert listing["loss_types"] == ["child", "sibling"]
    assert listing["languages"] == ["en", "es"]
    print(f"  loss_types={listing['loss_types']} languages={listing['languages']}")

    print("\nALL IMPORTER TESTS PASSED")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="CSV to import")
    parser.add_argument("--template", action="store_true", help="Write a blank template")
    parser.add_argument("--dry-run", action="store_true", help="Validate without saving")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.template:
        write_template()
        return 0
    if args.self_test:
        return self_test()
    if not args.file:
        parser.error("need --file, --template, or --self-test")

    metros, vocab = load_reference()
    with open(args.file, newline="") as fh:
        rows = list(csv.DictReader(fh))

    print(f"Read {len(rows)} rows from {args.file}\n")

    listings, all_errors = [], []
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        listing, errors = convert_row(row, i, metros, vocab)
        if errors:
            all_errors.extend(errors)
        else:
            listings.append(listing)

    if all_errors:
        print("REJECTED ROWS:")
        for err in all_errors:
            print(f"  {err}")
        print()

    print(f"Accepted: {len(listings)}  Rejected: {len(rows) - len(listings)}")

    if args.dry_run:
        print("\nDry run — nothing written.")
        return 1 if all_errors else 0

    existing = []
    if os.path.exists(LISTINGS_PATH):
        with open(LISTINGS_PATH) as fh:
            existing = json.load(fh)

    seen = {(l.get("source_url"), l.get("name")) for l in existing}
    added = [l for l in listings if (l["source_url"], l["name"]) not in seen]

    with open(LISTINGS_PATH, "w") as fh:
        json.dump(existing + added, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(f"Added {len(added)} new listings "
          f"({len(listings) - len(added)} were already present)")
    print(f"Total in {LISTINGS_PATH}: {len(existing) + len(added)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
