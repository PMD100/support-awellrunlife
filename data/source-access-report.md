# Source Access Report

_Generated 2026-08-07 16:24 UTC_

What each national directory permits. **Adapters may only be written for sources marked `automate`.** Everything else goes through the manual CSV path in `scripts/ingest/import_csv.py`, which does not breach anyone's terms.

| Source | Directory allowed | Crawl delay | Recommendation |
|---|---|---|---|
| The Compassionate Friends | yes | — | **automate** |
| American Foundation for Suicide Prevention | yes | — | **human_review** |
| National Alliance for Children's Grief | yes | — | **automate** |
| Bereaved Parents of the USA | yes | — | **automate** |
| GRASP (Grief Recovery After Substance Passing) | yes | — | **automate** |
| Share Pregnancy & Infant Loss Support | yes | — | **automate** |
| MISS Foundation | yes | — | **automate** |
| Soaring Spirits International | yes | 5.0 | **automate** |
| TAPS (Tragedy Assistance Program for Survivors) | assumed_yes | — | **human_review** |
| GriefShare | yes | — | **contact_first** |

## Notes requiring a human


### American Foundation for Suicide Prevention

Terms: https://afsp.org/privacy-notice/

- Terms page mentions automated access — read before automating.

### Soaring Spirits International
- Requests a 5.0s crawl-delay, slower than our 1.0s default. Any adapter for this source must honor 5.0s.

### TAPS (Tragedy Assistance Program for Survivors)

Terms: https://www.taps.org/terms

- No readable robots.txt. Standard interpretation is that crawling is permitted, but the terms of service still govern - read them.

## What `contact_first` means

Send a short email describing the directory, that it is free and carries no advertising, and asking whether they would like their groups included and whether they can share data directly. Many will say yes. A conversation produces better data than a scraper and starts a relationship we want anyway.

