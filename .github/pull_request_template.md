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

<!-- Delete this section if not applicable. -->

- [ ] Verified the board API returns JSON (e.g. `curl -s "https://boards-api.greenhouse.io/v1/boards/<slug>/jobs" | head -c 200`)
- [ ] `security_company: true` only set for pure-play security vendors/consultancies
- Company careers page URL:

## For scraper/filter changes

<!-- Delete this section if not applicable. -->

- [ ] `python .github/scripts/test_classification.py` passes
- [ ] Added/updated test cases in `test_classification.py` for the new behavior
- Example job titles this change affects (accepted or rejected differently than before):

## Checklist

- [ ] No senior/staff/manager, internship, or non-US listings are introduced by this change
- [ ] I did not hand-edit `listings.json` or the README tables (they are rebuilt automatically)
