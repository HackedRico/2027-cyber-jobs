#!/usr/bin/env python3
"""Close open listings whose apply link is gone, then rebuild the README.

Reads open rows from listings.json and checks only the ones nothing else can
close. A scraped row whose URL carries a requisition id is retired by the
scraper within VANISHED_DAYS once it leaves its board, so checking it here only
adds requests. What remains is Community rows, rows with no fingerprint, and
amazon.jobs rows, which this check closes the same day the posting goes.
"""

import html
import json
import re
import time
from datetime import datetime
from pathlib import Path

import rebuild_readme
import requests
from scrape_jobs import job_fingerprint

LISTINGS_FILE = Path('listings.json')

# Domains that block bots with 403/404 even for live jobs.
SKIP_DOMAINS = [
    'careers.ibm.com',
    'lockheedmartinjobs.com',
    'usajobs.gov',
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/126.0.0.0 Safari/537.36'
    )
}

# Only "not found" and "gone" mean the posting is dead. Across 9 runs the old
# any-4xx rule found 7 dead links in 1,978; a 403 from a bot wall is not one.
DEAD_STATUSES = frozenset({404, 410})

REQUEST_DELAY = 0.75

# Some career sites answer 200 for a job id that does not exist: Bank of
# America serves "404 Page not found" as the page title, and HII's
# jobs.hii-tsd.com an empty <title>. A live page names the role in its title.
SOFT_404_TITLE_RE = re.compile(
    r'\b404\b|not found|no longer available|job has expired|position has been filled',
    re.IGNORECASE)
TITLE_RE = re.compile(r'<title\b[^>]*>(.*?)</title\s*>', re.IGNORECASE | re.DOTALL)


def should_skip(url):
    return any(domain in url for domain in SKIP_DOMAINS)


def is_check_target(entry):
    """True for an open row that only the link check can close."""
    if not rebuild_readme.is_open(entry):
        return False
    url = entry['url'].strip()
    if entry.get('source') == 'Community' or 'amazon.jobs' in url:
        return True
    return job_fingerprint(entry.get('company', ''), entry.get('source', ''), url) is None


def is_soft_404(resp):
    """True for a 200 HTML page whose <title> says not found, or is empty.

    A page with no <title> tag at all says nothing, so it is not counted.
    """
    if resp.status_code != 200 or 'html' not in resp.headers.get('Content-Type', '').lower():
        return False
    match = TITLE_RE.search(resp.text)
    if match is None:
        return False
    title = ' '.join(html.unescape(match.group(1)).split())
    return not title or bool(SOFT_404_TITLE_RE.search(title))


def fetch_status(url, soft_404=False):
    """HTTP status for url, or None when the request itself failed.

    With `soft_404`, a 200 page that `is_soft_404` reads as missing counts as 404.
    """
    try:
        resp = requests.get(url, timeout=12, allow_redirects=True, headers=HEADERS)
    except requests.RequestException as e:
        print(f'  Request error: {e}')
        return None
    if soft_404 and is_soft_404(resp):
        print(f'  Soft 404 (page title reads as missing): {url}')
        return 404
    return resp.status_code


def record_result(entry, status, today):
    """Update one row's dead streak; return True when this result closes it.

    A dead status must repeat on a later day before the row closes, so one bad
    deploy on an employer's careers site does not padlock a live posting. Any
    other answer, a network error included, resets the streak.
    """
    if status not in DEAD_STATUSES:
        entry.pop('dead_since', None)
        return False
    first_dead = entry.get('dead_since')
    if not first_dead or first_dead >= today:
        entry['dead_since'] = first_dead or today
        return False
    # Blanking the url renders 🔒 and lets the scraper's revive path match the
    # row by title; last_url keeps the link for anyone auditing the closure.
    entry['last_url'] = entry['url']
    entry['url'] = ''
    entry['closed'] = True
    entry.setdefault('closed_date', today)
    entry.pop('dead_since', None)
    entry.pop('missing_since', None)
    return True


def main():
    listings = json.loads(LISTINGS_FILE.read_text())
    before = json.dumps(listings, sort_keys=True)
    today = datetime.now().strftime('%Y-%m-%d')

    targets = [e for e in listings if is_check_target(e)]
    print(f'Checking {len(targets)} of {len(listings)} listings')

    closed = 0
    for entry in targets:
        url = entry['url'].strip()
        if should_skip(url):
            print(f'  SKIP (bot-blocked domain): {url}')
            continue
        # Only Community links point at arbitrary career sites; amazon.jobs
        # answers a real 404 for a closed req.
        status = fetch_status(url, soft_404=entry.get('source') == 'Community')
        if record_result(entry, status, today):
            closed += 1
            print(f'  CLOSED {status}: {url}')
        elif entry.get('dead_since'):
            print(f'  DEAD {status} since {entry["dead_since"]}: {url}')
        else:
            print(f'  OK   {status}: {url}')
        time.sleep(REQUEST_DELAY)

    if json.dumps(listings, sort_keys=True) == before:
        print('\nNo listing changed')
        return

    tmp = LISTINGS_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(listings, indent=2))
    tmp.replace(LISTINGS_FILE)
    rebuild_readme.main()
    print(f'\nClosed {closed} listing(s)')


if __name__ == '__main__':
    main()
