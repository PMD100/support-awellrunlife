#!/usr/bin/env python3
"""
Source access check
===================

Establishes what each national directory actually permits BEFORE we write a single
scraper against it.

WHY THIS RUNS FIRST
-------------------
The temptation with a list of ten directories is to write ten scrapers and find out
later. That would be wrong here for three reasons:

  1. Several of these organizations explicitly restrict automated access. Ignoring that
     is a breach of their terms regardless of how public the data looks.
  2. These are the same organizations we will later ask to link to us, claim listings,
     and hand our charms to grieving families. Getting blocked or complained about would
     cost far more than the data is worth.
  3. Some of them would probably share their data directly if asked. A scraper forecloses
     a conversation that would have produced better data with less work.

So: check first, write adapters only where permitted, and use the manual CSV path
everywhere else.

WHAT IT CHECKS
--------------
  * robots.txt — is our bot allowed on the directory path specifically
  * whether a crawl-delay is specified, and whether it exceeds our default
  * where the terms-of-service page lives, so a human can read it

It reads. It never writes to the sources and never fetches directory content.

USAGE
-----
  python3 scripts/ingest/check_source_access.py
  python3 scripts/ingest/check_source_access.py --source compassionate-friends
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import politefetch  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SOURCES_PATH = os.path.join(REPO_ROOT, "data", "national-sources.json")
REPORT_PATH = os.path.join(REPO_ROOT, "data", "source-access-report.md")

TERMS_PATHS = ["/terms", "/terms-of-use", "/terms-of-service", "/terms-and-conditions",
               "/legal", "/privacy-policy", "/privacy"]


def check_source(source):
    """Return a dict describing what this source permits."""
    domain = source["domain"]
    base = f"https://{domain}"
    directory_url = urllib.parse.urljoin(base, source.get("directory_path", "/"))

    result = {
        "id": source["id"],
        "name": source["name"],
        "domain": domain,
        "directory_url": directory_url,
        "robots_txt": None,
        "directory_allowed": None,
        "crawl_delay": None,
        "terms_url": None,
        "notes": [],
    }

    robots_url = f"{base}/robots.txt"
    robots = politefetch.fetch(robots_url, check_robots=False)

    if not robots.ok:
        result["robots_txt"] = f"unavailable ({robots.error or robots.status})"
        result["directory_allowed"] = "assumed_yes"
        result["notes"].append(
            "No readable robots.txt. Standard interpretation is that crawling is "
            "permitted, but the terms of service still govern - read them."
        )
    else:
        result["robots_txt"] = "present"
        result["directory_allowed"] = (
            "yes" if politefetch.allowed_by_robots(directory_url) else "NO"
        )

        delay = re.search(r"(?im)^\s*crawl-delay:\s*([\d.]+)", robots.text)
        if delay:
            seconds = float(delay.group(1))
            result["crawl_delay"] = seconds
            if seconds > politefetch.MIN_DELAY:
                result["notes"].append(
                    f"Requests a {seconds}s crawl-delay, slower than our {politefetch.MIN_DELAY}s "
                    f"default. Any adapter for this source must honor {seconds}s."
                )

        if re.search(r"(?im)^\s*user-agent:\s*(GPTBot|CCBot|anthropic|ClaudeBot|Google-Extended)",
                     robots.text):
            result["notes"].append(
                "Explicitly names AI/bot user agents in robots.txt — this operator has "
                "thought about automated access. Read the terms carefully before automating."
            )

    for path in TERMS_PATHS:
        candidate = urllib.parse.urljoin(base, path)
        page = politefetch.fetch(candidate)
        if page.ok and len(page.text) > 2000:
            result["terms_url"] = page.final_url
            text = page.text.lower()
            for phrase, note in [
                ("scrap", "Terms page mentions scraping — read that clause before automating."),
                ("automated", "Terms page mentions automated access — read before automating."),
                ("crawler", "Terms page mentions crawlers — read before automating."),
                ("data mining", "Terms page mentions data mining — read before automating."),
            ]:
                if phrase in text and note not in result["notes"]:
                    result["notes"].append(note)
            break

    if result["directory_allowed"] == "NO":
        result["recommendation"] = "manual_only"
    elif source.get("access_status") == "contact_first":
        result["recommendation"] = "contact_first"
    elif any("read" in n.lower() for n in result["notes"]):
        result["recommendation"] = "human_review"
    else:
        result["recommendation"] = "automate"

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", help="Check a single source by id")
    args = parser.parse_args()

    with open(SOURCES_PATH) as fh:
        registry = json.load(fh)

    sources = registry["sources"]
    if args.source:
        sources = [s for s in sources if s["id"] == args.source]
        if not sources:
            print(f"No source with id {args.source!r}")
            return 1

    print(f"Checking {len(sources)} sources. Reading robots.txt and terms pages only — "
          f"no directory content is fetched.\n")

    results = []
    for source in sources:
        print(f"  {source['name']} ({source['domain']})...")
        result = check_source(source)
        results.append(result)
        print(f"    robots: {result['robots_txt']} | directory allowed: "
              f"{result['directory_allowed']} | -> {result['recommendation']}")
        for note in result["notes"]:
            print(f"    ! {note}")

    # Write recommendations back into the registry
    by_id = {r["id"]: r for r in results}
    for source in registry["sources"]:
        if source["id"] in by_id:
            r = by_id[source["id"]]
            source["access_status"] = r["recommendation"]
            source["access_checked"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if r["terms_url"]:
                source["terms_url"] = r["terms_url"]
            if r["crawl_delay"]:
                source["required_crawl_delay"] = r["crawl_delay"]

    with open(SOURCES_PATH, "w") as fh:
        json.dump(registry, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    # ---- Report ----------------------------------------------------------
    lines = ["# Source Access Report\n",
             f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n",
             "What each national directory permits. **Adapters may only be written for "
             "sources marked `automate`.** Everything else goes through the manual CSV path "
             "in `scripts/ingest/import_csv.py`, which does not breach anyone's terms.\n",
             "| Source | Directory allowed | Crawl delay | Recommendation |",
             "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['name']} | {r['directory_allowed']} | "
                     f"{r['crawl_delay'] or '—'} | **{r['recommendation']}** |")

    lines.append("\n## Notes requiring a human\n")
    any_notes = False
    for r in results:
        if r["notes"]:
            any_notes = True
            lines.append(f"\n### {r['name']}")
            if r["terms_url"]:
                lines.append(f"\nTerms: {r['terms_url']}\n")
            for note in r["notes"]:
                lines.append(f"- {note}")
    if not any_notes:
        lines.append("\nNone.")

    lines += ["\n## What `contact_first` means\n",
              "Send a short email describing the directory, that it is free and carries no "
              "advertising, and asking whether they would like their groups included and "
              "whether they can share data directly. Many will say yes. A conversation "
              "produces better data than a scraper and starts a relationship we want anyway.\n"]

    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"\nWrote {REPORT_PATH}")
    print(f"Updated {SOURCES_PATH}")

    counts = {}
    for r in results:
        counts[r["recommendation"]] = counts.get(r["recommendation"], 0) + 1
    print("\nRecommendations:")
    for rec, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {rec:16s} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
