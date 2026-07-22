#!/usr/bin/env python3
"""Scrape ATS job-board APIs for US new-grad, early-career, and internship
cybersecurity roles.

Classification, location filtering, and URL normalization live in the
dependency-free `classify` and `common` modules so the scraper, the
community-submission scripts, and the test suite share one source of truth.
This file owns the ATS scrapers, persistence, and orchestration.
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import rebuild_readme
import requests
import yaml
from classify import (
    AI_CATEGORY_RE,
    classify_level,
    evaluate_job,
    is_cyber_title,
    is_rejected_title,
    listing_dedup_key,
    normalize_location,
    prune_seen,
    purge_stale_listings,
    reclassify_listings,
    requires_clearance,
)
from common import normalize_url

LISTINGS_FILE = Path('listings.json')
SEEN_JOBS_FILE = Path('.github/data/seen_jobs.json')
BOARD_BASELINE_FILE = Path('.github/data/board_baseline.json')

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; cyber-jobs-scraper/1.0)'}

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
BACKOFF_BASE = 1.6
MAX_BACKOFF = 30
# Hard caps so a bad `total` or a page that echoes forever can't loop until the
# workflow's 15-minute timeout.
MAX_PAGES = 60

# One connection-pooled session for every request.
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Config values interpolated into a request HOST must not contain characters
# ('/', '@', '?', '#', ':') that could reparent the host — defense-in-depth on
# a malicious companies.yml entry.
_SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_HOST_RE = re.compile(r'^[A-Za-z0-9.-]+$')


def _valid_slug(value):
    return bool(value) and bool(_SLUG_RE.fullmatch(value))


def _valid_host(value):
    return bool(value) and bool(_HOST_RE.fullmatch(value)) and not value.startswith('.')


def _sleep_backoff(attempt, retry_after=None):
    if retry_after and str(retry_after).strip().isdigit():
        delay = float(retry_after)
    else:
        delay = BACKOFF_BASE ** attempt + random.uniform(0, 0.5)
    time.sleep(min(delay, MAX_BACKOFF))


def fetch_json(url, *, method='GET', label='', **kwargs):
    """HTTP request returning parsed JSON, or None on unrecoverable failure.

    Retries transient failures — timeouts, connection resets, 429/503 (honoring
    Retry-After), and 200s with a non-JSON body — with exponential backoff plus
    jitter, over one pooled Session. Returning None (not []) lets callers tell a
    broken fetch apart from a genuinely empty board.
    """
    kwargs.setdefault('timeout', REQUEST_TIMEOUT)
    for attempt in range(MAX_RETRIES):
        last = attempt + 1 == MAX_RETRIES
        try:
            resp = SESSION.request(method, url, **kwargs)
        except requests.RequestException as e:
            if last:
                print(f'  [{label}] request error: {e}')
                return None
            _sleep_backoff(attempt)
            continue
        if resp.status_code in (429, 503):
            if last:
                print(f'  [{label}] HTTP {resp.status_code} (rate limited)')
                return None
            _sleep_backoff(attempt, resp.headers.get('Retry-After'))
            continue
        if resp.status_code != 200:
            print(f'  [{label}] HTTP {resp.status_code}')
            return None
        try:
            return resp.json()
        except ValueError:
            if last:
                print(f'  [{label}] non-JSON 200 response')
                return None
            _sleep_backoff(attempt)
    return None


def _oneline(text):
    # Collapse newlines so a scraped title can't inject a ::workflow-command::
    # at the start of a log line that GitHub Actions parses.
    return ' '.join(str(text).split())


def check_container(data, key, label):
    """Warn (as a GitHub annotation) when an expected top-level key is missing.

    A 200 whose container key vanished is schema drift, indistinguishable from
    an empty board unless surfaced.
    """
    if isinstance(data, dict) and key not in data:
        print(f'::warning::[{label}] response missing expected key {key!r} '
              f'(schema drift?); got keys {sorted(data)[:8]}')


# ---------------------------------------------------------------------------
# ATS scrapers — each yields dicts with:
#   id, company, title, location, url, board, description (optional)
# Scrapers return None on an unrecoverable fetch failure and a (possibly empty)
# list otherwise, so the run summary can tell breakage from an empty board.
# ---------------------------------------------------------------------------

# Some boards put the workplace type where the location belongs; the real
# location is then in `offices` / "Job Posting Location" metadata.
WORKPLACE_LABELS = {'in-office', 'hybrid', 'distributed', 'remote', 'onsite',
                    'on-site', 'flexible', ''}


def greenhouse_location(job):
    loc = (job.get('location') or {}).get('name', '') or ''
    if loc.strip().lower() not in WORKPLACE_LABELS:
        return loc
    parts = [o.get('name') for o in job.get('offices') or [] if o.get('name')]
    for m in job.get('metadata') or []:
        if isinstance(m, dict) and 'location' in (m.get('name') or '').lower():
            v = m.get('value')
            if isinstance(v, list):
                parts.extend(str(x) for x in v)
            elif v:
                parts.append(str(v))
    return '; '.join(dict.fromkeys(parts)) if parts else loc


def scrape_greenhouse(company, slug):
    url = f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true'
    data = fetch_json(url, label=f'{company} Greenhouse')
    if data is None:
        return None
    check_container(data, 'jobs', f'{company} Greenhouse')
    jobs = []
    for job in data.get('jobs', []):
        jobs.append({
            'id': f'greenhouse_{slug}_{job["id"]}',
            'company': company,
            'title': job.get('title', ''),
            'location': greenhouse_location(job),
            'url': job.get('absolute_url', ''),
            'board': 'Greenhouse',
            'description': job.get('content', ''),
        })
    return jobs


def scrape_lever(company, slug):
    url = f'https://api.lever.co/v0/postings/{slug}?mode=json'
    data = fetch_json(url, label=f'{company} Lever')
    if data is None:
        return None
    jobs = []
    for job in data:
        country = job.get('country', '')
        if country and country.upper() != 'US':
            continue
        # Lever's commitment category ("Internship", "Intern") flags intern
        # reqs whose titles omit the word.
        cats = job.get('categories') or {}
        commitment = (cats.get('commitment') or '').lower()
        jobs.append({
            'id': f'lever_{slug}_{job["id"]}',
            'company': company,
            'title': job.get('text', ''),
            'location': cats.get('location', ''),
            'url': job.get('hostedUrl', ''),
            'board': 'Lever',
            'description': job.get('descriptionPlain', ''),
            'intern_hint': 'intern' in commitment,
        })
    return jobs


def scrape_ashby(company, slug):
    url = f'https://api.ashbyhq.com/posting-api/job-board/{slug}'
    data = fetch_json(url, label=f'{company} Ashby')
    if data is None:
        return None
    if isinstance(data, dict) and 'jobs' not in data and 'jobPostings' not in data:
        check_container(data, 'jobs', f'{company} Ashby')
    jobs = []
    for job in data.get('jobs') or data.get('jobPostings') or []:
        if job.get('isListed') is False:
            continue
        locations = [job.get('location', '') or job.get('locationName', '')]
        locations += [s.get('location', '') for s in job.get('secondaryLocations') or []]
        location = '; '.join(dict.fromkeys(x for x in locations if x))
        apply_url = (
            job.get('jobUrl', '')
            or job.get('applyUrl', '')
            or f'https://jobs.ashbyhq.com/{slug}/{job.get("id", "")}'
        )
        jobs.append({
            'id': f'ashby_{slug}_{job["id"]}',
            'company': company,
            'title': job.get('title', ''),
            'location': location,
            'url': apply_url,
            'board': 'Ashby',
            'description': job.get('descriptionPlain', ''),
            'intern_hint': job.get('employmentType', '') == 'Intern',
        })
    return jobs


def scrape_smartrecruiters(company, identifier):
    url = f'https://api.smartrecruiters.com/v1/companies/{identifier}/postings'
    limit = 100
    params = {'limit': limit, 'offset': 0}
    jobs = []
    for _page in range(MAX_PAGES):
        data = fetch_json(url, params=params, label=f'{company} SmartRecruiters')
        if data is None:
            return jobs if jobs else None
        content = data.get('content', [])
        if not content:
            break
        for job in content:
            loc = job.get('location', {})
            if loc.get('country', '').lower() != 'us' and not loc.get('remote'):
                continue
            city = loc.get('city', '')
            region = loc.get('region', '')
            if loc.get('remote'):
                location = 'Remote (US)'
            elif city and region:
                location = f'{city}, {region}'
            else:
                location = city or 'United States'
            job_id = job.get('id', '')
            jobs.append({
                'id': f'smartrecruiters_{identifier}_{job_id}',
                'company': company,
                'title': job.get('name', ''),
                'location': location,
                'url': f'https://jobs.smartrecruiters.com/{identifier}/{job_id}',
                'board': 'SmartRecruiters',
            })
        total = data.get('totalFound')
        params['offset'] += len(content)
        # Stop on a short page (end of list) or once we've fetched `total`. A
        # missing `total` is NOT treated as 0, so a full first page keeps
        # paging instead of silently truncating.
        if len(content) < limit or (total is not None and params['offset'] >= total):
            break
        time.sleep(0.3)
    return jobs


def scrape_workable(company, slug):
    url = f'https://apply.workable.com/api/v1/widget/accounts/{slug}'
    data = fetch_json(url, label=f'{company} Workable')
    if data is None:
        return None
    check_container(data, 'jobs', f'{company} Workable')
    jobs = []
    for job in data.get('jobs', []):
        loc = job.get('location', {})
        country = loc.get('countryCode', '').upper()
        if country and country != 'US' and not loc.get('remote'):
            continue
        city = loc.get('city', '')
        region = loc.get('region', '')
        if loc.get('remote'):
            location = 'Remote (US)' if country in ('US', '') else ''
        elif city and region:
            location = f'{city}, {region}'
        else:
            location = city
        job_id = job.get('shortcode', job.get('id', ''))
        jobs.append({
            'id': f'workable_{slug}_{job_id}',
            'company': company,
            'title': job.get('title', ''),
            'location': location,
            'url': f'https://apply.workable.com/{slug}/j/{job_id}/',
            'board': 'Workable',
        })
    return jobs


def scrape_recruitee(company, slug):
    if not _valid_slug(slug):
        print(f'  [{company}] invalid recruitee slug {slug!r} — skipping')
        return None
    url = f'https://{slug}.recruitee.com/api/offers/'
    data = fetch_json(url, label=f'{company} Recruitee')
    if data is None:
        return None
    check_container(data, 'offers', f'{company} Recruitee')
    jobs = []
    for job in data.get('offers', []):
        country = (job.get('country') or '').lower()
        remote = job.get('remote', False)
        if country not in ('us', 'united states') and not remote:
            continue
        city = job.get('city') or ''
        region = job.get('province') or ''
        if remote:
            location = 'Remote (US)'
        elif city and region:
            location = f'{city}, {region}'
        else:
            location = city
        job_id = str(job.get('id', ''))
        jobs.append({
            'id': f'recruitee_{slug}_{job_id}',
            'company': company,
            'title': job.get('title', ''),
            'location': location,
            'url': job.get('careers_url',
                           f'https://{slug}.recruitee.com/o/{job.get("slug", job_id)}'),
            'board': 'Recruitee',
        })
    return jobs


def scrape_pinpoint(company, slug):
    if not _valid_slug(slug):
        print(f'  [{company}] invalid pinpoint slug {slug!r} — skipping')
        return None
    url = f'https://{slug}.pinpointhq.com/postings.json'
    data = fetch_json(url, label=f'{company} Pinpoint')
    if data is None:
        return None
    check_container(data, 'data', f'{company} Pinpoint')
    jobs = []
    for job in data.get('data', []):
        loc = job.get('location') or {}
        if job.get('workplace_type') == 'remote':
            location = 'Remote'
        else:
            location = ', '.join(p for p in (loc.get('city'), loc.get('province')) if p)
        job_id = str(job.get('id', ''))
        jobs.append({
            'id': f'pinpoint_{slug}_{job_id}',
            'company': company,
            'title': job.get('title', ''),
            'location': location,
            'url': job.get('url', f'https://{slug}.pinpointhq.com/postings/{job_id}'),
            'board': 'Pinpoint',
            'description': job.get('description', ''),
        })
    return jobs


MULTI_LOCATION_RE = re.compile(r'^\d+ locations$', re.IGNORECASE)


def fetch_workday_detail(cxs_root, path, wd_headers, label=''):
    """Fetch a posting's real locations and description (list view hides both)."""
    data = fetch_json(f'{cxs_root}{path}', headers=wd_headers, label=label)
    if data is None:
        return None, ''
    info = data.get('jobPostingInfo', {})
    locations = [info.get('location', '')]
    locations += info.get('additionalLocations', []) or []
    location = '; '.join(dict.fromkeys(x for x in locations if x))
    return location, info.get('jobDescription', '')


