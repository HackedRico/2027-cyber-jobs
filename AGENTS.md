# 2027-cyber-jobs

A job board rendered by GitHub. `listings.json` holds every accepted role, the `README.md`
tables and `companies.md` are rebuilt from it, and GitHub Actions do all the writing on `main`.
The audience is cybersecurity students, so the intern and new-grad tables come first and a
change is judged by what a student sees. A wrong row on the board costs more than a missed one.

## Charter

A row belongs only if it is all three: a cybersecurity role (or any engineering role at a
`security_company: true` employer), internship or new grad or 0 to 2 years, and in the US or
Remote (US). CONTRIBUTING.md states the rules for humans; `classify.evaluate_job` is the
executable version, and the two must agree.

## Who writes what

Bots own these on `main`: `listings.json`, the README tables and stats block, `companies.md`,
and `.github/data/`. Change the board by changing its inputs (`companies.yml`, the scripts, or
an approved issue), never by editing the outputs.

Three workflows share the `readme-updates` concurrency group and commit as
`github-actions[bot]`: the scrape at 08:00 and 20:00 UTC, the link check at 02:00 UTC that
marks 🔒, and add-listing when a maintainer applies the `approved` label. A branch a day old is
behind `main`. Rebase before opening a PR, and take `main` for any conflict in a bot-owned file.

## Module map

- `classify.py` is the taxonomy and the US location rules. Stdlib only, so
  `test_classification.py` runs on a bare interpreter and the scraper, the issue flow and the
  tests share one source of truth.
- `common.py` is URL normalisation and issue-body parsing, shared for the same reason: dedup
  only works when the scraper and the submission flow normalise identically.
- `scrape_jobs.py` owns one `scrape_<ats>` function per platform, persistence
  (`seen_jobs.json` is per-posting last-seen, `board_baseline.json` is per-board counts for the
  silent-board alarm), and `main()`, which runs purge, renormalise, reclassify, the
  over-experience drop, the location repair and the vanished-req retire over existing rows
  before dedup and insert of new ones.
- `rebuild_readme.py` regenerates the tables between the `TABLE_START <type>` markers.
- `validate_issue.py` and `process_approved.py` are the community path: issue form, validation
  comment, `approved` label, row added, issue closed.

## Changing the classifier

Every keyword or location change lands with a case in `test_classification.py`, and the PR
names the example titles that flip. Rejects are cheap. An accept rule costs precision, so it
needs a real title the current rules miss and a reason no existing rule should catch it.
Existing rows are reclassified on the next scrape from title only, so a description-based rule
reaches new rows only. A new category is added in `CATEGORY_RULES` and in the issue template
dropdown together.

## Verifying a scraper change

1. `ruff check .github/scripts`, then `test_classification.py` and `test_scrapers.py`. All
   offline.
2. `scrape_jobs.py --dry-run --board <ats> --limit 3` while iterating on one parser.
3. A full `--dry-run` on `main` and on the branch, minutes apart, each redirected to a log,
   then `compare_runs.py before.log after.log`. A clean exit is the equivalence proof; the
   flipped rows it prints are the example titles the PR names.

Workday tenants are most of the wall clock, the workflow times out at 30 minutes, and a
cancelled run drops the whole scrape. Anything new that runs per Workday job needs the full
dry-run timing before merge.

## Adding or repairing a board

`companies.yml` carries no comments; the reason for a slug fix or drop goes in the commit
message. Two skills under `.claude/skills/` hold the procedures, and each ends on a `Checking
<board>... ok (N postings` line as its proof:

- [add-company](.claude/skills/add-company/SKILL.md) takes a careers URL to a verified entry.
- [triage-board](.claude/skills/triage-board/SKILL.md) takes a failed, regressed or silent
  board to a repaired or dropped entry. It holds what each ATS status code means.

## Code and test style

Everything under `.github/scripts/` is plain Python 3.12 checked by the `ruff.toml` at the
root. Beyond what the linter enforces:

- A comment says why, never what. Most comments in this repo cite the title or board that
  broke and the issue number; match that.
- Public functions carry a short docstring; private helpers do not.
- `classify.py` and `common.py` import from the standard library only. New third-party
  dependencies belong in `scrape_jobs.py` and get pinned in `requirements.txt`.
- Tests are plain scripts run with `python <file>`, no pytest. `test_classification.py` is
  data tables of `(input, expected)` rows looped through `check`; `test_scrapers.py` is
  `test_*` functions over mocked HTTP, each registered in the tuple at the bottom of the
  file. Add a row or a function in the same shape.
- A scraper function returns `None` for a broken fetch and `[]` for an empty board. The
  health check relies on the difference.

## Commits

Conventional Commits, lowercase, present tense, no trailing period, with scope `classify`,
`scraper`, `companies`, `skills`, `readme` or `ci`, as in `fix(classify): reject non-cyber
titles`. Every scraper or classifier PR states the example titles it flips. The bot commit
subjects are fixed strings the workflows own.
