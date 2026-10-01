#!/usr/bin/env python3
"""Flag configured job-board slugs that return nothing or belong to someone else.

    python .github/scripts/check_slugs.py [--board greenhouse]

Read-only maintenance tool: hits each board's list endpoint and reports slugs
that error or return zero postings, plus SmartRecruiters ids whose careers page
redirects because no such company exists, so dead entries can be fixed or dropped
from companies.yml. (The scraper's run summary flags regressions automatically;
this is the deeper on-demand sweep.)

It also prints the name each board reports for its owner next to the
configured name, and marks a likely mismatch. Ashby slug `menlo` returned
postings for months as "Menlo Security" while it belonged to Menlo Research,
a robotics lab. Greenhouse, Ashby, SmartRecruiters, Workable and Recruitee
report an org name; Lever and Pinpoint do not, so their check is whether the
posting text names the company. Workday, Oracle, Eightfold, Phenom and Jibe
are skipped: their host is already the employer's own. A mismatch is a
warning to review, not a failure, since renames such as ConsenSys to MetaMask
and placeholders such as Corelight's "Job Board" are harmless.
"""
import argparse
import re
import sys
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import scrape_jobs as sj  # noqa: E402

# Words that say nothing about which company a name is: legal suffixes and the
# industry words half the config shares. Without the second set "Acme
# Security" would match any other "... Security" board.
GENERIC_NAME_WORDS = {
    'inc', 'llc', 'ltd', 'corp', 'corporation', 'co', 'company', 'the', 'and', 'of',
    'group', 'holdings', 'hq', 'ai', 'io', 'security', 'cyber', 'cybersecurity',
    'technologies', 'technology', 'software', 'systems', 'labs', 'lab', 'solutions',
    'careers', 'jobs',
}

ASHBY_ORG_QUERY = (
    'query ApiOrganizationFromHostedJobsPageName($organizationHostedJobsPageName: String!, '
    '$searchContext: OrganizationSearchContext) { organization: '
    'organizationFromHostedJobsPageName(organizationHostedJobsPageName: '
    '$organizationHostedJobsPageName, searchContext: $searchContext) '
    '{ name publicWebsite } }')


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


def _words(name):
    return re.findall(r'[a-z0-9]+', str(name).lower())


def _distinctive(name):
    words = _words(name)
    return {w for w in words if w not in GENERIC_NAME_WORDS} or set(words)


def _acronyms(name):
    # Boards and postings often use the short form: SmartRecruiters names
    # Lawrence Livermore National Laboratory 'LLNL', Very Good Security's
    # postings say 'VGS' and SANS Institute's say 'SANS'.
    words = str(name).split()
    found = {w.lower() for w in words if len(w) >= 3 and w.isupper() and w.isalpha()}
    if len(words) >= 3:
        found.add(''.join(w[0] for w in words).lower())
    return found


def names_match(configured, reported):
    """Return whether two company names plausibly name the same employer.

    True on any shared distinctive word ('Abnormal AI' and 'Abnormal
    Security'), when one name run together contains the other ('Ping
    Identity' and 'PingIdentity'), or when one is the other's acronym.
    """
    if _distinctive(configured) & _distinctive(reported):
        return True
    a, b = ''.join(_words(configured)), ''.join(_words(reported))
    if a and b and (a in b or b in a):
        return True
    return bool((_acronyms(configured) & {b}) or (_acronyms(reported) & {a}))


def named_in_text(name, texts):
    """Return whether any of `texts` mentions `name` or its acronym, ignoring case."""
    wanted = ''.join(_words(name))
    acronyms = _acronyms(name)
    for text in texts:
        words = _words(sj.strip_html(text or ''))
        if (wanted and wanted in ''.join(words)) or acronyms & set(words):
            return True
    return False


def _greenhouse_owner(slug, label):
    data = sj.fetch_json(f'https://boards-api.greenhouse.io/v1/boards/{slug}', label=label)
    return (data or {}).get('name'), None


def _ashby_owner(slug, label):
    # The posting API carries no org name; the public jobs page's GraphQL does,
    # with the org's own website, which is what exposed Ashby `primer` as a
    # K-8 school network at primer.com.
    data = sj.fetch_json(
        'https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiOrganizationFromHostedJobsPageName',
        method='POST', label=label,
        json={'operationName': 'ApiOrganizationFromHostedJobsPageName',
              'variables': {'organizationHostedJobsPageName': slug,
                            'searchContext': 'JobBoard'},
              'query': ASHBY_ORG_QUERY})
    org = ((data or {}).get('data') or {}).get('organization') or {}
    return org.get('name'), org.get('publicWebsite')


def _smartrecruiters_owner(slug, label):
    data = sj.fetch_json(f'https://api.smartrecruiters.com/v1/companies/{slug}/postings',
                         params={'limit': 1}, label=label)
    content = (data or {}).get('content') or [{}]
    return (content[0].get('company') or {}).get('name'), None


def _workable_owner(slug, label):
    data = sj.fetch_json(f'https://apply.workable.com/api/v1/widget/accounts/{slug}',
                         label=label)
    return (data or {}).get('name'), None


