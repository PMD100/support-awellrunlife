# support.awellrunlife.com

A national directory of local grief support groups, organized by city and by type of loss.

Built and funded by [A Well Run Life](https://awellrunlifegear.com), a handmade bronze
charm maker in Chandler, Arizona. We sell memorial keepsakes; this directory is free,
carries no advertising, and never sells placement.

**Start here:** [`HANDOFF.md`](HANDOFF.md) — current state and what happens next.

---

## What's in here

```
.github/workflows/     Scheduled jobs that do the round-the-clock work
scripts/
  lib/normalize.py     Shared cleanup: phones, counties, names, ZIPs
  ingest/              One module per data source
data/
  metros.json          The 25 launch metros, defined by county
  organizations.json   Hospices and other orgs (generated — don't hand-edit)
  coverage-report.md   Where our county coverage has gaps (generated)
tests/fixtures/        Synthetic data for testing without hitting live APIs
```

Full specifications live in the **AWRL Support Directory** folder: strategy, data model,
verification policy, site architecture, data sources, and the roadmap.

## How the pipeline works

```
CMS federal hospice dataset
        ↓  cms_hospice.py — filter to our metros by county
   organizations.json
        ↓  discover_websites.py — find and VERIFY each org's real site
   organizations.json (+ website, bereavement_page)
        ↓  extract_groups.py — pull actual groups off those pages
   listings.json
        ↓  validate.py — schema + publish gates, fails the build on violation
   the website
```

Every stage opens a pull request rather than committing to `main`. Nothing reaches the
public site without passing the gates.

## The rules that aren't negotiable

1. **No listing publishes without a live source URL** that belongs to the named organization.
2. **Extract, never infer.** If a page doesn't state the cost, the cost is `unknown` — not a guess.
3. **Every listing shows its confidence tier and the date we last checked it.**
4. **No ad-network tracking pixels on this site. Ever.** No Meta pixel, no remarketing tags.
5. **Crawl politely:** honor `robots.txt`, one request per second per domain, honest user agent.
6. **Any organization asking to be removed is removed within 72 hours**, no questions.

## Running things locally

The ingest scripts use only the Python standard library — no install step.

```bash
python3 scripts/ingest/cms_hospice.py --inspect                        # show dataset columns
python3 scripts/ingest/cms_hospice.py --fixture tests/fixtures/hospice_sample.csv
python3 scripts/ingest/cms_hospice.py                                  # live run
```

In normal use you won't run these by hand — GitHub Actions does, on a schedule.
