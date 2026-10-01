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
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import rebuild_readme
import requests
from common import fetch_target_problem, host_matches, oneline
from scrape_jobs import job_fingerprint
from urllib3.exceptions import HTTPError as Urllib3Error
from validate_issue import BlockedAddress, public_session

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

# A 200 that only reads as missing: a not-found or empty <title>, or a redirect
# that dropped the req id. It is weaker evidence than a real 404, since a
# single-page app can serve an empty title while it loads, so it has to hold
# for SOFT_DEAD_DAYS distinct days instead of two.
SOFT_404 = 'soft-404'
SOFT_DEAD_DAYS = 3

# A Community row no scraper watches can sit open on a live-looking page long
# after the cohort filled. Four months covers a fall recruiting season.
COMMUNITY_MAX_AGE_DAYS = 120

REQ_TOKEN_RE = re.compile(r'\d{4,}')

REQUEST_DELAY = 0.75
REQUEST_TIMEOUT = 12
# requests allowed 30; career sites chain a few for SSO and locale.
MAX_REDIRECTS = 10
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# A host that is not public, or a port other than 80 and 443, on any hop. A
# public careers site never sends a student there, so it counts as dead.
BLOCKED = 'blocked'

# A Community link points anywhere, and a page that streamed an unbounded
# body stalled the whole check: 2 MB of unclosed <title tags ran past the
# 30-minute job timeout. A <title> sits in the first few KB of a real page.
MAX_BODY_BYTES = 256 * 1024
BODY_SECONDS = 20

# Some career sites answer 200 for a job id that does not exist: Bank of
# America serves "404 Page not found" as the page title, and HII's
# jobs.hii-tsd.com an empty <title>. A live page names the role in its title.
SOFT_404_TITLE_RE = re.compile(
    r'\b404\b|not found|no longer available|job has expired|position has been filled',
    re.IGNORECASE)
# Every repeat is bounded, so a run of unclosed "<title" tags costs linear
# time; the lazy DOTALL form was quadratic on them.
TITLE_RE = re.compile(r'<title\b[^<>]{0,256}>([^<]{0,512})</title', re.IGNORECASE)

_session = None


def should_skip(url):
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    return host_matches(host, SKIP_DOMAINS)


def is_check_target(entry):
    """True for an open row that only the link check can close."""
    if not rebuild_readme.is_open(entry):
        return False
    url = entry['url'].strip()
    if entry.get('source') == 'Community' or 'amazon.jobs' in url:
        return True
    return job_fingerprint(entry.get('company', ''), entry.get('source', ''), url) is None


def _is_html_200(resp):
    return resp.status_code == 200 and 'html' in resp.headers.get('Content-Type', '').lower()


def read_body(resp, limit=MAX_BODY_BYTES, seconds=BODY_SECONDS):
    """Up to `limit` decoded bytes of a streamed response, within `seconds`.

    read1 returns whatever one socket read brings, so a server that drips a
    byte at a time still meets the deadline.
    """
    started = time.monotonic()
    read = getattr(resp.raw, 'read1', None) or resp.raw.read
    chunks, size = [], 0
    while size < limit and time.monotonic() - started < seconds:
        chunk = read(min(16384, limit - size), decode_content=True)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b''.join(chunks)[:limit]


def _decode(resp, body):
    try:
        return body.decode(resp.encoding or 'utf-8', errors='replace')
    except LookupError:
        return body.decode('utf-8', errors='replace')


def is_soft_404(resp, text):
    """True for a 200 HTML page whose <title> says not found, or is empty.

    `text` is the start of the page. A page with no <title> tag at all says
    nothing, so it is not counted.
    """
    if not _is_html_200(resp):
        return False
    match = TITLE_RE.search(text)
    if match is None:
        return False
    title = ' '.join(html.unescape(match.group(1)).split())
    return not title or bool(SOFT_404_TITLE_RE.search(title))


def lost_req_on_redirect(url, final_url):
    """True when a redirect landed on a page without the posting's req id.

    Greenhouse sends a closed req to the board root with ?error=true, and many
    career sites send one to their search page, both answering 200. A URL with
    no id of four or more digits says nothing either way.
    """
    if 'error=true' in urlsplit(final_url).query.lower():
        return True
    parts = urlsplit(url)
    tokens = REQ_TOKEN_RE.findall(parts.path + '?' + parts.query)
    return bool(tokens) and tokens[-1] not in final_url


def _get_session():
    global _session
    if _session is None:
        _session = public_session()
    return _session


