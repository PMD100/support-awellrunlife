#!/usr/bin/env python3
"""
Static page builder
===================

Renders listings.json into plain HTML for the Shopify page.

WHY STATIC RATHER THAN JAVASCRIPT
---------------------------------
The first version fetched listings client-side and filtered them in the browser. The
markup rendered fine but the cards never appeared: Shopify's page rendering strips the
<script> tag out of page content.

Rather than fight that, this generates finished HTML. Three things get better as a result:

1. It works. No script, nothing to strip.
2. **It is indexable.** Search engines read the listings directly instead of executing
   JavaScript to find them. Given the whole strategy rests on organic search, that is
   not a consolation prize - it is the better design.
3. It degrades honestly. A reader with JavaScript disabled sees everything.

What we lose is live search and filtering. At this size, grouping by state with a jump
menu is adequate. When the subdomain exists, real filtering comes back with it.

The trade-off is that the page must be regenerated when data changes. That is one
command, and can be wired to run after each merge.

USAGE
-----
  python3 scripts/build_page.py                 writes site/page-body.html
  python3 scripts/build_page.py --stats         counts only, writes nothing
"""

import argparse
import html
import json
import os
from collections import defaultdict
from datetime import date, datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LISTINGS_PATH = os.path.join(REPO_ROOT, "data", "listings.json")
OUT_PATH = os.path.join(REPO_ROOT, "site", "page-body.html")

LOSS = {
    "general": "General grief", "spouse_partner": "Loss of a spouse or partner",
    "child": "Loss of a child", "pregnancy_infant": "Pregnancy or infant loss",
    "parent": "Loss of a parent", "sibling": "Loss of a sibling",
    "suicide": "Suicide loss", "overdose_substance": "Overdose loss",
    "homicide": "Homicide loss", "accident_sudden": "Sudden loss",
    "illness_long": "Loss after illness", "military_service": "Military loss",
    "pet": "Pet loss", "caregiver_anticipatory": "Anticipatory grief",
}
COST = {
    "free": ("Free", "pos"), "donation_suggested": ("Free · donation optional", "pos"),
    "sliding_scale": ("Sliding scale", ""), "paid": ("Fee", ""),
    "unknown": ("Cost not listed — ask when you call", "caution"),
}
STATUS = {
    "org_confirmed": ("Confirmed by the organization", "pos"),
    "source_verified": ("Listed from a current public source", ""),
    "needs_review": ("Unconfirmed — call ahead", "caution"),
}
STATES = {
    "AL": "Alabama", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "Washington DC",
    "FL": "Florida", "GA": "Georgia", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MO": "Missouri", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VA": "Virginia",
    "WA": "Washington", "WI": "Wisconsin",
}


def e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def titlecase(s):
    """CMS gives us SHOUTING NAMES. Render them like a person would write them."""
    if not s:
        return ""
    s = str(s)
    if not s.isupper():
        return s
    small = {"of", "the", "and", "at", "for", "in", "on", "a", "an"}
    out = []
    for i, w in enumerate(s.lower().split()):
        if w in small and i:
            out.append(w)
        elif w.replace(".", "") in {"inc", "llc", "pc"}:
            out.append(w.upper().replace(".", "") + ".")
        else:
            out.append(w.capitalize())
    return " ".join(out)


def is_expired(l, today):
    exp = l.get("schedule_expires")
    if not exp:
        return False
    try:
        return date.fromisoformat(exp) < today
    except ValueError:
        return False


def human_date(iso):
    try:
        return date.fromisoformat(iso).strftime("%b %-d, %Y")
    except Exception:
        return iso or ""


def card(l):
    cost_label, cost_cls = COST.get(l.get("cost"), COST["unknown"])
    stat_label, stat_cls = STATUS.get(l.get("verification_status"), STATUS["needs_review"])
    tags = "".join(f'<span class="awrl-tag">{e(LOSS.get(t, t))}</span>'
                   for t in l.get("loss_types", []))

    where = ", ".join(filter(None, [titlecase(l.get("city")), l.get("state")]))
    actions = []
    if l.get("phone"):
        digits = "".join(c for c in l["phone"] if c.isdigit())
        actions.append(f'<a href="tel:{e(digits)}">Call {e(l["phone"])}</a>')
    site = l.get("url") or l.get("source_url")
    if site:
        actions.append(f'<a href="{e(site)}" target="_blank" rel="noopener noreferrer">'
                       f"Visit website</a>")

    parts = [
        '<article class="awrl-card">',
        f'<h3>{e(l.get("name"))}</h3>',
        f'<p class="awrl-org">{e(titlecase(l.get("organization")))}'
        + (f" · {e(where)}" if where else "") + "</p>",
        f'<div class="awrl-tags">{tags}'
        f'<span class="awrl-tag {cost_cls}">{e(cost_label)}</span>'
        f'<span class="awrl-tag {stat_cls}">{e(stat_label)}</span></div>',
        f'<div class="awrl-row"><b>Meets</b><span>{e(l.get("schedule_text"))}</span></div>',
    ]
    if l.get("description"):
        parts.append(f'<div class="awrl-row"><b>About</b>'
                     f'<span>{e(l["description"][:400])}</span></div>')
    if l.get("verification_status") != "org_confirmed":
        parts.append('<p class="awrl-callahead">Groups change often. '
                     "Please call before you go.</p>")
    parts.append('<div class="awrl-actions">' + "".join(actions)
                 + f'<span class="awrl-checked">Last checked '
                   f'{e(human_date(l.get("last_checked")))}</span></div></article>')
    return "".join(parts)


