---
name: triage-board
description: Diagnoses a configured job board that errors or returns zero postings and repairs or drops its companies.yml entry. Use when the scrape summary flags a failed, regressed, or silent board, when check_slugs.py reports one, or when a new entry shows zero postings.
---

# Triage board

A board that errors or goes silent looks identical to one with no new cyber jobs, which is why
the scraper tracks a per-board zero streak and `check_slugs.py` exists as the on-demand sweep.
Each ATS status code means one specific thing; read it before touching the entry.

## Steps

1. **Reproduce.** `python .github/scripts/scrape_jobs.py --dry-run --board <ats>` prints the
   `[label] ... error` line and the `Checking <label>... <status>` line for the board. For a
   whole-config sweep run `python .github/scripts/check_slugs.py`.
2. **Read the status code** against the table below. It tells you whether the identifier is
   wrong, the tenant moved, or the board is gone.
3. **Find the current identifier** on the company's own careers site. The apply links there
   carry the live tenant, instance and board (Workday) or point at the platform the company
   moved to. The careers page HTML can hide it, so search the source for the platform hosts
   listed in the `add-company` skill.
4. **Repair or drop.** Repair when a live identifier exists: edit the entry and rerun step 1
   until the `Checking` line reads `ok` with postings above zero. Drop the entry when the
   company has no public board any more.
5. **Record it.** Commit as `fix(companies): ...` with one line per board saying what changed
   and why. `companies.yml` carries no comments, so the commit message is the only record.

Done when every board you touched shows `ok (N postings` with N above zero, or is gone from
`companies.yml` with the reason in the commit.

## Status codes

| ATS | Code | Meaning | Fix |
| --- | ---- | ------- | --- |
| Workday cxs `/jobs` | 422 | Unknown tenant on that instance. A made-up tenant also 422s. | Read tenant and instance from an apply link on the careers site. |
| Workday cxs `/jobs` | 404 | Real tenant, wrong board name. | Read the board segment from an apply link. |
| Workday cxs `/jobs` | 410 with `ERR_TENANT_MIGRATED` | Tenant moved to another instance. The old board root still renders 200 in a browser, so that check lies. | Take the new instance from an apply link. Comcast moved wd5 to wd115, Walmart wd5 to wd504. |
| Greenhouse boards-api | 404 | The board token is gone, not renamed. | Search the careers page HTML for `jobs.ashbyhq.com/<slug>` or a `myworkdayjobs.com` link and move the entry to that platform. An Ashby slug such as `marqeta-inc` is never the old Greenhouse token. Fortra moved to Workday as `fortra` / `wd12` / `FortraCareers`. |
| Any | 200 with zero postings | The board is live and empty, or the parser lost its container key after schema drift. | Compare the raw JSON with `scrape_<ats>` in `scrape_jobs.py`; extend the schema-drift case in `test_scrapers.py` if the shape moved. |

## Example

The summary says `[Fortra (greenhouse/fortra)] returned 0 postings but had 41 last run`. The
Greenhouse API returns 404, so the token is gone. Fortra's careers page links to
`fortra.wd12.myworkdayjobs.com/FortraCareers`, so the entry moves from `greenhouse:` to
`workday:` as `tenant: fortra`, `instance: wd12`, `board: FortraCareers`, and the dry run
shows `Checking Fortra (workday/fortra)... ok (41 postings`.
