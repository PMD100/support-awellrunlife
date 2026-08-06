# HANDOFF

**This file is the memory between work sessions.** Each session starts cold with no
recollection of the last one. Whatever isn't written here didn't happen.

Read this file first. Update it last. Never leave a session without updating it.

---

## The protocol

Every session follows the same four beats:

1. **Read** this file top to bottom, then the "Next up" section.
2. **Do** the work in "Next up" — one piece, finished, not three pieces half-done.
3. **Verify** it. Run it, test it, prove it works. An untested piece is not a finished piece.
4. **Rewrite** the "Current state," "Next up," and "Log" sections below.

### The stop rule

**If verification fails and the cause isn't obvious within a couple of attempts, stop.**
Write what broke into "Blocked on" and end the session. Do not improvise around a
failure, do not skip the broken piece and move to the next one, do not mark something
done that isn't.

An unattended chain that pushes through failures produces damage that takes longer to
find than to prevent. A chain that stops cleanly costs one session.

### What "verified" means here

| Kind of work | Verified means |
|---|---|
| A script | Runs against a fixture, output asserted correct |
| A workflow | Actually ran in Actions and went green |
| Data | Row counts and spot checks recorded in the Log below |
| Site code | Builds without error, page renders, no console errors |
| A document | Cross-checked against the specs it references |

---

## Current state

**Phase:** 0 — Foundation
**Last updated:** 2026-08-05

### What exists and works

| Piece | Status | Verified how |
|---|---|---|
| GitHub repo, GitHub Desktop workflow | Working | Hello World workflow ran green |
| `.github/workflows/hello-world.yml` | Working | Green check on GitHub |
| `data/metros.json` — 25 metros with county lists | Written | Loaded and indexed without collisions |
| `scripts/lib/normalize.py` | Written | Exercised by the ingest test below |
| `scripts/ingest/cms_hospice.py` | Written | **Tested against fixture — 9 assertions pass** |
| `.github/workflows/ingest-hospices.yml` | Written | YAML parses; **not yet run in Actions** |
| Planning docs (in the AWRL Support Directory folder) | Complete | Cross-checked for consistency |

### What does not exist yet

- Website discovery — organizations have no `website` value yet
- Any actual support group listings (we have organizations, not groups)
- The Astro site, any page templates
- Cloudflare account and deploy pipeline
- The claim and report flows

### Blocked on

**Peggie needs to do two things before the ingest workflow can open a pull request:**

1. **Enable PR creation by Actions.** On github.com: repo → **Settings** → **Actions** →
   **General** → scroll to **Workflow permissions** → check
   **"Allow GitHub Actions to create and approve pull requests"** → Save.
   Without this, the workflow runs fine but fails at the last step with a permissions error.

2. **Commit and push** the current files via GitHub Desktop.

---

## Next up

### Piece 1 — Run the ingest for real *(needs Peggie)*

1. Do the two blocked items above.
2. Actions tab → **Ingest CMS Hospices** → **Run workflow**.
   Leave "inspect only" unchecked.
3. Expect a pull request titled "Data refresh: CMS hospice organizations."
4. **Do not merge blindly.** Open `data/coverage-report.md` in the PR and check the
   "Metros with ZERO matches" section. Report what it says.

**Expected result:** roughly 900–1,300 organizations across the 25 metros. Materially
fewer means the county lists have gaps; materially more means the county lists are
too wide.

**If the run fails:** copy the last ~15 lines of the failed step's log. That is
almost always enough to diagnose.

### Piece 2 — Website discovery *(next session)*

Organizations currently have `website: null`. Build `scripts/ingest/discover_websites.py` to:

- Find each organization's official website
- Verify the site actually belongs to that organization (org name must appear in the
  page text — this is the "links go to the actual business" guarantee)
- Locate a bereavement or grief-support page on that site
- Record `website_status`: `verified`, `mismatch`, `unreachable`, or `not_found`

**Constraints that are not negotiable:** honor `robots.txt`, one request per second per
domain, identify honestly as `AWRLSupportBot/1.0`.

### Piece 3 — Group extraction

Turn bereavement pages into listing records per `data/schema.json`. This is the hardest
step. Rules from the verification policy that must be enforced in code:

- **Extract only, never infer.** Cost not stated on the page means `cost: unknown`,
  never a guess.
- Every extracted field keeps its source quote in `internal_notes`.
- Low extraction confidence means `needs_review`, never silent publication.

### Piece 4 — The 50-record audit *(needs Peggie, ~90 minutes, once)*

Generate a single review page showing each extracted field beside the source quote it
came from. Peggie clicks yes/no. This is the one manual gate in the whole pipeline and
it exists to catch systematic extraction errors before they multiply across thousands
of records.

---

## Log

Newest first. One entry per session. Keep them short and factual.

### 2026-08-05 — Ingest pipeline built

- Added county-level definitions to all 25 metros in `data/metros.json`. County matching
  beats city-name matching because the CMS dataset carries a county field and a metro
  has few counties but hundreds of city names.
- Wrote `scripts/lib/normalize.py` — phone, county, org name, ZIP, slug normalization.
- Wrote `scripts/ingest/cms_hospice.py`. Resolves CMS column names by pattern rather than
  hard-coding them, because CMS renames columns between releases ("County Name" became
  "County/Parish"). Fails loudly with the real header list if resolution fails.
- Built a 13-row synthetic fixture covering the known-hard cases and asserted all of them:
  - Exact duplicate rows collapse (dedupe by normalized phone)
  - "Saint Louis" and "St. Louis County" both normalize to `ST. LOUIS`
  - `ST. LOUIS CITY` stays distinct from `ST. LOUIS` — separate jurisdictions
  - Lake County IN and Cook County IL both map to `chicago-il`, state-scoped
  - Falls Church City VA maps to `washington-dc` — Virginia independent cities
  - ZIP+4 trims to 5 digits; leading `1` country code strips from phones
  - Out-of-state and out-of-metro rows are excluded; nameless rows dropped
  - **Result: 13 rows in → 9 organizations out, all 9 assertions passed**
- Wrote `.github/workflows/ingest-hospices.yml`. Opens a pull request rather than
  committing to main. Monthly cron plus manual trigger. Zero pip dependencies.
- Reset `data/organizations.json` to `[]` so the first real run generates it — the
  fixture output was synthetic and must not be mistaken for real data.

**Not yet verified:** the workflow has never run in Actions. The CMS metastore API
response shape is assumed, not confirmed. If `find_csv_url()` fails on the first run,
that is the most likely reason — run with "inspect only" checked to see the real columns.
