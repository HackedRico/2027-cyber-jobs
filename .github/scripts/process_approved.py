#!/usr/bin/env python3
"""Add community listings from issues labeled 'approved' to listings.json.

Runs twice per add-listing workflow. The default mode adds rows, rebuilds the
README, writes each issue's outcome to $GITHUB_OUTPUT and the added rows to
$RUN_EVENTS_FILE for notify.py's alerts. `--notify` runs after
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
    normalize_submitted_location,
    normalize_url,
    parse_issue_body,
    security_company_names,
    validate_location,
    write_run_events,
)

LISTINGS_FILE = Path('listings.json')
COMPANIES_FILE = Path('companies.yml')
API = 'https://api.github.com'

ADDED_COMMENT = '✅ Listing added to the board. Thanks for contributing!'
DUPLICATE_COMMENT = 'This role is already on the board ({match}), so closing. Thanks!'
SKIPPED_COMMENT = (
    'This submission could not be added: {reason}\n\n'
    'Edit the issue to fix it. A maintainer will add the `approved` label again.'
)


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


def load_security_companies(path=COMPANIES_FILE):
    """Lowercased names of the `security_company: true` employers, or empty."""
    try:
        with open(path) as f:
            return security_company_names(yaml.safe_load(f))
    except (OSError, yaml.YAMLError) as e:
        print(f'Could not read {path} ({e}); treating no company as a security company')
        return set()


def fields_to_listing(fields, security_companies=frozenset(), today=None):
    """Build a listings.json row from parsed issue-form fields."""
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
    if not re.match(r'^https?://', listing['url']) or re.search(r'\s', listing['url']):
        return 'the application link must be a single-line http(s) URL.'
    # Re-validated at ingestion: an edit after validate_issue.py ran could
    # carry a non-US or malformed location past that check.
    loc_errors = validate_location(fields.get('Location', ''))
    if loc_errors:
        return 'the location is not valid: ' + '; '.join(loc_errors)
    return None


def _describe(entry):
    state = 'closed' if entry.get('closed') or not entry.get('url') else 'open'
    return f"{entry.get('company', '')}: {entry.get('role', '')}, {state}"


def ingest(issues, listings, security_companies=frozenset(), today=None):
    """Append approvable submissions to `listings`; return one result per issue.

    A result is {'number', 'outcome', 'detail'} with outcome 'added',
    'duplicate' or 'skipped'. Duplicates match on normalized URL or on
    listing_dedup_key, the two identities the scraper dedups on.
    """
    by_url = {normalize_url(e['url']): e for e in listings if e.get('url')}
    by_key = {listing_dedup_key(e.get('company', ''), e.get('role', ''),
                                e.get('location', '')): e for e in listings}
    results = []
    for issue in issues:
        number = issue.get('number')
        try:
            fields = parse_issue_body(issue.get('body') or '')
            listing = fields_to_listing(fields, security_companies, today)
        except Exception as e:
            # One malformed issue must not abort the whole approved batch.
            results.append({'number': number, 'outcome': 'skipped',
                            'detail': f'the issue body could not be parsed ({e}).'})
            continue

        problem = submission_problem(fields, listing)
        if problem:
            results.append({'number': number, 'outcome': 'skipped', 'detail': problem})
            continue

        key = listing_dedup_key(listing['company'], listing['role'], listing['location'])
        match = by_url.get(normalize_url(listing['url'])) or by_key.get(key)
        if match:
            results.append({'number': number, 'outcome': 'duplicate',
                            'detail': _describe(match)})
            continue

        listings.append(listing)
        by_url[normalize_url(listing['url'])] = listing
        by_key[key] = listing
        results.append({'number': number, 'outcome': 'added',
                        'detail': f"{listing['company']}: {listing['role']}"})
    return results


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
    listings = json.loads(LISTINGS_FILE.read_text()) if LISTINGS_FILE.exists() else []
    held = len(listings)
    results = ingest(issues, listings, load_security_companies())
    for r in results:
        print(f"  Issue #{r['number']}: {r['outcome']}, {r['detail']}")
    write_output(results)

    # ingest only appends, so the tail is this run's rows. Written even when
    # empty: notify.py fails on a missing file, since that means a broken
    # handoff rather than a quiet run.
    added_rows = listings[held:]
    events_file = os.environ.get('RUN_EVENTS_FILE')
    if events_file:
        write_run_events(events_file, added_rows)

    if added_rows:
        tmp = LISTINGS_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(listings, indent=2))
        tmp.replace(LISTINGS_FILE)
        rebuild_readme.main()
    print(f'\nAdded {len(added_rows)} listing(s)')


def _api(method, token, url, **kwargs):
    resp = requests.request(method, url, headers=gh_headers(token), timeout=10, **kwargs)
    # 404 on a label delete means the label is already off the issue.
    if resp.status_code >= 400 and not (method == 'DELETE' and resp.status_code == 404):
        print(f'  {method} {url} failed: {resp.status_code} {resp.text[:200]}')
        return False
    return True


def run_notify(token, repo, results, pushed):
    """Tell each submitter what happened; return the number of failed API calls."""
    failures = 0
    for r in results:
        number, outcome = r['number'], r['outcome']
        issue_url = f'{API}/repos/{repo}/issues/{number}'
        calls = []
        if outcome == 'added' and not pushed:
            # Left open and approved, so the next approved label event retries.
            print(f'  Issue #{number}: push did not land, leaving it open')
            continue
        if outcome == 'added':
            calls = [('POST', f'{issue_url}/comments', {'json': {'body': ADDED_COMMENT}}),
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