def scrape_workday(company, tenant, instance, board, security_company=False,
                   extra_terms=None):
    if not _valid_slug(tenant) or not _valid_slug(instance):
        print(f'  [{company}] invalid workday tenant/instance — skipping')
        return None
    if board and not _valid_slug(board):
        print(f'  [{company}] invalid workday board {board!r} — skipping')
        return None
    if board:
        cxs_root = f'https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{board}'
    else:
        cxs_root = f'https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}'
    api_url = f'{cxs_root}/jobs'
    base_url = f'https://{tenant}.{instance}.myworkdayjobs.com'
    wd_headers = {**HEADERS, 'Content-Type': 'application/json',
                  'Accept': 'application/json'}

    search_terms = ['cyber', 'security', 'new grad', 'early career']
    if security_company:
        # A bare 'intern' sweep at a general employer pages through hundreds
        # of non-cyber intern reqs that is_cyber_title rejects anyway (cyber
        # interns already match 'cyber'/'security'). Only at pure-play
        # security companies do generic titles like "Software Engineer
        # Intern" count, so only there is the broad term worth the requests.
        search_terms += ['graduate', 'associate engineer', 'engineer i',
                         'intern']
    # Opt-in per-company terms (companies.yml `search_terms:`) for cohort-heavy
    # tenants whose GRC/identity/privacy roles avoid the 'cyber'/'security'
    # tokens; each term is a full paginated sweep, so only add where it pays.
    if extra_terms:
        search_terms += [t for t in extra_terms if t not in search_terms]

    limit = 20
    jobs = []
    seen_paths = set()
    any_ok = False
    for term in search_terms:
        offset = 0
        for _page in range(MAX_PAGES):
            payload = {'appliedFacets': {}, 'limit': limit, 'offset': offset,
                       'searchText': term}
            data = fetch_json(api_url, method='POST', json=payload,
                              headers=wd_headers,
                              label=f'{company} Workday "{term}"')
            if data is None:
                break
            any_ok = True
            postings = data.get('jobPostings', [])
            if not postings:
                break
            for job in postings:
                path = job.get('externalPath', '')
                if not path or path in seen_paths:
                    continue
                seen_paths.add(path)
                # Job pages 404 without the board segment in the URL.
                public_root = f'{base_url}/{board}' if board else base_url
                jobs.append({
                    'id': f'workday_{tenant}_{path}',
                    'company': company,
                    'title': job.get('title', ''),
                    'location': job.get('locationsText', ''),
                    'url': f'{public_root}{path}',
                    'board': 'Workday',
                    '_path': path,
                })
            total = data.get('total')
            offset += len(postings)
            # Short page, exhausted `total` (when present), or the MAX_PAGES
            # backstop stop the loop so a bad `total` can't run to the timeout.
            if len(postings) < limit or (total is not None and offset >= total):
                break
            time.sleep(0.3)

    # No page fetched at all -> a real failure, not an empty board.
    if not any_ok:
        return None

    # The list view gives no description and hides multi-location postings
    # behind "N Locations". Fetch details for the few title-level candidates
    # so the US filter and clearance detection see real data.
    for job in jobs:
        title = job['title']
        path = job.pop('_path', None)
        if is_rejected_title(title) or not is_cyber_title(title, security_company):
            continue
        # Leveled candidates need location detail; AI flat titles need the
        # description so the requires_experience gate in evaluate_job can run.
        if classify_level(title) is None and not AI_CATEGORY_RE.search(title.lower()):
            continue
        if not path:
            continue
        needs_locations = MULTI_LOCATION_RE.match(job['location'].strip())
        location, description = fetch_workday_detail(cxs_root, path, wd_headers,
                                                     label=f'{company} Workday')
        if needs_locations and location:
            job['location'] = location
        if description:
            job['description'] = description
        time.sleep(0.3)
    return jobs


