#!/usr/bin/env python3
"""
Add community-submitted listings from issues labeled 'approved' to
listings.json, rebuild the README, and close the issues.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests

LISTINGS_FILE = Path('listings.json')

STRIP_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term',
    'utm_id', 'source', 'src', 'ref', 'referer', 'lever-source',
    'lever-origin', 'gh_src',
}

CATEGORIES = {
    'Offensive Security', 'SOC & Detection', 'Threat Intelligence',
    'Forensics & IR', 'AppSec & ProdSec', 'Cloud & Infra Security',
    'Identity & IAM', 'GRC & Risk', 'Security Engineering',
    'Engineering @ Security Co',
}


def normalize_url(url):
    try:
        p = urlparse(url.strip())
        params = {k: v for k, v in parse_qs(p.query, keep_blank_values=True).items()
                  if k.lower() not in STRIP_PARAMS}
        u = urlunparse(p._replace(
            scheme=p.scheme.lower(),
            netloc=p.netloc.lower(),
            path=p.path.rstrip('/'),
            query=urlencode(sorted(params.items()), doseq=True),
            fragment='',
        ))
        return re.sub(r'(myworkdayjobs\.com)/en-[A-Z]{2}/([^/]+/)?job/', r'\1/job/', u)
    except Exception:
        return url


def gh_headers(token):
    return {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json',
    }


def get_approved_issues(token, repo):
    issues = []
    page = 1
    while True:
        resp = requests.get(
            f'https://api.github.com/repos/{repo}/issues',
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


def comment_and_close(token, repo, issue_number, body):
    requests.post(
        f'https://api.github.com/repos/{repo}/issues/{issue_number}/comments',
        headers=gh_headers(token), json={'body': body}, timeout=10,
    )
    requests.patch(
        f'https://api.github.com/repos/{repo}/issues/{issue_number}',
        headers=gh_headers(token), json={'state': 'closed'}, timeout=10,
    )


def parse_issue_body(body):
    fields = {}
    for section in re.split(r'^### ', body, flags=re.MULTILINE):
        if not section.strip():
            continue
        lines = section.strip().split('\n')
        value = '\n'.join(lines[1:]).strip()
        fields[lines[0].strip()] = '' if value == '_No response_' else value
    return fields


def fields_to_listing(fields):
    listing_type = fields.get('Listing Type', '')
    level = 'newgrad' if 'New Grad' in listing_type else 'earlycareer'
    category = fields.get('Category', '').strip()
    if category not in CATEGORIES:
        category = 'Security Engineering'
    clearance = 'yes' in fields.get('Security Clearance / U.S. Citizenship Required?', '').lower()
    location = '; '.join(
        p.strip() for p in re.split(r'[;\n]', fields.get('Location', '')) if p.strip()
    )
    return {
        'company': fields.get('Company Name', '').strip(),
        'role': fields.get('Role / Job Title', '').strip(),
        'location': location,
        'type': level,
        'category': category,
        'clearance': clearance,
        'url': fields.get('Direct Application Link', '').strip(),
        'source': 'Community',
        'date_added': datetime.now().strftime('%Y-%m-%d'),
    }


def main():
    token = os.environ.get('GITHUB_TOKEN')
    repo = os.environ.get('GITHUB_REPOSITORY')
    if not token or not repo:
        print('GITHUB_TOKEN or GITHUB_REPOSITORY not set — skipping')
        sys.exit(0)

    issues = get_approved_issues(token, repo)
    print(f'Found {len(issues)} approved issue(s) to process')
    if not issues:
        return

    listings = json.loads(LISTINGS_FILE.read_text()) if LISTINGS_FILE.exists() else []
    seen_urls = {normalize_url(e.get('url', '')) for e in listings}
    added = 0

    for issue in issues:
        number = issue.get('number')
        fields = parse_issue_body(issue.get('body', ''))
        listing = fields_to_listing(fields)

        if not listing['url'] or not listing['company'] or not listing['role']:
            print(f'  Issue #{number}: missing required fields, skipping')
            continue

        if normalize_url(listing['url']) in seen_urls:
            print(f'  Issue #{number}: already listed, closing')
            comment_and_close(token, repo, number,
                              'This listing is already in the repo — closing. Thanks!')
            time.sleep(0.5)
            continue

        listings.append(listing)
        seen_urls.add(normalize_url(listing['url']))
        comment_and_close(token, repo, number,
                          '✅ Listing added to the repo! Thanks for contributing.')
        print(f'  Issue #{number}: added "{listing["role"]}" at {listing["company"]}')
        added += 1
        time.sleep(0.5)

    if added:
        tmp = LISTINGS_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(listings, indent=2))
        tmp.replace(LISTINGS_FILE)
        result = subprocess.run(
            [sys.executable, '.github/scripts/rebuild_readme.py'],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f'rebuild_readme.py failed:\n{result.stderr}')
            sys.exit(1)
        print(result.stdout.strip())
        print(f'\nAdded {added} listing(s)')
    else:
        print('\nNo new listings to add')


if __name__ == '__main__':
    main()
