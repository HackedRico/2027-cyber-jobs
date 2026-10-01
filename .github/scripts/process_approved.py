#!/usr/bin/env python3
"""Add community listings from issues labeled 'approved' to listings.json.

Runs twice per add-listing workflow. The default mode adds or reopens rows,
rebuilds the README, writes each issue's outcome to $GITHUB_OUTPUT and the
added and revived rows to $RUN_EVENTS_FILE for notify.py. An issue whose body
changed after its `approved` label went on is skipped, since the maintainer
never saw that version. `--notify` runs after
the push step and comments on, closes or unlabels the issues. Closing an issue
before the row reaches `main` loses the row for good when the push fails,
because only open approved issues are fetched.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import rebuild_readme  # noqa: E402
from classify import CATEGORY_ALLOWLIST, infer_category, listing_dedup_key  # noqa: E402
from common import (  # noqa: E402
    gh_headers,
    link_host,
    md_escape,
    normalize_submitted_location,
    normalize_url,
    oneline,
    parse_issue_body,
    security_company_names,
    strip_controls,
    validate_location,
    write_run_events,
)

LISTINGS_FILE = Path('listings.json')
COMPANIES_FILE = Path('companies.yml')
API = 'https://api.github.com'

ADDED_COMMENT = '✅ Listing added to the board. Thanks for contributing!'
REVIVED_COMMENT = ('✅ This role was closed on the board, so your link reopened it. '
                   'Thanks for contributing!')
DUPLICATE_COMMENT = 'This role is already on the board ({match}), so closing. Thanks!'
SKIPPED_COMMENT = (
    'This submission could not be added: {reason}\n\n'
    'Edit the issue to fix it. A maintainer will add the `approved` label again.'
)
EDITED_REASON = ('the issue was edited after a maintainer approved it, so the edit '
                 'needs a fresh review.')
# Outcomes whose row only exists once the push lands.
ROW_OUTCOMES = {'added': ADDED_COMMENT, 'revived': REVIVED_COMMENT}


def get_approved_issues(token, repo):
    issues = []
    page = 1
    while True:
        resp = requests.get(
            f'{API}/repos/{repo}/issues',
            headers=gh_headers(token),
            params={'state': 'open', 'labels': 'approved', 'per_page': 100,
                    'page': page},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f'GitHub API error: {resp.status_code}')
            break
        batch = resp.json()
        if not batch:
            break
        issues.extend(i for i in batch if 'pull_request' not in i)
        page += 1
    return issues


def _stamp(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def approved_at(token, repo, number):
    """When the `approved` label last went on the issue, or None if unknown."""
    latest, page = None, 1
    while True:
        resp = requests.get(f'{API}/repos/{repo}/issues/{number}/events',
                            headers=gh_headers(token),
                            params={'per_page': 100, 'page': page}, timeout=10)
        if resp.status_code != 200:
            print(f'  Issue #{number}: events API error {resp.status_code}')
            return None
        batch = resp.json()
        for event in batch:
            if (event.get('event') == 'labeled'
                    and (event.get('label') or {}).get('name') == 'approved'):
                stamp = _stamp(event.get('created_at'))
                if stamp and (latest is None or stamp > latest):
                    latest = stamp
        if len(batch) < 100:
            return latest
        page += 1


def body_edited_at(token, repo, number):
    """(ok, when the body was last edited) from GraphQL `lastEditedAt`.

    REST has no body-edit time: `updated_at` also moves on every label and
    comment. `lastEditedAt` is null for a body never edited since it was filed.
    """
    owner, name = repo.split('/', 1)
    query = ('query($owner: String!, $name: String!, $number: Int!) {'
             ' repository(owner: $owner, name: $name) {'
             ' issue(number: $number) { lastEditedAt } } }')
    resp = requests.post(f'{API}/graphql', headers=gh_headers(token), timeout=10,
                         json={'query': query, 'variables': {
                             'owner': owner, 'name': name, 'number': int(number)}})
    data = resp.json() if resp.status_code == 200 else {}
    issue = ((data.get('data') or {}).get('repository') or {}).get('issue')
    if data.get('errors') or issue is None:
        print(f'  Issue #{number}: GraphQL error {resp.status_code} {oneline(resp.text[:200])}')
        return False, None
    return True, _stamp(issue.get('lastEditedAt'))


def approval_is_current(token, repo, number):
    """Whether the body is unchanged since approval; None when unreadable."""
    labeled = approved_at(token, repo, number)
    ok, edited = body_edited_at(token, repo, number)
    if labeled is None or not ok:
        return None
    return edited is None or edited <= labeled


def screen_edited(token, repo, issues):
    """Split approved issues into (current, results for the rest).

    A submitter can change the Direct Application Link after approval while
    this run waits in the readme-updates queue. The body was fetched before
    `lastEditedAt` is read, so an edit landing between the two still counts
    as after approval.
    """
    current, results = [], []
    for issue in issues:
        number = issue.get('number')
        verdict = approval_is_current(token, repo, number)
        if verdict:
            current.append(issue)
        elif verdict is None:
            # Left approved, so the next add-listing run retries it.
            results.append({'number': number, 'outcome': 'held',
                            'detail': 'could not read when it was approved or edited'})
        else:
            results.append({'number': number, 'outcome': 'skipped',
                            'detail': EDITED_REASON})
    return current, results


def load_security_companies(path=COMPANIES_FILE):
    """Lowercased names of the `security_company: true` employers, or empty."""
    try:
        with open(path) as f:
            return security_company_names(yaml.safe_load(f))
    except (OSError, yaml.YAMLError) as e:
        print(f'Could not read {path} ({e}); treating no company as a security company')
        return set()


def fields_to_listing(fields, security_companies=frozenset(), today=None):
    """Build a listings.json row from parsed issue-form fields.

    Control characters are stripped: a rendered issue hides them, so the
    maintainer approved the text without them.
    """
    fields = {k: strip_controls(v) for k, v in fields.items()}
    listing_type = fields.get('Listing Type', '')
    if 'Intern' in listing_type:
        level = 'intern'
    elif 'New Grad' in listing_type:
        level = 'newgrad'
    else:
        level = 'earlycareer'
    company = fields.get('Company Name', '').strip()
    role = fields.get('Role / Job Title', '').strip()
    category = fields.get('Category', '').strip()
    if category not in CATEGORY_ALLOWLIST:
        # 'Not sure', or a value from an older form: classify by title the way
        # the scraper would rather than defaulting everything to one bucket.
        category = infer_category(role, company.lower() in security_companies)
    clearance = 'yes' in fields.get('Security Clearance / U.S. Citizenship Required?',
                                    '').lower()
    return {
        'company': company,
        'role': role,
        'location': normalize_submitted_location(fields.get('Location', '')),
        'type': level,
        'category': category,
        'clearance': clearance,
        'url': fields.get('Direct Application Link', '').strip(),
        'source': 'Community',
        'date_added': today or datetime.now().strftime('%Y-%m-%d'),
    }


def submission_problem(fields, listing):
    """Why a parsed submission cannot become a row, or None when it can."""
    if not listing['url'] or not listing['company'] or not listing['role']:
        return 'the company, role or application link is missing.'
    # parse_issue_body joins a field's lines, and each of these renders on one
    # line of the board and the logs. A multi-line Location is split into
    # places before it gets here.
    if any(re.search(r'[\r\n]', listing[f]) for f in ('company', 'role', 'location')):
        return 'the company, role and location must each be one line.'
    if not re.match(r'^https?://', listing['url']) or re.search(r'\s', listing['url']):
        return 'the application link must be a single-line http(s) URL.'
    # Re-validated at ingestion: an edit after validate_issue.py ran could
    # carry a non-US or malformed location past that check.
    loc_errors = validate_location(fields.get('Location', ''))
    if loc_errors:
        return 'the location is not valid: ' + '; '.join(loc_errors)
    return None


def _describe(entry):
    # Posted in the duplicate comment, and a row's company and role may be
    # another submitter's text.
    state = 'open' if rebuild_readme.is_open(entry) else 'closed'
    return f"{md_escape(entry.get('company', ''))}: {md_escape(entry.get('role', ''))}, {state}"


def ingest(issues, listings, security_companies=frozenset(), today=None):
    """Append approvable submissions to `listings`; return one result per issue.

    A result is {'number', 'outcome', 'detail'} with outcome 'added',
    'revived', 'duplicate' or 'skipped'. Matches use normalized URL or
    listing_dedup_key, the two identities the scraper dedups on. Only an open
    match is a duplicate; a closed match is revived in place, as the scraper
    revives a closed row whose posting comes back.
    """
    open_url, open_key, closed_url, closed_key = {}, {}, {}, {}
    for e in listings:
        is_open = rebuild_readme.is_open(e)
        by_url, by_key = (open_url, open_key) if is_open else (closed_url, closed_key)
        if e.get('url'):
            by_url[normalize_url(e['url'])] = e
        by_key[listing_dedup_key(e.get('company', ''), e.get('role', ''),
                                 e.get('location', ''))] = e
    results = []
    for issue in issues:
        number = issue.get('number')
        try:
            fields = parse_issue_body(issue.get('body') or '')
            listing = fields_to_listing(fields, security_companies, today)
        except Exception as e:
            # One malformed issue must not abort the whole approved batch.
            results.append({'number': number, 'outcome': 'skipped',
                            'detail': f'the issue body could not be parsed '
                                      f'({md_escape(str(e))}).'})
            continue

        problem = submission_problem(fields, listing)
        if problem:
            results.append({'number': number, 'outcome': 'skipped', 'detail': problem})
            continue

        key = listing_dedup_key(listing['company'], listing['role'], listing['location'])
        url = normalize_url(listing['url'])
        match = open_url.get(url) or open_key.get(key)
        if match:
            results.append({'number': number, 'outcome': 'duplicate',
                            'detail': _describe(match)})
            continue

        row = closed_url.get(url) or closed_key.get(key)
        if row:
            _revive(row, listing['url'])
            for index, k in ((closed_url, url), (closed_key, key)):
                if index.get(k) is row:
                    del index[k]
            outcome = 'revived'
        else:
            row = listing
            listings.append(row)
            outcome = 'added'
        open_url[url] = row
        open_key[key] = row
        results.append({'number': number, 'outcome': outcome,
                        'detail': f"{row['company']}: {row['role']}"})
    return results


def _revive(row, url):
    # A submission can point a scraped row at a new domain; the log keeps the
    # switch visible to anyone auditing the run.
    old_host = link_host(row.get('url') or row.get('last_url')) or '(none)'
    print(f"  Reviving {oneline(row.get('company', ''))}: {old_host} -> {link_host(url)}")
    row['url'] = url
    # A maintainer vetted this link, and a scraped row whose board no longer
    # lists the req would be retired again on the next scrape by the vanished
    # or orphan pass. Community rows are exempt from both and go to the daily
    # link check instead.
    row['source'] = 'Community'
    for field in ('closed', 'closed_date', 'missing_since', 'dead_since'):
        row.pop(field, None)


def write_output(results):
    line = f'results={json.dumps(results)}\n'
    path = os.environ.get('GITHUB_OUTPUT')
    if path:
        with open(path, 'a') as f:
            f.write(line)
    else:
        print(line, end='')


def run_ingest(token, repo):
    issues = get_approved_issues(token, repo)
    print(f'Found {len(issues)} approved issue(s) to process')
    current, results = screen_edited(token, repo, issues)
    listings = json.loads(LISTINGS_FILE.read_text()) if LISTINGS_FILE.exists() else []
    held = len(listings)
    closed_before = [e for e in listings if not rebuild_readme.is_open(e)]
    results += ingest(current, listings, load_security_companies())
    for r in results:
        print(f"  Issue #{r['number']}: {r['outcome']}, {oneline(r['detail'])}")
    write_output(results)

    # ingest appends new rows and reopens closed ones in place. Written even
    # when empty: notify.py fails on a missing file, since that means a broken
    # handoff rather than a quiet run.
    added_rows = listings[held:]
    revived_rows = [e for e in closed_before if rebuild_readme.is_open(e)]
    events_file = os.environ.get('RUN_EVENTS_FILE')
    if events_file:
        write_run_events(events_file, added_rows, revived_rows)

    if added_rows or revived_rows:
        tmp = LISTINGS_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(listings, indent=2))
        tmp.replace(LISTINGS_FILE)
        rebuild_readme.main()
    print(f'\nAdded {len(added_rows)} listing(s), revived {len(revived_rows)}')


def _api(method, token, url, **kwargs):
    resp = requests.request(method, url, headers=gh_headers(token), timeout=10, **kwargs)
    # 404 on a label delete means the label is already off the issue.
    if resp.status_code >= 400 and not (method == 'DELETE' and resp.status_code == 404):
        print(f'  {method} {url} failed: {resp.status_code} {oneline(resp.text[:200])}')
        return False
    return True


def run_notify(token, repo, results, pushed):
    """Tell each submitter what happened; return the number of failed API calls."""
    failures = 0
    for r in results:
        number, outcome = r['number'], r['outcome']
        issue_url = f'{API}/repos/{repo}/issues/{number}'
        calls = []
        if outcome == 'held':
            print(f'  Issue #{number}: {oneline(r["detail"])}, '
                  'leaving it approved for the next run')
            continue
        if outcome in ROW_OUTCOMES and not pushed:
            # Left open and approved, so the next approved label event retries.
            print(f'  Issue #{number}: push did not land, leaving it open')
            continue
        if outcome in ROW_OUTCOMES:
            calls = [('POST', f'{issue_url}/comments',
                      {'json': {'body': ROW_OUTCOMES[outcome]}}),
                     ('PATCH', issue_url, {'json': {'state': 'closed'}})]
        elif outcome == 'duplicate':
            body = DUPLICATE_COMMENT.format(match=r['detail'])
            calls = [('POST', f'{issue_url}/comments', {'json': {'body': body}}),
                     ('PATCH', issue_url, {'json': {'state': 'closed'}})]
        elif outcome == 'skipped':
            body = SKIPPED_COMMENT.format(reason=r['detail'])
            calls = [('POST', f'{issue_url}/comments', {'json': {'body': body}}),
                     ('DELETE', f'{issue_url}/labels/approved', {})]
        for method, url, kwargs in calls:
            if not _api(method, token, url, **kwargs):
                failures += 1
            time.sleep(0.5)
        print(f'  Issue #{number}: {outcome}, notified')
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--notify', action='store_true',
                        help='comment on and close issues from $RESULTS after the push')
    args = parser.parse_args()

    token = os.environ.get('GITHUB_TOKEN')
    repo = os.environ.get('GITHUB_REPOSITORY')
    if not token or not repo:
        print('GITHUB_TOKEN or GITHUB_REPOSITORY not set, skipping')
        sys.exit(0)

    if not args.notify:
        run_ingest(token, repo)
        return

    results = json.loads(os.environ.get('RESULTS') or '[]')
    pushed = os.environ.get('PUSHED', '').lower() == 'true'
    if run_notify(token, repo, results, pushed):
        sys.exit(1)


if __name__ == '__main__':
    main()
