---
name: add-company
description: Adds an employer to companies.yml so the scraper picks up its job board, and proves the board belongs to that employer and returns postings. Use when asked to add, track, or onboard a company, or when handed a careers page URL.
---

# Add company

One employer, one entry in `companies.yml`, one proof that the board is theirs and the
scraper reads it.

## Steps

1. **Find the ATS from the employer's own site.** Start at a careers link on the employer's
   own website, never a search result, an aggregator or a guessed slug. A slug that returns
   postings can still belong to someone else: Ashby `menlo` is Menlo Research, a robotics
   lab, while menlosecurity.com links to `menlosecurity`. The careers URL usually names the
   ATS (`boards.greenhouse.io/acme`, `acme.wd5.myworkdayjobs.com/External`). A branded page
   such as `careers.acme.com` embeds one: fetch the HTML and search it for every platform in
   the identifier table in CONTRIBUTING.md, which maps each URL shape to the fields the entry
   needs: `greenhouse.io`, `lever.co`, `ashbyhq.com`, `smartrecruiters.com`, `workable.com`,
   `recruitee.com`, `pinpointhq.com`, `myworkdayjobs.com`, `oraclecloud.com` or
   `eightfold.ai`, or a script from `cdn.phenompeople.com` (Phenom). For iCIMS Jibe, check
   whether `<careers host>/api/jobs?keywords=cyber` returns JSON with `totalCount`. A company
   on none of these platforms cannot be scraped; tell the user to submit roles through the
   issue form instead.
2. **Decide `security_company`.** Set it to `true` only when security products or services
   are the company's main business, because it admits every engineering title from that
   board. A bank with a security team is not one; a pentest consultancy is.
3. **Write the entry** once a search of `companies.yml` for the employer's name finds none; a
   tracked employer whose board stopped working is `triage-board` work. Put it under the
   platform key, alphabetical by name, with exactly the fields the CONTRIBUTING.md table
   gives for that platform. No comments in this file; the reason for anything unusual goes in
   the commit message.
4. **Prove it.** Run the platform alone:

   ```bash
   python .github/scripts/scrape_jobs.py --dry-run --board <ats>
   ```

   Done when the log holds `Checking Acme (<ats>/<id>)... ok (N postings` with N above zero.
   `zero`, `FAILED` or `CRASHED` means the identifier is wrong: fix it with the
   `triage-board` skill before opening a PR. Read the `NEW [...]` lines for the company as a
   sanity check on what the board contributes, and mention one or two in the PR.

   A line saying `apply link off the allowed hosts (careers.acme.com x12)` means the board
   links to a page off the ATS. Add that host under `apply_hosts:` only when it is the
   employer's own domain, then rerun.
5. **Prove the owner.** Run `python .github/scripts/check_slugs.py --board <ats>` and find
   the entry's line. The board name must be the employer's, and on Ashby the website must be
   their domain; on Lever and Pinpoint the postings must name them. A line marked `?` means
   stop until the employer's own site confirms a rename. Workday, Oracle, Eightfold, Phenom
   and Jibe report no owner name, so the page on the employer's own site that links to the
   board is the proof.
6. **Commit** as `feat(companies): add Acme`, and fill the company-additions section of the
   PR template with the careers URL, the page on the employer's site that links to it, the
   `Checking` line from step 4 and the owner line from step 5.

## Example

A made-up employer, so the output lines show the shape of a pass, not a real run. Acme's own
site links to `https://jobs.ashbyhq.com/acme`, which is Ashby with slug `acme`. Acme sells
endpoint security, so:

```yaml
ashby:
- name: Acme
  slug: acme
  security_company: true
```

`--dry-run --board ashby` then shows `Checking Acme (ashby/acme)... ok (N postings, ...)` with
N above zero, and `check_slugs.py --board ashby` shows `ashby/Acme (acme): board name "Acme",
website https://www.acme.com/`.