def scrape_oracle(company, host, site):
    """Oracle Recruiting Cloud (Candidate Experience) public JSON API.

    `host` is the tenant host (e.g. 'company.fa.us2.oraclecloud.com'); `site`
    is the CE site number (e.g. 'CX_1'). Unlocks large enterprises/banks that
    run cyber-analyst new-grad programs but aren't on the other ATSs.
    """
    if not _valid_host(host) or not _valid_slug(site):
        print(f'  [{company}] invalid oracle host/site — skipping')
        return None
    api = f'https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions'
    limit = 200
    jobs = []
    offset = 0
    for _page in range(MAX_PAGES):
        finder = (f'findReqs;siteNumber={site},limit={limit},offset={offset},'
                  f'sortBy=POSTING_DATES_DESC')
        params = {'onlyData': 'true',
                  'expand': 'requisitionList.secondaryLocations',
                  'finder': finder}
        data = fetch_json(api, params=params, label=f'{company} Oracle')
        if data is None:
            return jobs if jobs else None
        items = data.get('items') or []
        req_list = items[0].get('requisitionList', []) if items else []
        if not req_list:
            break
        for job in req_list:
            job_id = str(job.get('Id', ''))
            secondary = [s.get('Name', '') for s in job.get('secondaryLocations') or []]
            locations = [job.get('PrimaryLocation', '')] + secondary
            location = '; '.join(dict.fromkeys(x for x in locations if x))
            jobs.append({
                'id': f'oracle_{site}_{job_id}',
                'company': company,
                'title': job.get('Title', ''),
                'location': location,
                'url': f'https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}',
                'board': 'Oracle',
            })
        total = items[0].get('TotalJobsCount') if items else None
        offset += len(req_list)
        if len(req_list) < limit or (total is not None and offset >= total):
            break
        time.sleep(0.3)
    return jobs


