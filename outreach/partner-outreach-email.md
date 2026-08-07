# Partner outreach — national grief organizations

For sources marked `contact_first` in `data/national-sources.json`.

## Why this email matters more than the data

Asking gets us four things a scraper never would:

1. **Better data.** They may send a spreadsheet, or a feed, and it will be more current and more complete than anything we could extract.
2. **A backlink from a high-authority nonprofit.** Worth more for search than dozens of small ones.
3. **Permission we can point to** when a hospice or funeral director asks who we are.
4. **The B2B door.** Every one of these organizations touches grieving families constantly. That is the wholesale channel for A Well Run Life, and it does not open with a scrape.

The email below is a courtesy notice that happens to do all four. Write it as a person, not as a company.

## Ground rules

- **Do not pitch the charms.** Not one word. This email is about the directory. The commercial relationship, if it ever comes, is a later conversation earned by being useful first.
- **Disclose the funding anyway.** Say plainly that A Well Run Life pays for it. If they find out later on their own, we look like we were hiding it. Said upfront, it reads as straightforward.
- **Offer to be excluded.** Genuinely. A directory that removes on request is one people trust.
- **Send from a real person's address**, not `info@`.

---

## Template

**Subject:** A free directory of grief support groups — may we include your chapters?

Hello,

My name is Peggie, and I'm building a free directory of local grief support groups at support.awellrunlife.com. It's meant for the person searching at 2am for a group near them — a problem I found is surprisingly hard to solve, because the information is scattered across hundreds of organizations and nobody has pulled it together.

I'm writing before including anything of yours, rather than after.

**What it is.** Every listing shows who the group serves, whether it's free, whether it's faith-based, when it meets, and the date we last checked that it's still running. Nothing is behind an email signup. There is no advertising, and placement is never for sale.

**Who pays for it.** I run A Well Run Life, a small business in Chandler, Arizona that makes handmade bronze memorial charms. The directory is free and always will be; my business simply funds it. I'd rather tell you that plainly than have you wonder.

**What I'm asking.** Two questions:

1. Would you like your [chapters / groups] included? If so, is there a file or feed you could share? That would be more accurate than anything I could assemble myself.
2. If you'd rather not be listed, just say so and I won't include you. No follow-up, no persuasion.

I'd also welcome being told I've got something wrong. Anything you flag gets fixed within 72 hours, and any organization that asks to be removed is removed in the same window.

Thank you for the work you do.

Peggie
A Well Run Life · Chandler, Arizona
[phone] · [email]

---

## Send order

| Organization | Why this order |
|---|---|
| **MISS Foundation** | Arizona-founded. Local, warmest reception, best place to learn what these conversations feel like. |
| **The Compassionate Friends** | 110 chapters, our highest-value source, and their locator page carries a usage restriction — this one genuinely needs asking. |
| **GRASP** | Robots.txt inconclusive, overdose loss badly underserved. |
| **GriefShare** | Largest by far at ~900. Save for last, once we have a live site to point at. |

## After they reply

- **Yes with data** → import via `scripts/ingest/import_csv.py`, set `verification_status: org_confirmed`
- **Yes, no data** → build the adapter, and say thank you
- **No** → mark `access_status: "declined"` in the registry and never revisit
- **No reply after three weeks** → one short follow-up, then leave it
