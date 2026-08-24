# Runbook

Keep this open. There is nothing to memorise.

---

## The one rule

**A workflow reads whatever is on `main` at the moment it starts.**

Everything else follows from that. Merge first, then run. If you get it backwards, just
run it again — no harm done, because **workflows never write to `main` directly.** They
open a pull request. The worst possible outcome is a branch you close without merging.

---

## The loop, every time

```
1. Merge any open pull request you want included
2. GitHub Desktop:  Fetch origin  ->  Pull origin
3. Actions -> [workflow] -> Run workflow
4. Wait for the green check
5. Merge the pull request it opened
6. Fetch origin -> Pull origin
```

Steps 2 and 6 are the ones easiest to skip and the ones that cause the most confusion.

---

## Which button

| Situation | Button |
|---|---|
| Code changed, or a run is stuck | **Run workflow** (on the workflow's own page) |
| Code identical, run failed on GitHub's infrastructure | **Re-run jobs** (on the run's page) |

If you're unsure, **Run workflow** is almost always right.

**Finding it:** Actions tab → click the workflow name in the **left sidebar** → the button
sits at the **right-hand end of the blue banner**. If you're looking at a page titled with
a run number, you're one level too deep — click the workflow name at the top left.

---

## Merging a pull request

A workflow never changes anything by itself. It opens a pull request and waits. Merging
is how you say yes.

**Go straight to:** <https://github.com/PMD100/support-awellrunlife/pulls>

1. Click the pull request's title.
2. **Files changed** tab — glance at the report file (`data/*-report.md`). That is the
   run explaining itself.
3. Back to the **Conversation** tab. Scroll to the bottom.
4. Green **Merge pull request** → then **Confirm merge**.
5. GitHub Desktop → **Fetch origin** → **Pull origin**.

Step 5 is the one people skip. Until you pull, your computer still has the old data and
so does anything you run locally.

**Nothing there?** Either the run is still going, or it finished and found no changes to
propose — check the Actions tab. A workflow with nothing to say opens no pull request.

---

## Discover Websites: which checkbox

This is the one that has genuinely caught us out.

| Goal | Checkbox |
|---|---|
| Fill in organizations we haven't checked yet | neither |
| Retry ones that failed to find a site | **Retry previous failures** |
| **Re-validate under a changed rule** | **Re-check everything** |

The trap: if a listing is currently *wrong but verified*, "Retry previous failures" skips
it, because as far as the code knows it isn't a failure. Changing a verification rule
always means **Re-check everything**.

---

## Order that matters

Only one dependency exists in the whole pipeline:

```
Ingest CMS Hospices  ->  Discover Websites  ->  Extract Groups  ->  page rebuild
```

Each reads what the one before it merged. Running them out of order isn't destructive —
the later one just works from stale input, and you re-run it afterwards.

**Ingest BPUSA Chapters sits outside that chain.** It reads one page from Bereaved
Parents of the USA and rebuilds the directory page itself, so it can be run at any time,
on its own, in about ten seconds. No API key, no cost.

Before merging its pull request, open `data/bpusa-report.md` and check the
**"Personal data discarded"** table. It should show a non-zero count of email addresses
and of addresses presumed personal. Zero doesn't mean the page got cleaner — it means
the filter stopped recognising them, and that's the one failure worth catching by eye.

---

## When something looks wrong

1. **Red X on a run** — open it, expand the failed step, send me the last dozen lines.
2. **Ran but no pull request** — the run may still be finishing; give it a minute.
3. **Numbers look wrong** — say so before merging. Every mistake we've caught was caught
   by someone looking at the output and thinking "that doesn't seem right." Keep doing that.
4. **Anything at all** — nothing here can be broken permanently. Data lives in pull
   requests until you approve it, and every previous version stays in the history.

---

## What you never have to do

- Remember any command
- Edit code
- Fix a merge conflict (close the branch and re-run instead)
- Worry that a wrong click destroyed something
