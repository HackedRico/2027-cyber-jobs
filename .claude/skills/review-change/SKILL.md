---
name: review-change
description: Reviews a pull request, branch or diff in this repo for leaks, scrape cost and broken wiring, reproducing every finding before reporting it. Use when asked to review or code review a change here, before marking a classifier, scraper, companies or workflow PR ready, or when a dry run or corpus diff moves rows nobody expected.
---

# Review change

A review answers one question: what does a student see differently, and is each difference
right? A wrong row costs more than a missed one, so the hunt is for **leaks**: rows the change
lets onto the board that are not entry-level US security, cloud or solutions work. Every
finding is **reproduced**, with its input and wrong output, before it is reported.

## Steps

1. **Map the change.** Run `git diff origin/main...HEAD --stat` and give every touched file an
   area from [REFERENCE.md](REFERENCE.md): classifier, scraper and search terms, companies,
   workflows or docs. Done when each file has an area and you have read each area's checklist.
2. **Run the gates.** `ruff check .github/scripts`, then every `run: python
   .github/scripts/test_*.py` line in `.github/workflows/tests.yml`. Done when all pass; a
   failing gate is the first finding.
3. **Read the blast radius.** `git diff origin/main...HEAD --
   .github/scripts/fixtures/title_corpus.tsv` lists every real title, stored rows included,
   whose verdict the change moves. Judge each changed line as the row a student would see.
   Done when every changed line is called right or written up as a finding.
4. **Probe every new accept path.** For each term, regex or branch that admits a title, write
   **probes** from every family in REFERENCE.md that its words could reach, and run each
   through `classify.judge_job(title, 'Austin, TX', '', security_company)` on main and on the
   branch. Done when every family has met every new accept path.
5. **Check the live run** when the change touches scraping, search terms, companies or an
   accept rule: a full `--dry-run` on main and on the branch, `compare_runs.py`, and both
   scrape timings. Done when every `NEW`, `DROP` and `REFRESHED` line is called right or a
   finding, and the scrape phase sits far inside the workflow's 30-minute timeout.
6. **Report.** Findings ranked by severity, each with `file:line`, the input, the wrong output
   and a fix, marked reproduced or suspected; unreproduced suspicions go in a closing list.
   Each leak, once fixed, becomes a reject row in `test_classification.py`.

## Splitting a large review

For a diff across several areas, dispatch one read-only reviewer per area with that area's
checklist, its probe families, a scratch directory and the rule that a finding counts only
once reproduced. Merge the reports, fix, and rerun steps 2 and 3 on the fixes.

## Example

A branch admits titles where "cloud" leads a role noun. Step 3 shows `Junior Scrum Master,
Cloud Engineering` moving from `out|not-cyber` to `earlycareer|Cloud Engineering`, and the
team-name probe family reproduces the class. The finding reads: `classify.py` at
`CLOUD_ENGINEERING_RE`, input `Junior Scrum Master, Cloud Engineering`, got `('earlycareer',
'Cloud Engineering')`, want `None`; fix: reject a cloud title whose head is a non-technical
job, plus a reject row.

## Changing this skill

The checklists and probe families record traps past changes fell into; add a line when a
review finds a new class of leak. Keep the review's shape: gates, blast radius, probes, live
run, reproduced findings.