def build(listings):
    today = date.today()
    live = [l for l in listings
            if l.get("name") and l.get("organization") and not is_expired(l, today)]

    by_state = defaultdict(list)
    for l in live:
        by_state[l.get("state") or "Other"].append(l)
    for rows in by_state.values():
        rows.sort(key=lambda x: (
            {"org_confirmed": 0, "source_verified": 1}.get(x.get("verification_status"), 2),
            titlecase(x.get("organization") or "")))

    order = sorted(by_state, key=lambda s: STATES.get(s, s))
    jump = " · ".join(f'<a href="#awrl-{e(s)}">{e(STATES.get(s, s))} '
                      f"({len(by_state[s])})</a>" for s in order)

    sections = []
    for s in order:
        sections.append(f'<h3 class="awrl-state" id="awrl-{e(s)}">{e(STATES.get(s, s))}</h3>'
                        + "".join(card(l) for l in by_state[s]))

    n = len(live)
    return f"""<div class="awrl-dir">
<div class="awrl-crisis"><strong>If you are in crisis, call or text 988</strong> — the Suicide &amp; Crisis Lifeline, available 24 hours a day. This directory is not a crisis service and is not monitored.</div>
<h2>Find a grief support group near you</h2>
<p class="awrl-intro">Free and low-cost bereavement groups, listed as a public service. No advertising, nothing behind an email signup, and placement is never for sale.</p>
<p class="awrl-count"><strong>{n} group{"" if n == 1 else "s"}</strong> across {len(order)} state{"" if len(order) == 1 else "s"} · updated {human_date(today.isoformat())}</p>
<p class="awrl-jump">{jump}</p>
{"".join(sections)}
<div class="awrl-foot">
<p><strong>Please call before you go.</strong> Support groups change meeting times, pause, and sometimes stop entirely. Every listing shows the date we last checked it. If you find something out of date, <a href="mailto:tbbcamp@gmail.com?subject=Directory%20correction">tell us</a> and we will fix it within 72 hours.</p>
<p class="awrl-fund">This directory is free and always will be. It is built and paid for by A Well Run Life, which makes handmade bronze memorial charms. We would rather say that plainly than have you wonder.</p>
</div>
</div>
<style>
.awrl-dir{{max-width:52rem;margin:0 auto;padding:1rem;color:#2b2b2b;line-height:1.55;text-align:left}}
.awrl-dir h2{{font-size:1.55rem;margin:0 0 .4rem;text-transform:none;letter-spacing:0}}
.awrl-intro{{color:#6b6b6b;margin:0 0 1rem}}
.awrl-crisis{{background:#fff6f4;border:1px solid #f0d6cf;border-left:4px solid #b4553f;padding:.85rem 1rem;border-radius:6px;margin-bottom:1.5rem;font-size:.95rem}}
.awrl-count{{font-size:.92rem;color:#6b6b6b;margin:0 0 .6rem}}
.awrl-jump{{font-size:.9rem;margin:0 0 1.5rem;line-height:2}}
.awrl-jump a{{color:#8b5a2b;text-decoration:none;border-bottom:1px solid #e2ded8}}
.awrl-state{{font-size:1.15rem;margin:2rem 0 .8rem;padding-bottom:.35rem;border-bottom:2px solid #e2ded8;text-transform:none;letter-spacing:0}}
.awrl-card{{border:1px solid #e2ded8;border-radius:8px;padding:1.05rem 1.15rem;margin-bottom:.8rem;background:#fff}}
.awrl-card h3{{margin:0 0 .15rem;font-size:1.05rem;line-height:1.3;text-transform:none;letter-spacing:0}}
.awrl-org{{margin:0 0 .65rem;color:#6b6b6b;font-size:.9rem}}
.awrl-tags{{margin-bottom:.65rem}}
.awrl-tag{{display:inline-block;font-size:.74rem;padding:.16rem .5rem;margin:0 .3rem .3rem 0;border-radius:99px;border:1px solid #e2ded8;background:#faf8f5;color:#6b6b6b}}
.awrl-tag.pos{{color:#2f6b4f;border-color:#c3ddcd;background:#f2f9f5}}
.awrl-tag.caution{{color:#8a6d1f;border-color:#e6dcb8;background:#fdfaef}}
.awrl-row{{margin:.28rem 0;font-size:.94rem}}
.awrl-row b{{display:inline-block;min-width:5rem;font-size:.78rem;text-transform:uppercase;letter-spacing:.03em;color:#6b6b6b;vertical-align:top}}
.awrl-row span{{display:inline-block;max-width:38rem;vertical-align:top}}
.awrl-callahead{{font-size:.85rem;color:#8a6d1f;margin:.5rem 0 0}}
.awrl-actions{{margin-top:.8rem;padding-top:.8rem;border-top:1px solid #e2ded8;font-size:.93rem}}
.awrl-actions a{{color:#8b5a2b;font-weight:600;text-decoration:none;margin-right:1rem}}
.awrl-checked{{color:#6b6b6b;font-size:.78rem}}
.awrl-foot{{margin-top:2.5rem;padding-top:1.25rem;border-top:1px solid #e2ded8;font-size:.9rem;color:#6b6b6b}}
.awrl-foot a{{color:#8b5a2b}}
.awrl-fund{{font-size:.85rem}}
</style>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    with open(LISTINGS_PATH) as fh:
        listings = json.load(fh)

    today = date.today()
    live = [l for l in listings if not is_expired(l, today)]
    expired = len(listings) - len(live)

    print(f"listings in file : {len(listings)}")
    print(f"expired (hidden) : {expired}")
    print(f"rendering        : {len(live)}")
    states = sorted({l.get("state") for l in live if l.get("state")})
    print(f"states           : {len(states)}  {', '.join(states)}")
    if args.stats:
        return 0

    body = build(listings)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as fh:
        fh.write(body)
    print(f"\nwrote {OUT_PATH}  ({len(body):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
