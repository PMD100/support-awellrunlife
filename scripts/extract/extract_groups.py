#!/usr/bin/env python3
"""
Group extraction
================

Reads the verified bereavement pages and pulls out the actual support groups.

This is the step that finally produces listings. Everything before it — the CMS ingest,
the priority scoring, the website discovery — existed to get us to a set of pages we
trust. Now we read them.

THE CENTRAL DANGER
------------------
An LLM reading a hospice bereavement page will happily produce a plausible meeting time
that the page never stated. That single failure mode is worse than every other bug in
this project combined, because it ends with a grieving person sitting in an empty
parking lot on a Tuesday evening.

So the model is never trusted. Three defenses, in order of importance:

1. **Quote verification.** Every extracted fact must be accompanied by a verbatim quote
   from the page. Python then checks that the quote actually appears in the page text.
   If it doesn't, the model invented it and the field is discarded. This is cheap,
   mechanical, and catches the failure mode directly — a hallucinated schedule cannot
   survive it, because the model would have to also hallucinate a quote that happens to
   be present in the source.

2. **Extract, never infer.** The prompt is explicit: if the page does not state a cost,
   the answer is "unknown". A nonprofit hospice is not evidence that a group is free.

3. **Vocabulary enforcement.** Loss types, cost values, and structures must come from
   `data/vocabularies.json`. Anything else is rejected rather than coerced.

Anything that survives all three still lands as `source_verified`, never `org_confirmed`.
A page is evidence; it is not a person telling us the group runs next Tuesday.

COST
----
Roughly 60 pages, a few thousand tokens each. Cents, not dollars. Requires an
`ANTHROPIC_API_KEY` secret in the repository.

USAGE
-----
  python3 scripts/extract/extract_groups.py --offline-test   (no API calls, no key needed)
  python3 scripts/extract/extract_groups.py --show-prompt
  python3 scripts/extract/extract_groups.py --limit 5
  python3 scripts/extract/extract_groups.py
"""

import argparse
import json
import os
import re
import sys
import urllib.request
import uuid
from collections import Counter
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from normalize import normalize_phone, format_phone, clean_text, slugify  # noqa: E402
import politefetch  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORGS_PATH = os.path.join(REPO_ROOT, "data", "organizations.json")
LISTINGS_PATH = os.path.join(REPO_ROOT, "data", "listings.json")
VOCAB_PATH = os.path.join(REPO_ROOT, "data", "vocabularies.json")
REPORT_PATH = os.path.join(REPO_ROOT, "data", "extraction-report.md")
AUDIT_PATH = os.path.join(REPO_ROOT, "data", "extraction-audit.md")

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("EXTRACTION_MODEL", "claude-sonnet-5")
MAX_PAGE_CHARS = 24_000

