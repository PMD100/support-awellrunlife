#!/usr/bin/env python3
"""
Crawl prioritization
====================

Scores each organization by how likely it is to run a real community bereavement
program, so the crawler spends its time where listings actually are.

WHY THIS EXISTS
---------------
The first ingest returned 3,308 hospices, and the distribution was inverted from
what a bereavement-supply model predicts:

    Los Angeles     1,402 hospices
    Tampa               9 hospices
    Orlando             5 hospices

Tampa and Orlando have among the highest older-adult populations in the country.
They should be dense with bereavement programs, and they are - they just run through
a handful of very large, established nonprofits.

Two real-world forces explain the inversion:

1. **Fraud-driven proliferation.** Los Angeles County saw roughly a 1,500% increase
   in hospice agencies over a decade. California imposed a moratorium on new hospice
   licenses in 2021 (SB 664) after finding that 93% of applications came from LA and
   Southern California and 72% shared addresses with other applicants - one single
   LA address was tied to 191 separate applications. Those entities have no clinical
   staff, no community programs, and frequently no website.

2. **Certificate of Need laws.** Florida is one of ~12 states that restricts hospice
   entry through CON review. Florida ranks 2nd nationally in hospice patients served
   but 37th in number of providers. Its hospices are few, large, old, and exactly the
   kind that run free community grief groups.

**Conclusion: provider count is a poor proxy for group supply, and in fraud-affected
markets it is actively misleading.** Crawling 1,402 LA shells at one request per second
would burn 23 minutes to find approximately nothing.

THE SHARED-ADDRESS SIGNAL
-------------------------
The most useful detector falls straight out of the data. Legitimate hospices occupy
their own premises. Shell entities cluster at mail-drop addresses. So we count how many
organizations share each normalized street address and penalize hard above a small
threshold. No external data needed - the fraud pattern is visible in the file itself.

USAGE
-----
  python3 scripts/score_organizations.py
  python3 scripts/score_organizations.py --report-only    (change nothing, just print)
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ORGS_PATH = os.path.join(REPO_ROOT, "data", "organizations.json")
REPORT_PATH = os.path.join(REPO_ROOT, "data", "priority-report.md")

BASE_SCORE = 50

# Tier thresholds
TIER_HIGH = 65
TIER_MEDIUM = 40


def normalize_address(street, city, state):
    """Canonical address key for detecting shared premises."""
    if not street:
        return None
    s = f"{street} {city or ''} {state or ''}".upper()
    s = re.sub(r"\b(SUITE|STE|UNIT|APT|#|FLOOR|FL|BLDG|BUILDING)\b.*", "", s)
    s = re.sub(r"\b(STREET|ST|AVENUE|AVE|ROAD|RD|BOULEVARD|BLVD|DRIVE|DR|LANE|LN|COURT|CT|PLACE|PL|PARKWAY|PKWY|HIGHWAY|HWY)\b", "", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def parse_year(value):
    if not value:
        return None
    match = re.search(r"(19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


def score_organization(org, address_counts):
    """Return (score, list_of_signal_strings)."""
    score = BASE_SCORE
    signals = []

    # ---- Ownership -------------------------------------------------------
    ownership = (org.get("ownership_type") or "").lower()
    if any(k in ownership for k in ("non - profit", "nonprofit", "non-profit", "voluntary", "church", "religious")):
        score += 30
        signals.append("nonprofit (+30)")
    elif any(k in ownership for k in ("government", "state", "county", "city", "veteran", "federal")):
        score += 30
        signals.append("government (+30)")
    elif "proprietary" in ownership or "for - profit" in ownership or "for-profit" in ownership:
        score -= 20
        signals.append("for-profit (-20)")

    # ---- Age -------------------------------------------------------------
    # Established providers have community programs. Entities certified during the
    # 2019+ proliferation wave overwhelmingly do not.
    year = parse_year(org.get("certification_date"))
    if year:
        if year <= 2005:
            score += 20
            signals.append(f"certified {year}, long-established (+20)")
        elif year <= 2015:
            score += 10
            signals.append(f"certified {year} (+10)")
        elif year >= 2020:
            score -= 15
            signals.append(f"certified {year}, recent entrant (-15)")
    else:
        signals.append("no certification date")

    # ---- Shared address (the shell-entity detector) ----------------------
    key = normalize_address(org.get("street"), org.get("city"), org.get("state"))
    shared = address_counts.get(key, 1) if key else 1
    if shared >= 10:
        score -= 45
        signals.append(f"address shared with {shared - 1} others (-45)")
    elif shared >= 4:
        score -= 30
        signals.append(f"address shared with {shared - 1} others (-30)")
    elif shared >= 2:
        score -= 12
        signals.append(f"address shared with {shared - 1} other(s) (-12)")

    # ---- Name heuristics -------------------------------------------------
    name = (org.get("name") or "").lower()
    if any(k in name for k in ("hospital", "health system", "medical center", "healthcare system", "university")):
        score += 10
        signals.append("hospital/health system affiliated (+10)")

    return max(0, min(100, score)), signals


def tier_for(score):
    if score >= TIER_HIGH:
        return "high"
    if score >= TIER_MEDIUM:
        return "medium"
    return "low"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-only", action="store_true",
                        help="Print the analysis without modifying organizations.json")
    args = parser.parse_args()

    with open(ORGS_PATH) as fh:
        orgs = json.load(fh)

    if not orgs:
        print("organizations.json is empty - run the ingest first.")
        return 1

    print(f"Scoring {len(orgs):,} organizations...")

    # Count organizations per physical address
    address_counts = Counter()
    for org in orgs:
        key = normalize_address(org.get("street"), org.get("city"), org.get("state"))
        if key:
            address_counts[key] += 1

    shared_addresses = {k: v for k, v in address_counts.items() if v >= 4}
    print(f"  Addresses hosting 4+ organizations: {len(shared_addresses):,}")

    tiers = Counter()
    by_metro = defaultdict(Counter)
    for org in orgs:
        score, signals = score_organization(org, address_counts)
        tier = tier_for(score)
        org["crawl_priority"] = score
        org["crawl_tier"] = tier
        org["priority_signals"] = signals
        tiers[tier] += 1
        by_metro[org["metro_id"]][tier] += 1

    # ---- Report ----------------------------------------------------------
    lines = ["# Crawl Priority Report\n",
             f"_Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_\n",
             f"**Organizations scored:** {len(orgs):,}\n",
             "Scoring reflects likelihood of running a real community bereavement program. ",
             "See the docstring in `scripts/score_organizations.py` for why raw provider counts mislead.\n",
             "## Tier totals\n",
             "| Tier | Count | Share | Meaning |",
             "|---|---|---|---|"]
    for tier, label in [("high", "Crawl first - established nonprofits, hospital systems"),
                        ("medium", "Crawl second"),
                        ("low", "Crawl last or never - likely shell or micro-provider")]:
        count = tiers[tier]
        lines.append(f"| {tier} | {count:,} | {count/len(orgs)*100:.1f}% | {label} |")

    lines.append("\n## By metro, sorted by high-priority count\n")
    lines.append("The **high** column is the number that actually predicts listings. ")
    lines.append("Compare it against total - where they diverge sharply, provider count is noise.\n")
    lines.append("| Metro | High | Medium | Low | Total |")
    lines.append("|---|---|---|---|---|")
    for metro_id, counts in sorted(by_metro.items(), key=lambda kv: -kv[1]["high"]):
        total = sum(counts.values())
        lines.append(f"| {metro_id} | **{counts['high']}** | {counts['medium']} | {counts['low']} | {total} |")

    if shared_addresses:
        lines.append("\n## Most-shared addresses\n")
        lines.append("Legitimate hospices occupy their own premises. Heavy clustering is the ")
        lines.append("documented signature of shell entities.\n")
        lines.append("| Organizations at address | Address (normalized) |")
        lines.append("|---|---|")
        for addr, count in sorted(shared_addresses.items(), key=lambda kv: -kv[1])[:25]:
            lines.append(f"| {count} | `{addr[:70]}` |")

    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    if not args.report_only:
        with open(ORGS_PATH, "w") as fh:
            json.dump(orgs, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    # ---- Console summary -------------------------------------------------
    print("\n" + "=" * 64)
    for tier in ("high", "medium", "low"):
        print(f"  {tier:8s} {tiers[tier]:5,d}  ({tiers[tier]/len(orgs)*100:5.1f}%)")
    print("=" * 64)
    print("\nHigh-priority organizations per metro (this is the number that matters):")
    for metro_id, counts in sorted(by_metro.items(), key=lambda kv: -kv[1]["high"])[:15]:
        total = sum(counts.values())
        print(f"  {metro_id:22s} high={counts['high']:4d}  of {total:5d} total")

    print(f"\nWrote {REPORT_PATH}")
    if not args.report_only:
        print(f"Updated {ORGS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
