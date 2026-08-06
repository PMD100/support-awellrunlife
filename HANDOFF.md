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

## The target

**400–500 high-quality institutions published across the 26 metros.** Set 2026-08-05.

This is a *quality bar with an expected count*, not a quota. We publish every organization
that clears the bar and none that don't. If the bar yields 380, we publish 380.

### The bar

An institution qualifies only if all five are true:

1. Runs at least one identifiable bereavement or grief support group
2. That group is **open to the community**, not restricted to its own patient families
3. There is a **live source page** on the organization's own domain that names the group
4. There is a **working phone number** a person can call to confirm before travelling
5. The organization's name is **verifiable on its own website** — this is the "links go to
   the actual business" guarantee, enforced in code, not by eye

### Measured against real data (2026-08-05, 3,435 orgs, all scored)

| Source | Candidates | Basis |
|---|---|---|
| High-tier hospices | **236** | measured |
| Medium-tier hospices scoring 55–64 | **42** | measured |
| National org directories | ~120 | estimated |
| Hospital systems, 3–5 per metro | ~100 | estimated |
| **Total candidates** | **~498** | |
| **After the 5-point bar (~22% attrition)** | **~388** | |

**We land just under the target.** The medium tier turned out to be a much smaller
reservoir than assumed — only 42 of 723 score above 55, so there is no large pool of
"almost good enough" hospices to draw on.

**The national directories are now the decisive lever.** They are the only source that
can move ~388 to ~450, and they should be worked before hospital systems.

**Seven metros cannot clear the 8-listing gate on hospices alone:**
Orlando (1), San Diego (3), Houston (4), San Antonio (4), Tampa (4), Denver (5),
**Phoenix (5)**. The home market is on that list.

**Important counterweight for CON states:** Orlando has 5 hospices total and Tampa 9,
because Florida restricts entry. The survivors are enormous — a single Florida hospice may
run six or more distinct bereavement groups. **Institution count understates listing yield
in CON states and overstates it in fraud-affected ones.** Do not judge Orlando by its 1.

### Where 400–500 comes from (original estimate)

| Source | Expected qualifying institutions |
|---|---|
| High-tier hospices (incl. Bay Area) | ~235 |
| Best of medium-tier hospices | ~100 |
| National org directories (Compassionate Friends, AFSP, NACG, GRASP, TAPS, Bereaved Parents) | ~120 |
| Large hospital systems, 3–5 per metro | ~100 |
| **Before the bar is applied** | **~555** |
| **After the bar** (expect 20–25% attrition) | **~420–460** |

At roughly 1.5–2.5 groups per institution, that produces **700–1,100 published listings** —
about 27–42 per metro, comfortably clearing the 8-listing metro gate everywhere.

**Why capping at quality rather than maximizing volume is the right call:** completeness is
copyable and freshness is not. Every competitor in this space is bigger than they can
maintain, which is exactly why they're all stale. 450 institutions we can actually keep
current beats 3,000 we can't.

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

## THE BIG FINDING — read before doing anything else

The first ingest returned **3,308 organizations**, roughly three times the estimate.
The distribution is what matters:

| Metro | Hospices |
|---|---|
| Los Angeles | **1,402** |
| Houston | 307 |
| Dallas | 253 |
| ... | |
| Tampa | **9** |
| Orlando | **5** |

Tampa and Orlando have among the highest older-adult populations in the country. That
inversion is not a bug in our county lists. Two real forces produce it:

1. **Fraud-driven proliferation.** LA County saw roughly a 1,500% increase in hospice
   agencies over a decade. California imposed a moratorium on new hospice licenses in
   2021 (SB 664) after finding 93% of applications came from LA/Southern California and
   72% shared an address with other applicants — one LA address was tied to 191 separate
   applications. These entities have no clinical staff and no community programs.

2. **Certificate of Need laws.** Florida restricts hospice entry through CON review. It
   ranks 2nd nationally in hospice patients served but 37th in provider count. Its
   hospices are few, large, old, and exactly the kind that run free community grief groups.

**Consequence: provider count is a poor proxy for group supply, and in fraud-affected
markets it is actively misleading. Tampa's 9 will likely out-produce LA's 1,402.**

### What changed as a result

- `scripts/score_organizations.py` — scores every org on ownership type, certification
  age, and **shared-address density** (the shell detector — legitimate hospices occupy
  their own premises). Crawl `high` tier first, likely never crawl `low`.
- LA demoted from priority 2 to 3 in `metros.json`.
- **San Francisco Bay Area added** — the coverage report exposed Alameda (50),
  Contra Costa (26), Santa Clara (16), San Mateo (11) matching nothing. The Bay Area was
  simply missing from the original 25. We now have 26 metros.
- Ingest workflow now runs scoring automatically and reports high-tier counts.

### Scoring results against the real 3,308 records

| | Count | Share |
|---|---|---|
| **high** — crawl these | **220** | 6.7% |
| medium — crawl second | 696 | 21.0% |
| low — shells and micro-providers | 2,392 | 72.3% |

**Signal ratio by metro** (high ÷ total) — this is the number that matters:

| Best signal | | Worst signal | |
|---|---|---|---|
| New York | 33 of 59 (56%) | Los Angeles | 8 of 1,402 (**0.6%**) |
| Baltimore | 6 of 11 (55%) | Houston | 4 of 307 (1.3%) |
| Charlotte | 7 of 15 (47%) | Dallas | 7 of 253 (2.8%) |
| Tampa | 4 of 9 (44%) | Riverside | 7 of 231 (3.0%) |
| Seattle | 8 of 21 (38%) | Phoenix | 5 of 161 (3.1%) |

