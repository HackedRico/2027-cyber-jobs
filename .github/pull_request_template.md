<!-- Thanks for contributing! Job listings themselves go through an issue, not a PR:
     https://github.com/HackedRico/2027-cyber-jobs/issues/new/choose
     See CONTRIBUTING.md for details. -->

## What does this PR do?

<!-- One or two sentences. -->

## Type of change

- [ ] Add company/companies to the scraper (`companies.yml`)
- [ ] Scraper or filter logic change (`.github/scripts/`)
- [ ] Docs / README / templates
- [ ] Other (explain above)

## For company additions

<!-- Delete this section if not applicable. CONTRIBUTING.md section 3 says how to get each line. -->

- [ ] `security_company: true` only set for pure-play security vendors/consultancies
- Company careers page URL:
- Page on the employer's own site that links to the board:
- `Checking <Company> (<ats>/<id>)... ok (N postings` line from `scrape_jobs.py --dry-run --board <ats>`:
- Owner line from `check_slugs.py --board <ats>` (none for Workday, Oracle, Eightfold, Phenom or Jibe):

## For scraper/filter changes

<!-- Delete this section if not applicable. -->

- [ ] `python .github/scripts/test_classification.py` passes
- [ ] Added/updated test cases in `test_classification.py` for the new behavior
- [ ] `python .github/scripts/test_corpus.py` passes; if verdicts moved, the corpus was rewritten with `--update` and every changed line was read
- Example job titles this change affects (accepted or rejected differently than before):

## Checklist

- [ ] No senior/staff/manager, internship, or non-US listings are introduced by this change
- [ ] I did not hand-edit `listings.json` or the README tables (they are rebuilt automatically)