def _recruitee_owner(slug, label):
    data = sj.fetch_json(f'https://{slug}.recruitee.com/api/offers/', label=label)
    offers = (data or {}).get('offers') or [{}]
    return offers[0].get('company_name'), None


OWNER_LOOKUPS = {
    'greenhouse': _greenhouse_owner,
    'ashby': _ashby_owner,
    'smartrecruiters': _smartrecruiters_owner,
    'workable': _workable_owner,
    'recruitee': _recruitee_owner,
}
# Boards whose API names no owner, checked against the posting text instead.
TEXT_CHECKED_BOARDS = {'lever', 'pinpoint'}


def ownership_line(board, entry, jobs):
    """Return (line, mismatch) describing who a simple board says it belongs to.

    `jobs` is what the board's scraper returned, used for the posting-text
    check on boards with no org name. Returns (None, False) for a board with
    neither.
    """
    name, slug = entry['name'], entry['slug']
    prefix = f'{board}/{sj._oneline(name)} ({slug})'
    if board in OWNER_LOOKUPS:
        reported, website = OWNER_LOOKUPS[board](slug, f'{name} {board} owner')
        if not reported:
            return f'{prefix}: board reports no name', False
        site = f', website {sj._oneline(website)}' if website else ''
        return (f'{prefix}: board name "{sj._oneline(reported)}"{site}',
                not names_match(name, reported))
    if board in TEXT_CHECKED_BOARDS and jobs:
        texts = [j.get('description', '') for j in jobs]
        if named_in_text(name, texts):
            return f'{prefix}: named in posting text', False
        return f'{prefix}: not named in any posting text', True
    return None, False


def _scraper_args(board, entry):
    args = (entry['name'], entry['slug'])
    if board in sj.APPLY_LINK_BOARDS and entry.get('apply_hosts'):
        args += (tuple(entry['apply_hosts']),)
    return args


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--board', help='only check this ATS (e.g. greenhouse, ashby)')
    args = parser.parse_args(argv)

    def want(name):
        return not args.board or args.board == name

    config = yaml.safe_load(Path('companies.yml').read_text()) or {}
    problems, owners, mismatches = [], [], []

    def record(board, name, ident, jobs):
        if jobs is None:
            problems.append(f'{board}/{sj._oneline(name)} ({ident}): ERROR / unreachable')
        elif len(jobs) == 0:
            problems.append(f'{board}/{sj._oneline(name)} ({ident}): 0 postings')

    for board, fn in sj.SIMPLE_BOARDS.items():
        if not want(board):
            continue
        for e in config.get(board) or []:
            if board == 'smartrecruiters' and _smartrecruiters_id_unknown(e['slug']):
                problems.append(f'{board}/{sj._oneline(e["name"])} ({e["slug"]}): unknown id '
                                '(careers page redirects)')
                continue
            jobs = fn(*_scraper_args(board, e))
            record(board, e['name'], e['slug'], jobs)
            line, mismatch = ownership_line(board, e, jobs)
            if line:
                owners.append(line)
                if mismatch:
                    mismatches.append(line)

    if want('workday'):
        for e in config.get('workday') or []:
            jobs = sj.scrape_workday(e['name'], e['tenant'], e['instance'],
                                     e.get('board', ''), e.get('security_company', False))
            record('workday', e['name'], e['tenant'], jobs)

    if want('oracle'):
        for e in config.get('oracle') or []:
            record('oracle', e['name'], e['host'],
                   sj.scrape_oracle(e['name'], e['host'], e['site']))

    if want('eightfold'):
        for e in config.get('eightfold') or []:
            record('eightfold', e['name'], e['tenant'],
                   sj.scrape_eightfold(e['name'], e['tenant'], e['domain'],
                                       e.get('security_company', False)))

    if want('phenom'):
        for e in config.get('phenom') or []:
            record('phenom', e['name'], e['host'],
                   sj.scrape_phenom(e['name'], e['host'], e['lang'], e['country'],
                                    e.get('security_company', False)))

    if want('jibe'):
        for e in config.get('jibe') or []:
            record('jibe', e['name'], e['host'], sj.scrape_jibe(e['name'], e['host']))

    if owners:
        print('Board owners (configured name: what the board reports):')
        for line in owners:
            print(f'  {"?" if line in mismatches else " "} {line}')
        print()
    if mismatches:
        print('Owner names to review (a rename is fine; another employer is not):')
        for line in mismatches:
            print(f'  - {line}')
        print(f'\n{len(mismatches)} owner name(s) to review\n')

    if problems:
        print('Slugs needing attention:')
        for p in sorted(problems):
            print(f'  - {p}')
        print(f'\n{len(problems)} slug(s) to review')
    else:
        print('All configured slugs returned postings.')
    # Exit non-zero so the sweep can gate a workflow or a pre-merge check on a
    # companies.yml change, instead of a clean-looking green log that nobody
    # reads to the bottom. An owner mismatch does not count: known renames
    # would keep the gate red.
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