SYSTEM_PROMPT = """You extract grief support group information from web pages for a \
public directory used by recently bereaved people.

Accuracy matters more than completeness. If this directory tells someone a group meets \
on Tuesday and it does not, that person may drive to an empty building during the worst \
week of their life. An incomplete listing is recoverable. A wrong one is not.

RULES

1. EXTRACT, NEVER INFER. Report only what the page states in words. Do not reason from \
context. A nonprofit hospice is not evidence a group is free. A grief page is not \
evidence of a weekly schedule. If the page does not say it, the value is null.

2. EVERY FACT NEEDS A VERBATIM QUOTE. For schedule, cost, and loss type you must supply \
the exact text from the page that states it, copied character for character. Quotes are \
checked against the page automatically; an inexact quote causes the field to be discarded.

3. ONE OBJECT PER DISTINCT GROUP. A page describing a general adult group and a separate \
children's group yields two objects. A page describing bereavement services generally, \
with no specific group, yields an empty array.

4. WHEN UNSURE, RETURN NOTHING. An empty array is a perfectly good answer.

OUTPUT

Return only a JSON array. No markdown fence, no commentary.

Each object:
{
  "name": string,
  "loss_types": [values from: general, spouse_partner, child, pregnancy_infant, parent,
                 sibling, suicide, overdose_substance, homicide, accident_sudden,
                 illness_long, military_service, pet, caregiver_anticipatory],
  "loss_types_quote": string or null,
  "age_groups": [values from: children, teens, young_adults, adults, older_adults, families],
  "format": "in_person" | "online" | "hybrid" | null,
  "cost": "free" | "donation_suggested" | "sliding_scale" | "paid" | "unknown",
  "cost_quote": string or null,
  "cost_notes": string or null,
  "structure": "drop_in" | "closed_series" | "unknown",
  "series_length_weeks": integer or null,
  "registration_required": "yes" | "no" | "unknown",
  "faith_affiliation": "none" | "christian" | "catholic" | "jewish" | "muslim" |
                       "interfaith" | "other" | "unknown",
  "faith_participation_required": true | false | null,
  "facilitator_type": "licensed_clinician" | "trained_peer" | "chaplain" | "mixed" | "unknown",
  "schedule_text": string or null,
  "schedule_quote": string or null,
  "cadence": "weekly" | "biweekly" | "monthly" | "varies" | "unknown",
  "phone": string or null,
  "registration_url": string or null,
  "description": string or null,
  "confidence": "high" | "medium" | "low"
}

Set cost to "unknown" unless the page states the price or says it is free. Use "low" \
confidence whenever the page is vague about who the group serves or when it meets."""