def scrape_amazon():
    base_url = 'https://www.amazon.jobs/en/search.json'
    params = {
        'base_query': 'security engineer OR "security analyst" OR cybersecurity',
        'loc_query': 'united states',
        'result_limit': 100,
        'offset': 0,
    }
    jobs = []
    for _page in range(MAX_PAGES):
        data = fetch_json(base_url, params=params, label='Amazon')
        if data is None:
            return jobs if jobs else None
        postings = data.get('jobs', [])
        if not postings:
            break
        for job in postings:
            job_id = str(job.get('id_icims', job.get('id', '')))
            job_path = job.get('job_path', '')
            url = (f'https://www.amazon.jobs{job_path}' if job_path
                   else f'https://www.amazon.jobs/en/jobs/{job_id}')
            jobs.append({
                'id': f'amazon_{job_id}',
                'company': 'Amazon',
                'title': job.get('title', ''),
                'location': job.get('location', ''),
                'url': url,
                'board': 'Amazon Jobs',
                'description': job.get('description', ''),
            })
        total = data.get('hits')
        params['offset'] += len(postings)
        if (len(postings) < params['result_limit']
                or (total is not None and params['offset'] >= total)):
            break
        time.sleep(0.5)
    return jobs


def scrape_usajobs():
    """Federal cyber roles for recent grads and students (Pathways internships).

    Needs USAJOBS_API_KEY + USAJOBS_EMAIL.
    """
    api_key = os.environ.get('USAJOBS_API_KEY')
    email = os.environ.get('USAJOBS_EMAIL', 'cyber-jobs-scraper@example.com')
    if not api_key:
        return []
    headers = {
        'Host': 'data.usajobs.gov',
        'User-Agent': email,
        'Authorization-Key': api_key,
    }
    jobs = []
    any_ok = False
    # A posting can be open to both hiring paths; keep one copy.
    seen_ids = set()
    # 'student' is the Pathways internship path (codelist value STUDENT —
    # singular, unlike GRADUATES); titles usually come back as
    # "Student Trainee (...)" and classify as intern.
    for hiring_path in ('graduates', 'student'):
        for page in range(1, MAX_PAGES + 1):
            params = {
                'Keyword': 'cybersecurity',
                'HiringPath': hiring_path,
                'ResultsPerPage': 250,
                'Page': page,
            }
            # allow_redirects=False so the api-key header can't be forwarded to
            # another host on a cross-host redirect.
            data = fetch_json('https://data.usajobs.gov/api/search',
                              params=params, headers=headers, label='USAJOBS',
                              allow_redirects=False)
            if data is None:
                break
            any_ok = True
            result = data.get('SearchResult', {})
            items = result.get('SearchResultItems', [])
            if not items:
                break
            for item in items:
                d = item.get('MatchedObjectDescriptor', {})
                job_id = item.get('MatchedObjectId', '')
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                locations = d.get('PositionLocation', [])
                loc = locations[0].get('LocationName', '') if locations else ''
                # Student-only postings are Pathways internships even when
                # the title omits "Student Trainee"; a posting also open
                # to graduates stays title-classified.
                paths = [p.lower() for p in
                         (d.get('UserArea', {}).get('Details', {})
                          .get('HiringPath') or [])]
                jobs.append({
                    'id': f'usajobs_{job_id}',
                    'company': d.get('OrganizationName', 'US Federal Government'),
                    'title': d.get('PositionTitle', ''),
                    'location': loc,
                    'url': d.get('PositionURI', ''),
                    'board': 'USAJOBS',
                    'intern_hint': 'student' in paths and 'graduates' not in paths,
                })
            if page >= int(result.get('UserArea', {}).get('NumberOfPages', 1)):
                break
            time.sleep(0.5)
    if not any_ok:
        return None
    return jobs


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_seen_jobs():
    """Return {job_id: last_seen_date}. Accepts the legacy list format."""
    if SEEN_JOBS_FILE.exists():
        with open(SEEN_JOBS_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        today = datetime.now().strftime('%Y-%m-%d')
        return {jid: today for jid in data}
    return {}


def save_seen_jobs(seen):
    SEEN_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_JOBS_FILE, 'w') as f:
        json.dump(dict(sorted(seen.items())), f, indent=2)


