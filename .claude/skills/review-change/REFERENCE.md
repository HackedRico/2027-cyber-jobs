# Review reference

One checklist per area, then the probe families. Each line is a trap a past change fell into.

## Classifier

`classify.py`, its tables in `test_classification.py`, and the corpus.

- **Gate order.** `judge_job` runs the title rejects, the cyber check, the facility check, the
  level, the experience floor, then the location. A reject anywhere earlier wins.
- **Guards that yield to a keyword.** `NON_CYBER_SECURITY_RE`, `DEPARTMENT_REJECT_RE`, the
  weak security role rule and the program-analyst rule reject only when `_has_cyber_keyword`
  finds nothing. A term added to `CYBER_KEYWORDS` or `CYBER_REGEXES` also unlocks every title
  those guards held, so re-probe the physical, department and facility families after any
  keyword addition: "watch floor" as a keyword let "GSOC Watch Floor Analyst I" back in.
- **Narrow accept paths.** The security-team, tool, cloud and solutions rules live in
  `is_cyber_title` and unlock none of those guards. A term with other senses belongs there.
- **Text form.** Every title rule reads `fold_title(title)`: lowercased, whitespace collapsed,
  a spaced `&` read as `and`. `_has_cyber_keyword` also reads an unspaced `&` as `and`.
  `NEWGRAD_SIGNALS` match as substrings, so "analyst program" matched "Analyst Programmer";
  word-bound a new signal. A gap between the words of a pattern is a leak path: the cloud
  rule's gap crossed a bracket ("Python Backend Developer with AWS & SQL (Software Engineer
  II)") and let a free "and" reach a team name ("Java Developer I, Cloud and Data Platform
  Engineering"), and a role inside brackets names a team ("(Systems Engineering) - Cloud
  Payments").
- **Level order.** `classify_level` tries intern words, new-grad words, a leveled I or II, a
  summer season with a cohort year, a bare cohort year, early-career words, then the
  description. A new-grad word outranks the summer season and so escaped the passed-season
  reject ("Summer <past year> Cybersecurity Fellowship"). A new level noun also feeds
  `LEVELED_SENIOR_RE` and the multi-level rule.
- **Category order.** `CATEGORY_RULES` is first match. A vendor, place or program word in a
  category regex relabels every title holding it: bare "sentinel" moved Northrop's Sentinel
  missile rows into SOC, bare "palo alto" moved Palo Alto, CA rows. A late category gates on
  its admitting predicate in `infer_category` and yields to `_has_cyber_keyword`.
- **Stored rows.** `test_corpus.py --base` judges the current `listings.json` rows as well as
  the corpus. The scrape refreshes a stored row's category only while its posting is live.
- **Docs agree.** CONTRIBUTING.md states every rule a submitter reads. A new category lands in
  `CATEGORY_RULES`, the issue form, CONTRIBUTING.md and `CATEGORY_BLURBS` together;
  `test_wiring.py` and `test_classification.py` check all four.

## Scraper and search terms

`scrape_jobs.py`, `test_scrapers.py`.

- **Workday cxs** sorts results by date, matches any word of a term and ignores quotes. A term
  past 15 pages marks the sweep partial, which sends retirement to the detail endpoint.
- **Cost.** Each Workday term costs up to 15 requests on every tenant. Time the full dry run:
  the workflow stops at 30 minutes, and a cancelled run drops the whole scrape.
- **amazon.jobs** takes `OR` and quoted phrases. Read `hits` for each phrase: a bare
  "solutions architect" added thousands of senior postings.
- **None and empty.** A scraper returns `None` for a broken fetch and `[]` for an empty board;
  the health check relies on the difference.
- **Mocks follow the terms.** `test_scrapers.py` registers a mock per search term; loop over
  the term tuples so a new term is mocked too.

## Companies

`companies.yml`.

- Each new board carries the `add-company` skill's proof lines.
- `security_company: true` admits every engineering title, so only pure-play security firms
  get it.
- A new board brings titles the rules have never met. Read its `NEW` rows and fix leaks in the
  same PR, as with KeyBank's "Equity Research - Cybersecurity" and Merck's "Forensic Services
  Laboratory Intern".

## Workflows

`.github/workflows/`.

- Writers share `concurrency: group: readme-updates` with `queue: max`, and each runs
  `check_outputs.py` before every commit. Some linters reject `queue:`; GitHub accepts it.
- A push made with `GITHUB_TOKEN` starts no workflow, so Pages follows the writers through
  `workflow_run`.
- Actions pin a full commit SHA and installs use `--require-hashes`; `test_wiring.py` checks
  both.

## Tests and tooling

`testkit.py`, the `test_*.py` suites, `fixtures/`, `refresh_corpus.py`.

- **Determinism.** A suite reads no clock and no network: `testkit.py` pins `datetime` and
  cuts sockets, DNS and proxies, and `test_wiring.py` proves each suite imports it first and
  reads no clock itself, `time.time()` included. Run a
  changed suite twice with different `PYTHONHASHSEED` values and with the OS clock moved a
  year ahead; the output should not change.
- **A check that can fail.** Break the thing a new check guards, in a scratch copy, and
  watch it fail. An invariant that holds by construction, such as a category drawn from the
  set it is checked against, tests nothing.
- **Snapshots.** A corpus refresh lands in its own commit. A rule change that rewrites the
  snapshot shows only verdict moves.

## Docs and skills

AGENTS.md, CONTRIBUTING.md, `.claude/skills/`.

- CONTRIBUTING.md and AGENTS.md state what the code does now; check every claim a change
  touches against the code.
- A skill keeps its SKILL.md short with one level of reference, and carries no dates.

## Probe families

For each new accept path, probe every family its words could reach.

- **Other senses of the word:** medical device reporting (MDR), electronic data recorder
  (EDR), the FAA's ACAS X, SOAR program names, PCI hardware, Northrop's Sentinel, Palo Alto
  the city, point clouds, cloud seeding, Saint Cloud, Splunk observability.
- **Products named for the domain:** Salesforce Service Cloud, Oracle Cloud HCM, SAP and
  Workday "Cloud", ServiceNow.
- **A team name read as the role:** "Junior Scrum Master, Cloud Engineering", "Associate
  Technical Writer, AWS Developer Documentation", "Associate Pricing Analyst, Okta".
- **Physical and corporate security:** a GSOC watch floor, workplace violence threat
  assessment, electronic security systems, a regional security team, a badge or patient
  access office.
- **Finance and business:** equity research covering cyber, internal or financial audit,
  model risk, supplier or credit risk.
- **Industrial lookalikes:** field sales engineers in HVAC, customer engineers who repair
  equipment, forensic chemistry labs.
- **People who run a program:** "Development Program Specialist", "Academy Instructor".
- **Level traps:** senior, staff and manager forms, a "2/3" multi-level title, a leveled I or
  II where the rule wants an early-career word, a past summer season, a Big 4 "Associate -
  Summer <year>" start date, a bank's "Summer Associate".
- **Text form:** the title with non-breaking or doubled spaces, in upper case, with "&" for
  "and". `test_corpus.py` holds every accepted title to these.
