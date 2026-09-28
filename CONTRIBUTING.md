# Contributing

There are four ways to help: submit a job, report a bad row, ask for a company, or improve the scraper.

## 1. Submit a job

Open a [new issue](../../issues/new/choose) with the **Add Job Listing** form. Within a minute a bot comments with a check of your submission and labels it `valid` or `needs-fix`. Edit the issue to fix anything it flags, and the same comment updates. The check is advisory: a maintainer reads the posting and decides. Once the maintainer adds the `approved` label, a workflow adds the row, and your issue closes after the row is on the board. If the row cannot be added, the bot comments the reason and removes the label.

The scraper applies the rules below to every posting, and the bot runs the same rules on your title and location. A row has to meet all three.

### A cybersecurity role

The title names security work: security, cyber, infosec, threat, forensics, vulnerability, penetration testing, red team, SOC, DFIR, GRC, identity and access, DevSecOps, or AI security and safety terms such as AI safety, AI red team, alignment or safeguards.

Each row gets one of these categories, and the form offers **Not sure** if none fits: AI Security & Safety, Offensive Security, SOC & Detection, Threat Intelligence, Forensics & IR, AppSec & ProdSec, Cloud & Infra Security, Identity & IAM, GRC & Risk, Security Engineering, and Engineering @ Security Co.

At a company whose main business is security (🛡️ in [companies.md](companies.md)), a title does not need a security word. Any title with one of these words counts: engineer, developer, software, DevOps, SRE, researcher, scientist, analyst, infrastructure, platform, backend, frontend, full stack, machine learning, or detection.

### An internship, new-grad or early-career role

The title has to say so:

- **Internship:** intern, internship, co-op, summer analyst, student trainee, or a season with a cohort year such as "Summer 2027".
- **New grad:** new grad, university or college grad, graduate, rotational, early talent, a development or pathways program, or a cohort year such as "2027".
- **Early career:** junior, associate, entry level, early career, apprentice, tier or level 1 and 2, or a level I or II after the job noun ("SOC Analyst II").

Two kinds of title with no level word can still get in, and both need the posting to show an early-career level, such as "0 to 2 years", a stated floor of 2 years or less, or "no experience required": an AI security or safety role, and a role at a security company.

**Years of experience.** A full-time role gets in when the posting's required floor is 2 years or less. The floor is what the posting requires, not the most it mentions:

- Preferred or nice-to-have qualifications do not count.
- A range counts from its low end, so "2 to 4 years" passes.
- When a posting offers routes by degree ("BS and 5 years, or MS and 3, or PhD and 0"), the easiest route counts.
- A stated ceiling of 0 to 2 years, or "no experience required", wins over any other bullet.
- Internships are exempt.

So an "Analyst II" posting that asks for 2 years is in, and a "Security Engineer I" posting that requires 4 is out.

**Titles that are always out:**

- **Seniority:** senior, sr, staff, principal, lead, manager, director, VP, head of, chief, distinguished, fellow, executive, expert, SME, supervisor, leader, and level III or IV (3 or 4). Architect titles are out unless they are a new-grad or intern cohort.
- **Physical and facility security:** security guard, physical security, industrial or personnel security, executive protection, transportation security, protective services, and "Security Officer" unless it names information security or cyber.
- **Business functions:** sales, account executive or manager, marketing, recruiting and HR, customer success and support, business development, finance, accounting, billing, procurement, internal audit, SOX, legal, and administrative and facilities roles.
- **Hardware and manufacturing:** ASIC, SoC design or verification, silicon, chip design, and mechanical, electrical, chemical, industrial, civil or process engineering.
- **Placeholder postings:** talent communities, talent networks, general interest, and hackathons.
- **Roles a student cannot apply to:** return offers for current interns (intern conversion, return intern), DoD SkillBridge slots for active-duty members, hiring events, and an internship whose season has already passed.
- **Retail and plant security:** asset protection, security licence postings, and hourly shift work with a pay rate in the title.

### In the United States

The role is in the US or remote within the US. The form accepts:

- `City, ST`, for example `Arlington, VA`
- a full state name, for example `Arlington, Virginia`
- `Arlington VA` and `Washington, D.C.`
- `Remote (US)` or `Remote`, and `United States`

Separate several locations with `;` or put one per line. The bot shows the form it will store, such as `Arlington, VA`. A foreign location next to a US one is dropped, and a role with no US location is out.

The application link must go to the posting itself, not a careers homepage, and open without a login.

## 2. Report a bad row