def report_board_health(board_stats, persist=True):
    """Print a run summary, emit GitHub annotations for regressions, roll baseline.

    A board that reliably returned postings but now returns none is flagged as a
    likely broken slug / ATS drift — otherwise breakage is invisible because a
    dead board looks identical to one with no new cyber jobs.
    """
    ok = sum(1 for b in board_stats if b['status'] == 'ok')
    zero = [b for b in board_stats if b['status'] == 'zero']
    broken = [b for b in board_stats if b['status'] in ('FAILED', 'CRASHED')]
    total_raw = sum(b['count'] for b in board_stats)

    baseline = {}
    if BOARD_BASELINE_FILE.exists():
        try:
            baseline = json.loads(BOARD_BASELINE_FILE.read_text())
        except ValueError:
            baseline = {}
    regressed = [b for b in board_stats
                 if b['count'] == 0 and baseline.get(b['label'], 0) > 0]
    for b in regressed:
        print(f'::warning::[{b["label"]}] returned 0 postings but had '
              f'{baseline[b["label"]]} last run (broken slug or ATS drift?)')

    lines = [
        '## Scrape run summary',
        '',
        f'- Boards queried: **{len(board_stats)}** '
        f'({ok} ok · {len(zero)} empty · {len(broken)} failed)',
        f'- Raw postings fetched: **{total_raw}**',
    ]
    if broken:
        lines.append('- ⚠️ Failed/crashed: ' + ', '.join(b['label'] for b in broken))
    if regressed:
        lines.append('- ⚠️ Regressed to zero: ' + ', '.join(b['label'] for b in regressed))
    summary = '\n'.join(lines)
    print('\n' + summary)

    step_summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if step_summary:
        with open(step_summary, 'a') as f:
            f.write(summary + '\n')

    if persist:
        BOARD_BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        BOARD_BASELINE_FILE.write_text(json.dumps(
            {b['label']: b['count'] for b in board_stats},
            indent=2, sort_keys=True))


