#!/usr/bin/env python3
"""Flag configured job-board slugs that return nothing (likely stale/renamed).

    python .github/scripts/check_slugs.py

Read-only maintenance tool: hits each board's list endpoint and reports slugs
that error or return zero postings, plus SmartRecruiters ids whose careers page
redirects because no such company exists, so dead entries can be fixed or dropped
from companies.yml. (The scraper's run summary flags regressions automatically;
this is the deeper on-demand sweep.)
"""
import sys
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import scrape_jobs as sj  # noqa: E402


def _smartrecruiters_id_unknown(identifier):
    # The postings API answers 200 with an empty list for any id, so a typo
    # reads as a quiet board. The public careers page 302s to
    # jobs.smartrecruiters.com for an id that does not exist (zzqnotarealco987)
    # and returns 200 for a real one.
    try:
        r = requests.get(f'https://careers.smartrecruiters.com/{identifier}',
                         headers=sj.HEADERS, timeout=sj.REQUEST_TIMEOUT,
                         allow_redirects=False)
    except requests.RequestException:
        return False
    return r.status_code == 302


def main():
    config = yaml.safe_load(Path('companies.yml').read_text()) or {}
    problems = []

    def record(board, name, ident, jobs):
        if jobs is None:
            problems.append(f'{board}/{name} ({ident}): ERROR / unreachable')
        elif len(jobs) == 0:
            problems.append(f'{board}/{name} ({ident}): 0 postings')

    for board, fn in sj.SIMPLE_BOARDS.items():
        for e in config.get(board) or []:
            if board == 'smartrecruiters' and _smartrecruiters_id_unknown(e['slug']):
                problems.append(f'{board}/{e["name"]} ({e["slug"]}): unknown id '
                                '(careers page redirects)')
                continue
            record(board, e['name'], e['slug'], fn(e['name'], e['slug']))

    for e in config.get('workday') or []:
        jobs = sj.scrape_workday(e['name'], e['tenant'], e['instance'],
                                 e.get('board', ''), e.get('security_company', False))
        record('workday', e['name'], e['tenant'], jobs)

    for e in config.get('oracle') or []:
        record('oracle', e['name'], e['host'],
               sj.scrape_oracle(e['name'], e['host'], e['site']))

    if problems:
        print('Slugs needing attention:')
        for p in sorted(problems):
            print(f'  - {p}')
        print(f'\n{len(problems)} slug(s) to review')
    else:
        print('All configured slugs returned postings.')
    # Exit non-zero so the sweep can gate a workflow or a pre-merge check on a
    # companies.yml change, instead of a clean-looking green log that nobody
    # reads to the bottom.
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