def fetch_status(url, soft_404=False, session=None):
    """HTTP status for url, or None when the request itself failed.

    Redirects are followed by hand, and every hop must pass the shared
    public-address and port checks; one that fails returns BLOCKED. With
    `soft_404`, a 200 page that `is_soft_404` reads as missing, or that a
    redirect reached without the req id, returns SOFT_404.
    """
    session = session or _get_session()
    start = url
    for _ in range(MAX_REDIRECTS + 1):
        problem = fetch_target_problem(url)
        if problem:
            print(f'  Blocked ({problem}): {oneline(url)}')
            return BLOCKED
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False,
                               stream=True, headers=HEADERS)
        except BlockedAddress as e:
            print(f'  Blocked ({oneline(e)}): {oneline(start)}')
            return BLOCKED
        except requests.RequestException as e:
            print(f'  Request error: {oneline(e)}')
            return None
        with resp:
            location = resp.headers.get('Location')
            if resp.status_code in REDIRECT_STATUSES and location:
                url = urljoin(url, location)
                continue
            text = ''
            if soft_404 and _is_html_200(resp):
                try:
                    text = _decode(resp, read_body(resp))
                except (OSError, Urllib3Error) as e:
                    # resp.raw raises urllib3's errors, not requests' wrappers.
                    print(f'  Body read error ({type(e).__name__}): {oneline(start)}')
        if soft_404 and is_soft_404(resp, text):
            print(f'  Soft 404 (page title reads as missing): {oneline(start)}')
            return SOFT_404
        if (soft_404 and resp.status_code == 200 and url != start
                and lost_req_on_redirect(start, url)):
            print(f'  Soft 404 (redirected to {oneline(url)}): {oneline(start)}')
            return SOFT_404
        return resp.status_code
    print(f'  More than {MAX_REDIRECTS} redirects: {oneline(start)}')
    return None


def record_result(entry, status, today):
    """Update one row's dead streak; return True when this result closes it.

    A dead status must repeat on a later day before the row closes, so one bad
    deploy on an employer's careers site does not padlock a live posting. A
    SOFT_404 must hold across SOFT_DEAD_DAYS distinct days, and BLOCKED
    counts as a dead status. Any other answer, a network error included,
    resets the streak.
    """
    if status not in DEAD_STATUSES and status not in (SOFT_404, BLOCKED):
        entry.pop('dead_since', None)
        return False
    first_dead = entry.get('dead_since')
    if not first_dead or first_dead >= today:
        entry['dead_since'] = first_dead or today
        return False
    if status == SOFT_404 and _days_between(first_dead, today) < SOFT_DEAD_DAYS - 1:
        return False
    _close(entry, today)
    return True


def is_aged_out(entry, today):
    """True for an open Community row older than COMMUNITY_MAX_AGE_DAYS."""
    if entry.get('source') != 'Community' or not entry.get('date_added'):
        return False
    return _days_between(entry['date_added'], today) >= COMMUNITY_MAX_AGE_DAYS


def _days_between(start, end):
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return 0


def _close(entry, today):
    # Blanking the url renders 🔒 and lets the scraper's revive path match the
    # row by title; last_url keeps the link for anyone auditing the closure.
    entry['last_url'] = entry['url']
    entry['url'] = ''
    entry['closed'] = True
    entry.setdefault('closed_date', today)
    entry.pop('dead_since', None)
    entry.pop('missing_since', None)


def main():
    listings = json.loads(LISTINGS_FILE.read_text())
    before = json.dumps(listings, sort_keys=True)
    today = datetime.now().strftime('%Y-%m-%d')

    targets = [e for e in listings if is_check_target(e)]
    print(f'Checking {len(targets)} of {len(listings)} listings')

    closed = 0
    for entry in targets:
        url = entry['url'].strip()
        if is_aged_out(entry, today):
            _close(entry, today)
            closed += 1
            print(f'  AGED OUT (added {oneline(entry["date_added"])}): {oneline(url)}')
            continue
        if should_skip(url):
            print(f'  SKIP (bot-blocked domain): {oneline(url)}')
            continue
        # Only Community links point at arbitrary career sites; amazon.jobs
        # answers a real 404 for a closed req.
        status = fetch_status(url, soft_404=entry.get('source') == 'Community')
        if record_result(entry, status, today):
            closed += 1
            print(f'  CLOSED {status}: {oneline(url)}')
        elif entry.get('dead_since'):
            print(f'  DEAD {status} since {oneline(entry["dead_since"])}: {oneline(url)}')
        else:
            print(f'  OK   {status}: {oneline(url)}')
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