def load_listings():
    if LISTINGS_FILE.exists():
        with open(LISTINGS_FILE) as f:
            return json.load(f)
    return []


def save_listings(listings):
    tmp = LISTINGS_FILE.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(listings, f, indent=2)
    tmp.replace(LISTINGS_FILE)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Scrape ATS boards for early-career US cyber roles.')
    parser.add_argument('--dry-run', action='store_true',
                        help='scrape + classify but write no files and skip the '
                             'README rebuild — safe to run locally')
    parser.add_argument('--board',
                        help='only run this ATS (e.g. greenhouse, workday, '
                             'amazon, usajobs) for fast local iteration')
    parser.add_argument('--limit', type=int,
                        help='only scrape the first N configured companies per board')
    return parser.parse_args(argv)


def main():
    args = parse_args()
    if args.dry_run:
        print('DRY RUN — no files will be written\n')

    try:
        with open('companies.yml') as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        print(f'ERROR: Failed to load companies.yml: {e}')
        sys.exit(1)

    seen = load_seen_jobs()
    raw_jobs = []
    sec_flags = {}
    board_stats = []

    def want(board):
        return not args.board or args.board == board

    def limited(entries):
        entries = entries or []
        return entries[:args.limit] if args.limit else entries

    def run_scraper(label, fn, *args, security_company=False):
        print(f'Checking {label}...')
        status, count = 'ok', 0
        try:
            found = fn(*args)
            if found is None:
                status = 'FAILED'
            else:
                count = len(found)
                status = 'ok' if count else 'zero'
                for job in found:
                    sec_flags[job['id']] = security_company
                raw_jobs.extend(found)
        except Exception as e:
            status = 'CRASHED'
            print(f'  [{label}] Scraper crashed: {e}')
        board_stats.append({'label': label, 'status': status, 'count': count})
        time.sleep(0.4)

    simple_boards = {
        'greenhouse': scrape_greenhouse,
        'lever': scrape_lever,
        'ashby': scrape_ashby,
        'smartrecruiters': scrape_smartrecruiters,
        'workable': scrape_workable,
        'recruitee': scrape_recruitee,
        'pinpoint': scrape_pinpoint,
    }
    for board, scraper in simple_boards.items():
        if not want(board):
            continue
        for entry in limited(config.get(board)):
            run_scraper(f'{entry["name"]} ({board}/{entry["slug"]})',
                        scraper, entry['name'], entry['slug'],
                        security_company=entry.get('security_company', False))

    if want('workday'):
        for entry in limited(config.get('workday')):
            run_scraper(
                f'{entry["name"]} (workday/{entry["tenant"]})',
                scrape_workday, entry['name'], entry['tenant'], entry['instance'],
                entry.get('board', ''), entry.get('security_company', False),
                entry.get('search_terms'),
                security_company=entry.get('security_company', False),
            )

    if want('oracle'):
        for entry in limited(config.get('oracle')):
            run_scraper(
                f'{entry["name"]} (oracle/{entry["host"]})',
                scrape_oracle, entry['name'], entry['host'], entry['site'],
                security_company=entry.get('security_company', False),
            )

    if want('amazon'):
        run_scraper('Amazon (amazon.jobs)', scrape_amazon)
    if want('usajobs'):
        run_scraper('USAJOBS (data.usajobs.gov)', scrape_usajobs)

    # Only a full run may roll the baseline — a --board/--limit run holds counts
    # for a subset and would blind the zero-regression check for the rest.
    full_run = not args.dry_run and not args.board and not args.limit
    report_board_health(board_stats, persist=full_run)
    print(f'\nScraped {len(raw_jobs)} raw postings; filtering...')

    today = datetime.now().strftime('%Y-%m-%d')
    listings = load_listings()

    # Drop long-closed rows so the board doesn't accumulate dead postings.
    listings, purged = purge_stale_listings(listings, today)
    if purged:
        print(f'Purged {purged} stale closed listing(s)')

    # Let classifier improvements reach already-scraped listings (title-only).
    listings, reclass_changes = reclassify_listings(listings)
    for company, role, old, new in reclass_changes:
        print(f'  RECLASSIFY [{old} -> {new}] {company} — {role}')
    reclassified = len(reclass_changes)

    existing_urls = {normalize_url(e.get('url', '')) for e in listings if e.get('url')}
    # Secondary key catches the same role reposted per-location under distinct
    # req-ID URLs (e.g. one "Intern - Software Engineer" ×10) that URL dedup
    # can't see. Location stays in the key so genuinely different sites remain
    # separate rows.
    existing_keys = {listing_dedup_key(e.get('company', ''), e.get('role', ''),
                                       e.get('location', '')) for e in listings}
    # Rows a dead-link sweep blanked; a still-live posting revives them so a
    # 403/transient false positive self-heals instead of staying 🔒 forever.
    blanked = {listing_dedup_key(e.get('company', ''), e.get('role', ''),
                                 e.get('location', '')): e
               for e in listings if not e.get('url')}
    added = 0
    revived = 0

    for job in raw_jobs:
        jid = job['id']
        location = normalize_location(job.get('location', ''))
        key = listing_dedup_key(job['company'], job.get('title', ''), location)
        # Skip already-seen jobs unless they could revive a blanked row.
        if jid in seen and key not in blanked:
            continue
        verdict = evaluate_job(
            job.get('title', ''), job.get('location', ''),
            job.get('description', ''), sec_flags.get(jid, False),
            job.get('intern_hint', False),
        )
        if verdict is None:
            continue
        level, category = verdict

        url = job.get('url', '')
        if not url:
            # Don't record a URL-less posting as seen — otherwise it's skipped
            # forever even after the ATS later populates the URL.
            continue
        seen[jid] = today
        if key in blanked:
            row = blanked.pop(key)
            row['url'] = url
            row.pop('closed', None)
            row.pop('closed_date', None)
            existing_urls.add(normalize_url(url))
            revived += 1
            print(f'  REVIVED {_oneline(job["company"])} — {_oneline(job["title"])}')
            continue
        if normalize_url(url) in existing_urls or key in existing_keys:
            continue
        existing_urls.add(normalize_url(url))
        existing_keys.add(key)

        listings.append({
            'company': job['company'],
            'role': job['title'].strip(),
            'location': location,
            'type': level,
            'category': category,
            'clearance': requires_clearance(job.get('title', ''),
                                            job.get('description', '')),
            'url': url,
            'source': job.get('board', ''),
            'date_added': today,
        })
        added += 1
        print(f'  NEW [{level}] {_oneline(job["company"])} — {_oneline(job["title"])} '
              f'@ {_oneline(job.get("location", ""))}')

    # Refresh last-seen for every still-live id, then expire the stale ones.
    for job in raw_jobs:
        if job['id'] in seen:
            seen[job['id']] = today
    seen = prune_seen(seen, today)

    changed = added or reclassified or revived or purged
    print(f'\nAdded {added} new listing(s), revived {revived}, '
          f'reclassified {reclassified}, purged {purged}')

    if args.dry_run:
        print('[dry-run] no files written; skipping README rebuild')
        print('Done')
        return

    if changed:
        save_listings(listings)
        rebuild_readme.main()

    save_seen_jobs(seen)
    print('Done')


if __name__ == '__main__':
    main()