Use the **Report a Listing** form when a row is closed, is not a security role, is too senior, is outside the US, sits in the wrong table, or duplicates another row. Quote what the posting says, such as the years it asks for. Deleting a row by hand only sticks once its posting is gone, because the scraper re-adds any live posting it finds, so a report often leads to a rule change instead.

## 3. Add a company to the scraper

The easy way is the **Request a Company** form: give the careers URL and a role you saw there, and a maintainer adds and verifies the board.

To do it yourself: if an employer uses Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, Pinpoint, Workday, Oracle Recruiting Cloud, Eightfold, or Phenom, add it to [`companies.yml`](companies.yml) in a pull request and the scraper picks up its roles.

Find the identifier from the company's careers page URL:

| Platform | Careers URL looks like | companies.yml entry |
| -------- | ---------------------- | ------------------- |
| Greenhouse | `boards.greenhouse.io/acme` or `job-boards.greenhouse.io/acme` | `- name: Acme` / `slug: acme` |
| Lever | `jobs.lever.co/acme` | `- name: Acme` / `slug: acme` |
| Ashby | `jobs.ashbyhq.com/acme` | `- name: Acme` / `slug: acme` |
| SmartRecruiters | `jobs.smartrecruiters.com/Acme` | `- name: Acme` / `slug: Acme` |
| Workable | `apply.workable.com/acme` | `- name: Acme` / `slug: acme` |
| Recruitee | `acme.recruitee.com` | `- name: Acme` / `slug: acme` |
| Pinpoint | `acme.pinpointhq.com` | `- name: Acme` / `slug: acme` |
| Workday | `acme.wd5.myworkdayjobs.com/External` | `- name: Acme` / `tenant: acme` / `instance: wd5` / `board: External` |
| Oracle | `acme.fa.us2.oraclecloud.com/...CandidateExperience/en/sites/CX_1` | `- name: Acme` / `host: acme.fa.us2.oraclecloud.com` / `site: CX_1` |
| Eightfold | `acme.eightfold.ai/careers?domain=acme.com` | `- name: Acme` / `tenant: acme` / `domain: acme.com` |
| Phenom | `careers.acme.com/us/en/search-results`, page source loads `cdn.phenompeople.com` | `- name: Acme` / `host: careers.acme.com` / `lang: en_us` / `country: us`, read off the `/us/en/` path |

Set `security_company: true` for companies whose main business is security, such as vendors and consultancies. It lets the technical titles listed under section 1 through without a security word.

Please verify the endpoint returns JSON before opening the PR, e.g.:

```bash
curl -s "https://boards-api.greenhouse.io/v1/boards/acme/jobs" | head -c 200
```

## 4. Improve the scraper

The filtering logic lives in [`classify.py`](.github/scripts/classify.py): the keyword taxonomy (cyber keywords, new-grad/early-career signals, seniority rejects) and the US location rules. It is dependency-free and fully covered by `test_classification.py`, so add a case there for any change. The ATS scrapers, persistence, and orchestration live in [`scrape_jobs.py`](.github/scripts/scrape_jobs.py). PRs that tighten precision or add coverage are welcome. Include a few example titles the change affects, and update section 1 of this file when a rule a submitter reads changes.

## Testing locally

```bash
pip install -r requirements-dev.txt
ruff check .github/scripts                            # lint
python .github/scripts/test_classification.py         # classification spot checks (run after keyword changes)
python .github/scripts/test_scrapers.py               # offline ATS-parser tests
python .github/scripts/test_community.py              # issue forms, submission checks, approval flow
python .github/scripts/test_health.py                 # output checks and the scraper health alarm
python .github/scripts/check_outputs.py               # validate listings.json and the README tables
python .github/scripts/scrape_jobs.py --dry-run       # full scrape + classify, writes NOTHING
python .github/scripts/scrape_jobs.py --dry-run --board greenhouse --limit 3  # fast single-board iteration
python .github/scripts/check_slugs.py                 # find configured slugs that return no jobs (exits 1 if any)
python .github/scripts/compare_runs.py before.log after.log  # what a change flips between two dry-run logs
```

> ⚠️ `python .github/scripts/scrape_jobs.py` **without** `--dry-run` hits every live API and overwrites `listings.json`, `README.md`, `companies.md`, and `.github/data/`. Use `--dry-run` for local testing.

## Working with an AI coding agent

[AGENTS.md](AGENTS.md) is the agent-facing map of the repo: who writes which files, how to
verify a change, and the rules for classifier edits. Two skills under
[`.claude/skills/`](.claude/skills/) walk an agent through adding a company and through
repairing a board that stopped returning postings; both work as checklists for humans too.
