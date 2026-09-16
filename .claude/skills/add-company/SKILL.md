---
name: add-company
description: Adds an employer to companies.yml so the scraper picks up its job board, and proves the board returns postings. Use when asked to add, track, or onboard a company, or when handed a careers page URL.
---

# Add company

One employer, one entry in `companies.yml`, one proof that the scraper reads it.

## Steps

1. **Find the ATS.** The careers URL usually names it (`boards.greenhouse.io/acme`,
   `acme.wd5.myworkdayjobs.com/External`). A branded page such as `careers.acme.com` embeds
   one: fetch the HTML and search it for `greenhouse.io`, `lever.co`, `ashbyhq.com`,
   `smartrecruiters.com`, `workable.com`, `recruitee.com`, `pinpointhq.com`,
   `myworkdayjobs.com` or `oraclecloud.com`. The identifier table in CONTRIBUTING.md maps each
   URL shape to the fields the entry needs. A company on none of these platforms cannot be
   scraped; tell the user to submit roles through the issue form instead.
2. **Decide `security_company`.** Set it to `true` only when security products or services
   are the company's main business, because it admits every engineering title from that
   board. A bank with a security team is not one; a pentest consultancy is.
3. **Write the entry** under the platform key, alphabetical by name, with exactly the fields
   the CONTRIBUTING.md table gives for that platform. No comments in this file; the reason
   for anything unusual goes in the commit message.
4. **Prove it.** Run the platform alone:

   ```bash
   python .github/scripts/scrape_jobs.py --dry-run --board <ats>
   ```

   Done when the log holds `Checking Acme (<ats>/<id>)... ok (N postings` with N above zero.
   `zero`, `FAILED` or `CRASHED` means the identifier is wrong: fix it with the
   `triage-board` skill before opening a PR. Read the `NEW [...]` lines for the company as a
   sanity check on what the board contributes, and mention one or two in the PR.
5. **Commit** as `feat(companies): add Acme`, and fill the company-additions section of the
   PR template with the careers URL and the `Checking` line from step 4.

## Example

Careers URL `https://jobs.ashbyhq.com/wiz` is Ashby with slug `wiz`. Wiz sells cloud
security, so:

```yaml
ashby:
- name: Wiz
  slug: wiz
  security_company: true
```

`--dry-run --board ashby` then shows `Checking Wiz (ashby/wiz)... ok (84 postings, 1.2s)`.
