# Contributing

Thanks for helping keep this board useful! There are three ways to contribute.

## 1. Submit a job via issue (easiest)

Open a [new issue](../../issues/new/choose) using the **Add Job Listing** template. A maintainer will review it; once the `approved` label is added, a workflow automatically adds it to the list and closes your issue.

Ground rules — listings must be:

- **Cybersecurity-related** — security engineering, SOC/detection, offensive security, AppSec, threat intel, DFIR, cloud security, IAM, or GRC. Engineering roles at pure-play security companies also count.
- **New-grad or early-career** — no prior full-time experience required (0–2 years). No internships or co-ops, no senior/staff/manager roles.
- **Located in the United States** — or Remote (US). Use `City, ST` format, separate multiple locations with `;`.
- **Directly linkable** — the application link goes to the posting, not a careers homepage, and is viewable without a login.

## 2. Add a company to the scraper

If an employer uses Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, Pinpoint, or Workday, add it to [`companies.yml`](companies.yml) in a pull request and the scraper will pick up its roles automatically.

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

Set `security_company: true` for pure-play security vendors/consultancies — that allows generic engineering titles (not just titles containing security keywords) to be listed from that company.

Please verify the endpoint returns JSON before opening the PR, e.g.:

```bash
curl -s "https://boards-api.greenhouse.io/v1/boards/acme/jobs" | head -c 200
```

## 3. Improve the scraper

The filtering logic lives in [`scrape_jobs.py`](.github/scripts/scrape_jobs.py) as keyword lists near the top of the file (cyber keywords, new-grad/early-career signals, seniority rejects, location rules). PRs that tighten precision or add coverage are welcome — please include a few example titles the change affects.

## Testing locally

```bash
pip install requests pyyaml
python .github/scripts/scrape_jobs.py     # scrape + update listings.json + README
python .github/scripts/rebuild_readme.py  # rebuild tables from listings.json only
python .github/scripts/check_links.py     # mark dead links
```