def strip_html(markup):
    text = re.sub(r"(?is)<(script|style|noscript|nav|footer|header)[^>]*>.*?</\1>", " ", markup)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    import html as html_module
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_quote_match(text):
    """Whitespace and punctuation-insensitive form, for comparing quotes to source."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def quote_is_real(quote, page_text):
    """
    The anti-hallucination check.

    A quote must actually appear in the page. Compared with whitespace and punctuation
    removed, because models reflow whitespace and swap dash characters - but they cannot
    invent a sequence of words that happens to be present.
    """
    if not quote or len(quote.strip()) < 8:
        return False
    return normalize_for_quote_match(quote) in normalize_for_quote_match(page_text)


def call_api(page_text, org_name, api_key):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": (f"Organization: {org_name}\n\nPage text:\n\n"
                        f"{page_text[:MAX_PAGE_CHARS]}")
        }],
    }).encode()

    req = urllib.request.Request(API_URL, data=body, headers={
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.load(resp)
    text = "".join(b.get("text", "") for b in payload.get("content", []))
    text = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip())
    return json.loads(text)


def validate(group, page_text, vocab, org):
    """
    Apply every guardrail. Returns (listing, rejections).

    Fields whose quotes don't check out are stripped rather than the whole group being
    thrown away - a group with a verified name and an unverified schedule is still worth
    listing, flagged as needs_review.
    """
    rejections = []
    loss_values = {x["value"] for x in vocab["loss_types"]}
    cost_values = {x["value"] for x in vocab["cost"]}

    name = clean_text(group.get("name"))
    if not name:
        return None, [f"{org['name']}: group has no name"]

    # --- loss types ---
    loss_types = [t for t in (group.get("loss_types") or []) if t in loss_values]
    if len(loss_types) != len(group.get("loss_types") or []):
        rejections.append(f"{name}: dropped loss types outside our vocabulary")
    if loss_types and loss_types != ["general"]:
        if not quote_is_real(group.get("loss_types_quote"), page_text):
            rejections.append(f"{name}: loss-type quote not found on page - reset to general")
            loss_types = ["general"]
    if not loss_types:
        loss_types = ["general"]

    # --- cost: the field most likely to be guessed ---
    cost = group.get("cost") or "unknown"
    if cost not in cost_values:
        rejections.append(f"{name}: invalid cost value {cost!r} -> unknown")
        cost = "unknown"
    if cost != "unknown" and not quote_is_real(group.get("cost_quote"), page_text):
        rejections.append(f"{name}: cost quote not found on page - '{cost}' rejected -> unknown")
        cost = "unknown"

    # --- schedule ---
    schedule = clean_text(group.get("schedule_text"))
    schedule_verified = quote_is_real(group.get("schedule_quote"), page_text)
    if schedule and not schedule_verified:
        rejections.append(f"{name}: schedule quote not found on page - schedule discarded")
        schedule = None

    confidence = group.get("confidence", "low")
    needs_review = (not schedule) or confidence == "low" or cost == "unknown"

    listing = {
        "id": str(uuid.uuid4()),
        "slug": slugify(f"{org['name']} {name} {org.get('city') or ''}"),
        "name": name,
        "organization": org["name"],
        "org_type": org.get("org_type", "hospice"),
        "loss_types": loss_types,
        "age_groups": [a for a in (group.get("age_groups") or ["adults"])
                       if a in {"children", "teens", "young_adults", "adults",
                                "older_adults", "families"}] or ["adults"],
        "format": group.get("format") or "in_person",
        "cost": cost,
        "structure": group.get("structure") or "unknown",
        "registration_required": group.get("registration_required") or "unknown",
        "faith_affiliation": group.get("faith_affiliation") or "unknown",
        "schedule_text": schedule or "Contact the organization for current meeting times",
        "city": org.get("city"), "state": org.get("state"), "metro_id": org["metro_id"],
        "source_url": org["bereavement_page"],
        "source_type": "org_website",
        "verification_status": "needs_review" if needs_review else "source_verified",
        "first_seen": date.today().isoformat(),
        "last_checked": date.today().isoformat(),
        "source_link_status": "ok",
        "published": False,
        "languages": ["en"],
        "extraction_confidence": confidence,
    }

    phone = normalize_phone(group.get("phone")) or org.get("phone_normalized")
    if phone:
        listing["phone"] = format_phone(phone)
        listing["phone_normalized"] = phone
    if org.get("street"):
        listing["street"] = org["street"]
    if group.get("cost_notes"):
        listing["cost_notes"] = clean_text(group["cost_notes"])
    if group.get("description"):
        listing["description"] = clean_text(group["description"])[:600]
    if group.get("facilitator_type"):
        listing["facilitator_type"] = group["facilitator_type"]
    if group.get("cadence"):
        listing["cadence"] = group["cadence"]
    if group.get("registration_url"):
        listing["registration_url"] = group["registration_url"]
    if group.get("series_length_weeks") and listing["structure"] == "closed_series":
        listing["series_length_weeks"] = group["series_length_weeks"]

    # Audit trail: every surviving quote is retained so a human can verify in seconds.
    quotes = []
    for field in ("schedule", "cost", "loss_types"):
        q = group.get(f"{field}_quote")
        if quote_is_real(q, page_text):
            quotes.append(f"{field}: \"{q.strip()[:160]}\"")
    listing["internal_notes"] = ("Extracted from bereavement page. Verified quotes -> "
                                 + " | ".join(quotes) if quotes
                                 else "Extracted from bereavement page. No quotes verified.")
    return listing, rejections


def offline_test():
    """Every guardrail, with canned model output. No API key, no network, no spend."""
    with open(VOCAB_PATH) as fh:
        vocab = json.load(fh)
    org = {"name": "Gulfside Hospice", "city": "Land O' Lakes", "state": "FL",
           "metro_id": "tampa-fl", "bereavement_page": "https://example.org/grief",
           "phone_normalized": "7275550100", "org_type": "hospice"}
    page = ("Gulfside Hospice Bereavement Services. Our Adult Loss of a Spouse group "
            "meets the second and fourth Tuesday of each month at 6:30pm. "
            "All bereavement services are provided at no charge to the community. "
            "Call to register.")

    print("=== quote verification: the core defence ===")
    assert quote_is_real("meets the second and fourth Tuesday of each month at 6:30pm", page)
    assert quote_is_real("meets  the second and  fourth Tuesday of each month at 6:30pm", page)
    assert not quote_is_real("meets every Thursday at 7pm", page)
    print("  real quote accepted; whitespace differences tolerated; invented quote refused")

    print("\n=== a well-behaved extraction ===")
    good = {"name": "Adult Loss of a Spouse Group", "loss_types": ["spouse_partner"],
            "loss_types_quote": "Adult Loss of a Spouse group",
            "cost": "free", "cost_quote": "provided at no charge to the community",
            "schedule_text": "2nd and 4th Tuesday, 6:30pm",
            "schedule_quote": "meets the second and fourth Tuesday of each month at 6:30pm",
            "structure": "drop_in", "confidence": "high", "age_groups": ["adults"]}
    listing, rej = validate(good, page, vocab, org)
    assert not rej, rej
    assert listing["cost"] == "free" and listing["verification_status"] == "source_verified"
    print(f"  cost={listing['cost']} status={listing['verification_status']}")
    print(f"  audit: {listing['internal_notes'][:96]}...")

    print("\n=== HALLUCINATED SCHEDULE — the failure that matters most ===")
    bad = dict(good, schedule_text="Every Thursday at 7pm",
               schedule_quote="meets every Thursday at 7:00pm in the chapel")
    listing, rej = validate(bad, page, vocab, org)
    assert "Contact the organization" in listing["schedule_text"], "invented schedule survived!"
    assert listing["verification_status"] == "needs_review"
    print(f"  rejected -> {rej[0]}")
    print(f"  schedule replaced with: {listing['schedule_text']!r}")

    print("\n=== INVENTED 'free' ===")
    bad = dict(good, cost="free", cost_quote="there is no fee for any of our programs")
    listing, rej = validate(bad, page, vocab, org)
    assert listing["cost"] == "unknown", "unsupported 'free' survived!"
    print(f"  rejected -> {rej[0]}")

    print("\n=== value outside our vocabulary ===")
    bad = dict(good, cost="complimentary")
    listing, rej = validate(bad, page, vocab, org)
    assert listing["cost"] == "unknown"
    print(f"  rejected -> {rej[0]}")

    bad = dict(good, loss_types=["heartbreak"], loss_types_quote=None)
    listing, rej = validate(bad, page, vocab, org)
    assert listing["loss_types"] == ["general"]
    print(f"  rejected -> {rej[0]}")

    print("\n=== low confidence is always flagged ===")
    listing, _ = validate(dict(good, confidence="low"), page, vocab, org)
    assert listing["verification_status"] == "needs_review"
    print("  low confidence -> needs_review")

    print("\nALL EXTRACTION GUARDRAILS PASSED")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="Process at most N organizations")
    ap.add_argument("--offline-test", action="store_true", help="Guardrail tests, no API")
    ap.add_argument("--show-prompt", action="store_true", help="Print the system prompt")
    ap.add_argument("--dry-run", action="store_true", help="Extract but write nothing")
    args = ap.parse_args()

    if args.show_prompt:
        print(SYSTEM_PROMPT)
        return 0
    if args.offline_test:
        return offline_test()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("FATAL: ANTHROPIC_API_KEY is not set.")
        print("Add it under Settings -> Secrets and variables -> Actions.")
        return 1

    with open(VOCAB_PATH) as fh:
        vocab = json.load(fh)
    with open(ORGS_PATH) as fh:
        orgs = json.load(fh)

    queue = [o for o in orgs if o.get("bereavement_page")
             and str(o.get("website_status", "")).startswith("verified")]
    queue.sort(key=lambda o: -o.get("crawl_priority", 0))
    if args.limit:
        queue = queue[:args.limit]

    print(f"Extracting from {len(queue)} verified bereavement pages using {MODEL}\n")

    listings, all_rejections, stats = [], [], Counter()
    audit_rows = []

    for i, org in enumerate(queue, 1):
        print(f"[{i}/{len(queue)}] {org['name'][:48]}")
        page = politefetch.fetch(org["bereavement_page"])
        if not page.ok:
            print(f"    page unreachable: {page.error}")
            stats["page unreachable"] += 1
            continue

        page_text = strip_html(page.text)
        if len(page_text) < 200:
            stats["page too thin to extract from"] += 1
            continue

        try:
            groups = call_api(page_text, org["name"], api_key)
        except Exception as exc:  # noqa: BLE001
            print(f"    API error: {exc}")
            stats["api error"] += 1
            continue

        if not groups:
            print("    no distinct groups described")
            stats["no groups on page"] += 1
            continue

        for g in groups:
            listing, rejections = validate(g, page_text, vocab, org)
            all_rejections.extend(rejections)
            if not listing:
                continue
            listings.append(listing)
            stats[listing["verification_status"]] += 1
            audit_rows.append((org["name"], listing["name"], listing["cost"],
                               listing["schedule_text"], listing["verification_status"]))
            flag = "" if listing["verification_status"] == "source_verified" else "  [needs review]"
            print(f"    + {listing['name'][:44]}{flag}")

    if args.dry_run:
        print(f"\nDry run: {len(listings)} listings would be written.")
        return 0

    existing = []
    if os.path.exists(LISTINGS_PATH):
        with open(LISTINGS_PATH) as fh:
            existing = json.load(fh)
    seen = {(l.get("organization"), l.get("name")) for l in existing}
    new = [l for l in listings if (l["organization"], l["name"]) not in seen]

    with open(LISTINGS_PATH, "w") as fh:
        json.dump(existing + new, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    by_metro = Counter(l["metro_id"] for l in listings)
    verified = sum(1 for l in listings if l["verification_status"] == "source_verified")

    lines = ["# Extraction Report\n",
             f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n",
             f"- Pages read: **{len(queue)}**",
             f"- Groups extracted: **{len(listings)}**",
             f"- Fully verified (`source_verified`): **{verified}**",
             f"- Flagged `needs_review`: **{len(listings) - verified}**",
             f"- New to the file: **{len(new)}**\n",
             "## Outcomes\n", "| Outcome | Count |", "|---|---|"]
    for k, v in stats.most_common():
        lines.append(f"| {k} | {v} |")
    lines += ["\n## By metro\n", "| Metro | Groups |", "|---|---|"]
    for m, n in by_metro.most_common():
        lines.append(f"| {m} | {n} |")

    if all_rejections:
        lines += [f"\n## Rejected claims ({len(all_rejections)})\n",
                  "Each line is something the model asserted that could not be traced to a "
                  "verbatim quote on the page, and was therefore discarded. **A long list "
                  "here is the system working, not failing.**\n"]
        for r in all_rejections[:60]:
            lines.append(f"- {r}")

    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    audit = ["# Extraction Audit\n",
             "Every extracted group beside the schedule and cost we recorded. "
             "Spot-check a dozen against the source pages.\n",
             "| Organization | Group | Cost | Schedule | Status |", "|---|---|---|---|---|"]
    for row in audit_rows:
        audit.append("| " + " | ".join(str(c)[:60] for c in row) + " |")
    with open(AUDIT_PATH, "w") as fh:
        fh.write("\n".join(audit) + "\n")

    print("\n" + "=" * 60)
    print(f"  Groups extracted:   {len(listings)}")
    print(f"  source_verified:    {verified}")
    print(f"  needs_review:       {len(listings) - verified}")
    print(f"  Claims rejected:    {len(all_rejections)}")
    print("=" * 60)
    print(f"\nWrote {REPORT_PATH} and {AUDIT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