**Spot check confirms the scorer works.** High tier surfaced Hospice of the Valley,
Suncoast Hospice, Gulfside, Kaiser, MemorialCare, Providence, Barnabas Health — real,
established institutions. Low tier in LA surfaced "1 Heart Hospice," "24 Care Hospice,"
"5 Star Hospice," "7 Angeles Hospice" — all certified 2019–2023, all at suite numbers.
The most-shared address hosts **24 separate hospices**; the top eight clusters are all
San Fernando Valley strip malls. That is the documented fraud signature, visible in our
own data with no external lookup.

**Note: Phoenix is a fraud-affected market too** (5 high of 161). The home-field metro
has thin hospice supply and will depend on other sources.

### What this means for the roadmap

**220 high-tier hospices across 25 metros is about 9 per metro.** At a plausible 1–3
groups each, that is 9–27 groups per metro from hospices — and the metro publish gate
needs 8 published listings. So hospices alone barely clear the gate in strong metros and
will not clear it in weak ones.

**This demotes hospices from primary supply to one contributor among several.** The
national organization directories (GriefShare, Compassionate Friends, AFSP, the Dougy
Center network, Bereaved Parents, GRASP) and large hospital systems now have to carry
the load. Doc 05's yield model needs revising down: the realistic hospice contribution is
roughly 220 orgs × ~1.5 groups ≈ **330 listings**, not the 900–1,300 originally modeled,
and only where those orgs have a findable bereavement page.

**Crawl budget note:** at one request per second, crawling everything is 55 minutes and
high+medium is 15 minutes. Crawl time is not the constraint — LLM extraction cost is.
So crawl **high + medium (916 orgs)** and skip the 2,392 low. Skipping them is also the
polite choice; they are mostly shells and there is nothing to gain from touching them.

---

## Next up

### Piece 0 — Commit the scored data *(needs Peggie)*

The scorer has been run locally against the real 3,308 records. `organizations.json` and
`priority-report.md` are updated on the Mac but not committed.

1. Commit and push: `Add crawl priority scores`
2. **Bay Area is still missing** (0 organizations) — the merged data predates the metro
   being added. Re-run **Ingest CMS Hospices** to pick it up, then merge that PR too.
   Expect roughly 100–150 additional Bay Area organizations.

### Piece 1 — Run the ingest for real *(DONE 2026-08-05)*

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

### Piece 2 — Website discovery *(BUILT 2026-08-05, not yet run)*

`scripts/lib/politefetch.py` and `scripts/ingest/discover_websites.py`, plus the
**Discover Websites** workflow.

**How "links go to the actual business" is enforced.** A website attaches to an
organization only on proof, never on similarity:

| Status | Meaning |
|---|---|
| `verified_phone` | The CMS-registered phone number was found in the page text. Near-conclusive — unrelated businesses do not share a phone number. |
| `verified_name` | Every distinctive word of the org's name appeared on a page recognizably about hospice care. "Distinctive" excludes filler like HOSPICE, CARE, HEALTH, SERVICES. |
| `mismatch` | Site found, ownership not proved. **Never published.** |
| `not_found` | No candidate domain resolved. |
| `robots_disallowed` | Site asked not to be crawled. We comply and stop. |

Offline tests assert the rejection cases specifically: a page for *Suncoast Hospice* is
correctly refused for *Gulfside Hospice* even though both are real Florida hospices with
near-identical vocabulary. Right industry, wrong organization, rejected.

**Domain discovery without a search API.** CMS has no website column. The script generates
candidate domains from the organization's name (full name, connectors dropped, distinctive
words only, initials) across `.org/.com/.net/.health`, DNS-checks each cheaply, then
verifies. Recall is decent for established nonprofits — which is exactly the high tier.
Adding a `BRAVE_API_KEY` secret later would improve recall; nothing depends on it.

**Politeness is in code, not convention:** robots.txt honored without exception, 1 second
between requests to the same host, honest user agent with a contact URL, no aggressive
retries. These organizations are our future outreach targets.

**Run it:** Actions → Discover Websites → Run workflow. Start with tier `high`,
limit `150`. Expect 30–55 minutes for the full 236.

### Piece 2b — Website discovery, original notes

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

### 2026-08-05 (later) — First real ingest, and a strategy correction

- **Ingest workflow ran green in Actions.** 6,852 rows in the CMS dataset (May 2026
  release), 3,308 matched to our metros. Column resolution worked — no manual fixes needed.
- Coverage report revealed the LA/Tampa inversion described above. Verified against
  reporting on the California hospice fraud crisis and Florida's CON restrictions;
  both hypotheses confirmed.
- Wrote `scripts/score_organizations.py`. Tested against a synthetic set of 35 orgs
  designed to mimic the real pattern: 25 shells sharing one address (new, for-profit),
  3 established nonprofits, 1 hospital system, 6 old independent for-profits.
  **All assertions passed** — every shell scored `low` (0/100), every established
  nonprofit scored `high` (100/100), old independents landed `medium`.
  LA in that test: 1 high-priority org out of 26.
- Added San Francisco Bay Area metro (9 counties). Demoted LA to priority 3.
- Bumped action versions to clear the Node 20 deprecation warning
  (checkout@v5, setup-python@v6, create-pull-request@v7).

**Not yet verified:** the scorer has never run against the real 3,308 records — only
synthetic data. The bumped action versions have never run. If the next workflow run
fails with "unable to resolve action," revert those three version numbers by one.

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
