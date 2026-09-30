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
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlparse

import rebuild_readme
import requests
import yaml
from classify import (
    AI_CATEGORY_RE,
    US_STATE_ABBRS,
    _is_foreign_part,
    classify_level,
    evaluate_job,
    is_cyber_title,
    is_rejected_title,
    is_us_location,
    judge_job,
    listing_dedup_key,
    normalize_location,
    prune_seen,
    purge_stale_listings,
    reclassify_listings,
    renormalize_locations,
    requires_clearance,
    strip_html,
)
from common import normalize_url, security_company_flags, write_run_events

LISTINGS_FILE = Path('listings.json')
SEEN_JOBS_FILE = Path('.github/data/seen_jobs.json')
BOARD_BASELINE_FILE = Path('.github/data/board_baseline.json')
# Read by health_check.py, which opens the "Scraper health" issue.
HEALTH_FILE = Path('.github/data/health.json')

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; cyber-jobs-scraper/1.0)'}

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
BACKOFF_BASE = 1.6
MAX_BACKOFF = 30
# Hard caps so a bad `total` or a page that echoes forever can't loop until the
# workflow timeout.
MAX_PAGES = 60

# Boards scrape on a small thread pool (see `scrape_boards`). Each board is its
# own host or one of a handful of shared ATS API hosts, so this many in flight
# keeps the run minutes long as companies.yml grows without leaning on any one
# ATS; the sequential sweep outgrew the workflow timeout at ~350 boards.
SCRAPE_WORKERS = 6

# requests.Session is not guaranteed thread-safe, so each worker thread gets
# its own connection-pooled session.
_thread_local = threading.local()


def _session():
    session = getattr(_thread_local, 'session', None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session
    return session


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

    Retries transient failures (timeouts, connection resets, 429 and any 5xx
    honoring Retry-After, and 200s with a non-JSON body) with exponential
    backoff plus jitter, over the calling thread's pooled Session. Returning
    None (not []) lets callers tell a broken fetch apart from a genuinely
    empty board.
    """
    kwargs.setdefault('timeout', REQUEST_TIMEOUT)
    for attempt in range(MAX_RETRIES):
        last = attempt + 1 == MAX_RETRIES
        try:
            resp = _session().request(method, url, **kwargs)
        except requests.RequestException as e:
            if last:
                print(f'  [{label}] request error: {e}')
                return None
            _sleep_backoff(attempt)
            continue
        # A lone Workday 502 or 504 used to end the whole search term on its
        # first try, and the term's postings with it.
        if resp.status_code == 429 or resp.status_code >= 500:
            if last:
                reason = ' (rate limited)' if resp.status_code in (429, 503) else ''
                print(f'  [{label}] HTTP {resp.status_code}{reason}')
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
#   id, company, title, location, url, board, description (optional),
#   partial_sweep (optional: True when the board's feed was cut short)
# Scrapers return None on an unrecoverable fetch failure and a (possibly empty)
# list otherwise, so the run summary can tell breakage from an empty board.
# ---------------------------------------------------------------------------

# Some boards put the workplace type where the location belongs; the real
# location is then in `offices` / "Job Posting Location" metadata.
WORKPLACE_LABELS = {'in-office', 'hybrid', 'distributed', 'remote', 'onsite',
                    'on-site', 'flexible', ''}

# Metadata fields that hold a place. Matching any name containing "location"
# read Dropbox's 'Career Page Allocation' ('Sales') and 'Location Cost Tier'
# ('Mid'), Anthropic's 'Location Type' and Fastly's 'Work Location Type'
# ('Hybrid') as locations.
GREENHOUSE_LOCATION_FIELDS = {
    'job posting location', 'job post location', 'primary location',
    'additional locations', 'additional job post location', 'office location',
    'careers page: location',
}


def greenhouse_location(job):
    loc = (job.get('location') or {}).get('name', '') or ''
    label = loc.strip().lower()
    if label not in WORKPLACE_LABELS:
        return loc
    parts = [o.get('name') for o in job.get('offices') or [] if o.get('name')]
    for m in job.get('metadata') or []:
        if (isinstance(m, dict)
                and (m.get('name') or '').strip().lower() in GREENHOUSE_LOCATION_FIELDS):
            v = m.get('value')
            if isinstance(v, list):
                parts.extend(str(x) for x in v)
            elif v:
                parts.append(str(v))
    if label == 'remote':
        # 'Remote' is already a location, so only a part that names a place or
        # a remote scope may replace it. GuidePoint files its US-remote GPSU
        # internship under the office 'GuidePoint University (GPSU)', which
        # failed the US check. A part saying 'remote' stays so Bitwarden's
        # 'UK Remote' does not revert to a US 'Remote'.
        parts = [p for p in parts if 'remote' in p.lower() or is_us_location(p)
                 or _is_foreign_part(p)]
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
        cats = job.get('categories') or {}
        # `location` and `country` describe the primary site only. Saviynt
        # files a Vancouver + Milpitas req under country CA, and Shield AI
        # lists 'Wichita Metro Area' first with six US sites behind it in
        # `allLocations`.
        sites = [cats.get('location') or '']
        sites += [x for x in cats.get('allLocations') or [] if isinstance(x, str)]
        location = '; '.join(dict.fromkeys(x.strip() for x in sites if x and x.strip()))
        country = job.get('country', '')
        if country and country.upper() != 'US' and not is_us_location(location):
            continue
        # Lever's commitment category ("Internship", "Intern") flags intern
        # reqs whose titles omit the word.
        commitment = (cats.get('commitment') or '').lower()
        jobs.append({
            'id': f'lever_{slug}_{job["id"]}',
            'company': company,
            'title': job.get('text', ''),
            'location': location,
            'url': job.get('hostedUrl', ''),
            'board': 'Lever',
            'description': _lever_description(job),
            'intern_hint': 'intern' in commitment,
        })
    return jobs


def _lever_description(job):
    # descriptionPlain is only the intro. The requirement bullets live in
    # `lists`, so Immuta 'Software Engineer II (Marketplace)' hid its "3 to 5
    # years" from the experience gate. Headings stay so the gate can tell a
    # REQUIRED list from a PREFERRED one.
    parts = [job.get('descriptionPlain') or '']
    for section in job.get('lists') or []:
        if not isinstance(section, dict):
            continue
        heading = (section.get('text') or '').strip()
        body = strip_html(section.get('content') or '').strip()
        if heading or body:
            parts.append(f'{heading}\n{body}'.strip())
    return '\n\n'.join(p for p in parts if p.strip())


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
        locations = [_ashby_place(job.get('location', '') or job.get('locationName', ''),
                                  job.get('address'))]
        locations += [_ashby_place(s.get('location', ''), s.get('address'))
                      for s in job.get('secondaryLocations') or [] if isinstance(s, dict)]
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


US_COUNTRY_NAMES = {'us', 'usa', 'united states', 'united states of america'}


def _ashby_place(label, address):
    # Boards name a site however they like: bare 'San Mateo', 'Ann Arbor' or
    # 'Oakland', or 'North America', all of which fail the US check, while
    # the structured address beside them says United States. 105 postings
    # across the Ashby boards read that way. The label wins whenever it
    # already reads as US.
    if label and is_us_location(label):
        return label
    postal = (address or {}).get('postalAddress') or {}
    if (postal.get('addressCountry') or '').strip().lower() not in US_COUNTRY_NAMES:
        return label
    city = (postal.get('addressLocality') or '').strip()
    region = (postal.get('addressRegion') or '').strip()
    if label and label.strip().lower() != city.lower():
        # A scope label is not the address, which is often the head office:
        # WorkOS's 'United States & Canada' roles carry San Francisco, so
        # the scope stays and only the country is added.
        return f'{label}; United States'
    if not region or region.lower() in US_COUNTRY_NAMES:
        # 'San Mateo, United States' normalizes back to the bare city.
        return 'United States'
    region = US_STATE_ABBRS.get(region.lower(), region)
    return ', '.join(p for p in (city, region) if p)


def scrape_smartrecruiters(company, identifier, security_company=False):
    url = f'https://api.smartrecruiters.com/v1/companies/{identifier}/postings'
    limit = 100
    params = {'limit': limit, 'offset': 0}
    jobs = []
    # Cleared when a page fails or MAX_PAGES runs out, as in scrape_workday:
    # a sweep cut short used to come back as a plain list, so
    # retire_vanished_listings read the reqs past the cut as closed.
    complete = True
    for _page in range(MAX_PAGES):
        data = fetch_json(url, params=params, label=f'{company} SmartRecruiters')
        if data is None:
            complete = False
            break
        content = data.get('content', [])
        if not content:
            break
        for job in content:
            loc = job.get('location') or {}
            # `remote` says nothing about the country: Sectigo's remote
            # 'Software Engineer (Java)' is in Iasi, Romania and its remote
            # 'Network Engineer' in Manchester, and both were stored as
            # Remote (US).
            if (loc.get('country') or '').lower() != 'us':
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
    else:
        complete = False

    if not complete and not jobs:
        return None
    if not complete:
        for job in jobs:
            job['partial_sweep'] = True

    # The postings list carries no description, so Kudelski 'Network Support
    # Engineer I/II' passed the experience gate on its title while the posting
    # asks for 2 to 3 years. Candidates are judged under the company's own
    # flag: judged as a security company, LLNL spent its cap on generic
    # intern titles that evaluate_job rejects anyway.
    fetched = 0
    for job in jobs:
        if (fetched >= SMARTRECRUITERS_DETAIL_CAP
                or not _wants_detail(job['title'], security_company)):
            continue
        fetched += 1
        description = fetch_smartrecruiters_description(
            f'{url}/{job["id"].rsplit("_", 1)[-1]}', label=f'{company} SmartRecruiters')
        if description:
            job['description'] = description
        time.sleep(0.3)
    return jobs


# Detail requests per SmartRecruiters board per run, as ORACLE_DETAIL_CAP.
SMARTRECRUITERS_DETAIL_CAP = 30


def fetch_smartrecruiters_description(url, label=''):
    """Return a posting's jobAd sections as one body, or '' on failure."""
    data = fetch_json(url, label=label)
    sections = ((data or {}).get('jobAd') or {}).get('sections') or {}
    parts = []
    for section in sections.values():
        if not isinstance(section, dict):
            continue
        text = (section.get('text') or '').strip()
        if text:
            parts.append(f'<h3>{section.get("title") or ""}</h3>{text}')
    return '\n'.join(parts)


def _workable_part(city, region, country, code):
    place = [p.strip() for p in (city, region) if p and p.strip()]
    if code == 'US':
        return ', '.join(place)
    # A foreign part keeps its country so the US filter can reject it: "Attica"
    # alone says nothing about Greece.
    return ', '.join(place + [country or code])


def workable_location(job):
    """Build a location from the widget API's `locations[]` and flat fields.

    The widget API has no `location` object. Each posting carries flat `city`,
    `state`, `country` (a full name) and `telecommuting`, plus `locations[]`
    with a `countryCode` per site. Reading a `location` key that does not
    exist gave every Workable posting a blank location, so none ever passed
    the US filter (Trail of Bits 'Security Engineer I, Application Security').
    """
    sites = [(s.get('city'), s.get('region'), s.get('country'),
              (s.get('countryCode') or '').upper())
             for s in job.get('locations') or []
             if isinstance(s, dict) and not s.get('hidden')]
    if not sites:
        country = job.get('country') or ''
        code = 'US' if country.strip().lower() in ('united states', 'us', 'usa') else country
        sites = [(job.get('city'), job.get('state'), country, code)]
    parts = [(site[3], _workable_part(*site)) for site in sites]
    us = any(code == 'US' for code, _ in parts)
    names = [part for _, part in parts if part]
    if us and job.get('telecommuting'):
        names.append('Remote (US)')
    elif us and not any(part for code, part in parts if code == 'US'):
        names.append('United States')
    return '; '.join(dict.fromkeys(names))


def scrape_workable(company, slug):
    # details=true adds each posting's description to the same response.
    url = f'https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true'
    data = fetch_json(url, label=f'{company} Workable')
    if data is None:
        return None
    check_container(data, 'jobs', f'{company} Workable')
    jobs = []
    for job in data.get('jobs', []):
        job_id = job.get('shortcode', job.get('id', ''))
        jobs.append({
            'id': f'workable_{slug}_{job_id}',
            'company': company,
            'title': job.get('title', ''),
            'location': workable_location(job),
            'url': f'https://apply.workable.com/{slug}/j/{job_id}/',
            'board': 'Workable',
            'description': job.get('description', ''),
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
        location = recruitee_location(job)
        if not location:
            continue
        job_id = str(job.get('id', ''))
        jobs.append({
            'id': f'recruitee_{slug}_{job_id}',
            'company': company,
            'title': job.get('title', ''),
            'location': location,
            'url': job.get('careers_url',
                           f'https://{slug}.recruitee.com/o/{job.get("slug", job_id)}'),
            'board': 'Recruitee',
            # The requirements block holds the years bar, so reading neither
            # field kept every Recruitee posting out of the experience gate.
            'description': '\n'.join(filter(None, (job.get('description'),
                                                   job.get('requirements')))),
        })
    return jobs


def _recruitee_country(site):
    code = (site.get('country_code') or '').strip().upper()
    if code:
        return code
    name = (site.get('country') or '').strip().lower()
    return 'US' if name in ('us', 'usa', 'united states') else name


def _recruitee_part(site):
    city = (site.get('city') or '').strip()
    if _recruitee_country(site) == 'US':
        region = site.get('state_code') or site.get('state_name') or ''
        return ', '.join(p for p in (city, region.strip()) if p) or 'United States'
    # A foreign site keeps its country so the US filter can reject it.
    country = site.get('country') or site.get('country_code') or ''
    return ', '.join(p for p in (city, country.strip()) if p)


def recruitee_location(job):
    """Build a location from an offer's sites, or '' when none is in the US.

    The offers API has `state_code`, not the `province` this once read, so a US
    posting came out as a bare city ('Herndon') and failed the US check. And
    `remote` names no country: Aikido's remote Customer Success Engineers in
    Romania, Dubai and Sydney were stored as Remote (US).
    """
    if job.get('remote') and _recruitee_country(job) == 'US':
        return 'Remote (US)'
    sites = [s for s in job.get('locations') or [] if isinstance(s, dict)] or [job]
    if not any(_recruitee_country(s) == 'US' for s in sites):
        return ''
    return '; '.join(dict.fromkeys(p for p in map(_recruitee_part, sites) if p))


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
        # Pinpoint marks a Manchester-based home worker 'remote' too (NCC Group
        # 'Associate SOC Analyst'), so only a USA location name reads as US.
        if (job.get('workplace_type') == 'remote'
                and (loc.get('name') or '').strip().upper().startswith('USA')):
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


# Most tenants hide a multi-site req behind "N Locations"; Motorola lists its
# first site and a trailing "More..." ("Chicago, IL, More...") instead.
MULTI_LOCATION_RE = re.compile(r'^\d+ locations$|(?:^|[\s,])more\.{3}$', re.IGNORECASE)

# Search results are relevance-ranked, so student cyber titles sit near the top.
# Paging every term to the end reached 227 title candidates across 73 tenants
# in 3,243 pages and pushed a full dry run past 14 minutes; 15 pages reach 197
# of them in 1,551. Most of what lies deeper is Palo Alto Networks reposting
# 'Associate Systems Engineer'. A sweep that stops here is flagged partial, and
# retire_vanished_listings asks the detail endpoint about its missing rows.
WORKDAY_MAX_PAGES = 15


def _wants_detail(title, security_company):
    if is_rejected_title(title) or not is_cyber_title(title, security_company):
        return False
    # Leveled candidates need the description, since the experience gate in
    # evaluate_job runs on every level; AI flat titles need it for the same
    # reason.
    return classify_level(title) is not None or bool(AI_CATEGORY_RE.search(title.lower()))


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


_WORKDAY_JOB_URL_RE = re.compile(
    r'^https://([A-Za-z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com/'
    r'(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)(/job/[^?#]+)')


def workday_posting(url):
    """Ask Workday's detail endpoint about one posting.

    Returns (state, info). `state` is 'live', 'gone', or None when the answer
    says nothing. The public job page answers 200 even after a req closes, but
    the cxs detail endpoint does not: a live req is 200 with canApply, a closed
    one 403 with errorCode S22 (Nightwing JR102051, RTX 01870858), an unknown
    path 404 with S21. `info` is the live posting's `jobPostingInfo` (title,
    location, additionalLocations, jobDescription), else None, so a row past a
    capped sweep can be re-judged off the same request.
    """
    m = _WORKDAY_JOB_URL_RE.match(url or '')
    if not m:
        return None, None
    tenant, instance, board, path = m.groups()
    api = f'https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{board}{path}'
    try:
        resp = _session().get(api, headers={'Accept': 'application/json'},
                              timeout=REQUEST_TIMEOUT)
        body = resp.json()
    except (requests.RequestException, ValueError):
        return None, None
    # A JSON null or list body raised AttributeError out of the vanished pass
    # and took the whole scrape down with it.
    if not isinstance(body, dict):
        return None, None
    if resp.status_code == 200:
        info = body.get('jobPostingInfo')
        info = info if isinstance(info, dict) else {}
        if info.get('canApply') is False:
            return 'gone', None
        return 'live', info
    if resp.status_code in (403, 404) and body.get('errorCode') in ('S21', 'S22'):
        return 'gone', None
    return None, None


def workday_posting_state(url):
    """Return only the 'live', 'gone' or None state from `workday_posting`."""
    return workday_posting(url)[0]


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
    # Cleared when a term hits the page cap or a page fails mid-sweep. Postings
    # past that point went unseen, so their absence says nothing about closure
    # and retire_vanished_listings must not judge this board this run.
    complete = True
    for term in search_terms:
        offset = 0
        total = None
        for page in range(WORKDAY_MAX_PAGES):
            payload = {'appliedFacets': {}, 'limit': limit, 'offset': offset,
                       'searchText': term}
            data = fetch_json(api_url, method='POST', json=payload,
                              headers=wd_headers,
                              label=f'{company} Workday "{term}"')
            if data is None:
                complete = False
                break
            any_ok = True
            postings = data.get('jobPostings') or []
            if page == 0:
                # cxs reports `total` on the first page only and 0 after it
                # (Leidos 'security': 1662 at offset 0, 0 at offset 20). Re-read
                # per page, it stopped every term after 40 postings and hid
                # Booz Allen's 2027 Summer Games cyber interns at position 93.
                total = data.get('total')
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
            offset += len(postings)
            # A short or empty page, the first page's `total`, or the page cap
            # ends the term, so a lying `total` can't run to the timeout.
            if len(postings) < limit or (total and offset >= total):
                break
            time.sleep(0.3)
        else:
            complete = False

    # No page fetched at all -> a real failure, not an empty board. A sweep
    # that lost pages and found nothing is not proof of an empty board either.
    if not any_ok or (not complete and not jobs):
        return None

    # The list view gives no description and hides multi-location postings
    # behind "N Locations". Fetch details for the few title-level candidates
    # so the US filter and clearance detection see real data.
    for job in jobs:
        path = job.pop('_path', None)
        if not complete:
            job['partial_sweep'] = True
        if not path or not _wants_detail(job['title'], security_company):
            continue
        needs_locations = MULTI_LOCATION_RE.search(job['location'].strip())
        location, description = fetch_workday_detail(cxs_root, path, wd_headers,
                                                     label=f'{company} Workday')
        if needs_locations and location:
            job['location'] = location
        if description:
            job['description'] = description
        time.sleep(0.3)
    return jobs


# Detail requests per Oracle board per run. JPMorgan's CX_1001 lists 7,500
# reqs and today yields 7 candidates, SAIC 3; each detail call is a round trip.
ORACLE_DETAIL_CAP = 30


def fetch_oracle_description(host, site, req_id, label=''):
    """Return a req's external description and qualifications, or '' on failure."""
    api = f'https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails'
    params = {'onlyData': 'true', 'finder': f'ById;Id="{req_id}",siteNumber={site}'}
    data = fetch_json(api, params=params, label=label)
    items = (data or {}).get('items') or []
    if not items:
        return ''
    fields = (items[0].get('ExternalDescriptionStr'), items[0].get('ExternalQualificationsStr'))
    return '\n\n'.join(f for f in fields if f and f.strip())


def scrape_oracle(company, host, site, security_company=False):
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
    complete = True
    for _page in range(MAX_PAGES):
        finder = (f'findReqs;siteNumber={site},limit={limit},offset={offset},'
                  f'sortBy=POSTING_DATES_DESC')
        params = {'onlyData': 'true',
                  'expand': 'requisitionList.secondaryLocations',
                  'finder': finder}
        data = fetch_json(api, params=params, label=f'{company} Oracle')
        if data is None:
            if not jobs:
                return None
            complete = False
            break
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
                # Amex and Honeywell both post under site CX_1, so the site
                # alone does not scope a req id.
                'id': f'oracle_{host}_{site}_{job_id}',
                'legacy_id': f'oracle_{site}_{job_id}',
                'company': company,
                'title': job.get('Title', ''),
                'location': location,
                'url': f'https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}',
                'board': 'Oracle',
                '_req': job_id,
            })
        total = items[0].get('TotalJobsCount') if items else None
        offset += len(req_list)
        if len(req_list) < limit or (total is not None and offset >= total):
            break
        time.sleep(0.3)
    else:
        complete = False

    # The list view carries no description, so the experience gate and the
    # clearance flag saw only the title: SAIC 'Tier II or III ... IAM
    # Administrator' wants 5 years and 'Cyber Engineer Associate' a TS/SCI
    # with polygraph. Newest reqs come first, so the cap spends its requests
    # on the postings a student is most likely to still apply to. Judged as a
    # general employer, Fortinet never fetched 'Software Engineer I' and its
    # like, so they skipped the experience gate.
    fetched = 0
    for job in jobs:
        req = job.pop('_req')
        if not complete:
            job['partial_sweep'] = True
        if (fetched >= ORACLE_DETAIL_CAP or not req
                or not _wants_detail(job['title'], security_company)):
            continue
        fetched += 1
        description = fetch_oracle_description(host, site, req, label=f'{company} Oracle')
        if description:
            job['description'] = description
        time.sleep(0.3)
    return jobs


# Eightfold and Phenom search is fuzzy and relevance-ranked: Lockheed's
# 'security' query matches 3,039 postings led by badge and facility-security
# reps, and BAE's 'cyber' matches nearly its whole board. Cyber, intern and
# early-career titles rank near the top, so each term reads a bounded prefix
# instead of the whole feed.
EIGHTFOLD_TERMS = ('cyber', 'intern', 'early career')
EIGHTFOLD_MAX_PAGES = 50
PHENOM_TERMS = ('cyber', 'intern')
PHENOM_PAGE_SIZE = 50
PHENOM_MAX_PAGES = 4

# microsoft.eightfold.ai answers 429 with no Retry-After after about ten quick
# requests and clears within seconds, which fetch_json's short retries do not
# outlast.
RATE_LIMIT_DELAYS = (2, 4, 8, 16)
EIGHTFOLD_PAGE_DELAY = 0.5

# Lockheed tags each req with a hiring track. 'Experienced Professional' is its
# lateral-hire track and holds 'Associate Cyber Software Engineer' and 'Level 2
# DevSecOps Engineer' reqs whose titles read as early career.
EIGHTFOLD_LEVEL_FIELD = 'efcustomTextLevelofexperience'
EIGHTFOLD_EXPERIENCED_LEVELS = {'experienced professional'}


def _get_json_patiently(url, *, method='GET', label='', **kwargs):
    kwargs.setdefault('timeout', REQUEST_TIMEOUT)
    for delay in (*RATE_LIMIT_DELAYS, None):
        try:
            resp = _session().request(method, url, **kwargs)
        except requests.RequestException as e:
            if delay is None:
                print(f'  [{label}] request error: {_oneline(e)}')
                return None
            time.sleep(delay)
            continue
        if resp.status_code in (429, 503):
            if delay is None:
                print(f'  [{label}] HTTP {resp.status_code} (rate limited)')
                return None
            time.sleep(delay)
            continue
        if resp.status_code != 200:
            print(f'  [{label}] HTTP {resp.status_code}')
            return None
        try:
            return resp.json()
        except ValueError:
            print(f'  [{label}] non-JSON 200 response')
            return None
    return None


def _needs_detail(title, security_company):
    # The same candidate filter scrape_workday applies before its detail
    # fetch: only titles that can still pass evaluate_job are worth a request,
    # and those need the description for the experience gate.
    if is_rejected_title(title) or not is_cyber_title(title, security_company):
        return False
    return classify_level(title) is not None or bool(AI_CATEGORY_RE.search(title.lower()))


_STATE_ONLY_RE = re.compile(r'^\s*[A-Z]{2}\s*,\s*[A-Z]{2}\s*$')


def _eightfold_locations(pos):
    # The standardized list is cleaner ('Orlando, FL, US' over Microsoft's
    # 'United States, Washington, Redmond') but sometimes drops the city:
    # Lockheed's Hanover and Annapolis Junction reqs read 'MD,US' there. The
    # two lists run in parallel, so a state-only entry takes its raw twin.
    std = pos.get('standardizedLocations') or []
    raw = pos.get('locations') or []
    if not std:
        return raw
    if len(std) == len(raw):
        return [r if isinstance(s, str) and _STATE_ONLY_RE.match(s) and r else s
                for s, r in zip(std, raw, strict=True)]
    return std


def scrape_eightfold(company, tenant, domain, security_company=False,
                     extra_terms=None):
    """Eightfold PCSX careers search (`<tenant>.eightfold.ai/careers`).

    Reads 10 US postings per page for each search term, skips reqs on the
    tenant's experienced-hire track, and fetches descriptions for title-level
    candidates so the experience gate can run.
    """
    if not _valid_slug(tenant) or not _valid_host(domain):
        print(f'  [{company}] invalid eightfold tenant/domain — skipping')
        return None
    root = f'https://{tenant}.eightfold.ai'
    headers = {**HEADERS, 'Accept': 'application/json'}
    terms = list(EIGHTFOLD_TERMS)
    if extra_terms:
        terms += [t for t in extra_terms if t not in terms]

    jobs = []
    seen_ids = set()
    any_ok = False
    # Cleared when a term hits the page cap or a page fails. Microsoft's
    # 'security' matches 970 postings against a 500 cap, and without the flag
    # retire_vanished_listings read the reqs past the cut as closed.
    complete = True
    for term in terms:
        start = 0
        for _page in range(EIGHTFOLD_MAX_PAGES):
            params = {'domain': domain, 'query': term, 'location': 'United States',
                      'start': start}
            data = _get_json_patiently(f'{root}/api/pcsx/search', params=params,
                                       headers=headers,
                                       label=f'{company} Eightfold "{term}"')
            if data is None:
                complete = False
                break
            any_ok = True
            page = data.get('data') if isinstance(data, dict) else None
            if not isinstance(page, dict):
                check_container(data, 'data', f'{company} Eightfold')
                break
            check_container(page, 'positions', f'{company} Eightfold')
            positions = page.get('positions') or []
            if not positions:
                break
            for pos in positions:
                pid = str(pos.get('id', ''))
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                levels = {str(v).strip().lower() for v in pos.get(EIGHTFOLD_LEVEL_FIELD) or []}
                if levels & EIGHTFOLD_EXPERIENCED_LEVELS:
                    continue
                locations = _eightfold_locations(pos)
                jobs.append({
                    'id': f'eightfold_{tenant}_{pid}',
                    'company': company,
                    'title': pos.get('name', ''),
                    'location': '; '.join(dict.fromkeys(x for x in locations if x)),
                    'url': f'{root}/careers/job/{pid}',
                    'board': 'Eightfold',
                })
            start += len(positions)
            total = page.get('count')
            if total is not None and start >= total:
                break
            time.sleep(EIGHTFOLD_PAGE_DELAY)
        else:
            complete = False

    if not any_ok or (not complete and not jobs):
        return None

    for job in jobs:
        if not complete:
            job['partial_sweep'] = True
        if not _needs_detail(job['title'], security_company):
            continue
        pid = job['id'].rsplit('_', 1)[-1]
        data = _get_json_patiently(f'{root}/api/pcsx/position_details',
                                   params={'position_id': pid, 'domain': domain,
                                           'hl': 'en'},
                                   headers=headers, label=f'{company} Eightfold detail')
        detail = (data or {}).get('data') or {}
        if isinstance(detail, dict) and detail.get('jobDescription'):
            job['description'] = detail['jobDescription']
        time.sleep(EIGHTFOLD_PAGE_DELAY)
    return jobs


def _phenom_body(lang, country, **fields):
    return {'lang': lang, 'country': country, 'deviceType': 'desktop',
            'siteType': 'external', **fields}


def scrape_phenom(company, host, lang, country, security_company=False,
                  extra_terms=None):
    """Phenom People career sites, read through their `/widgets` search API.

    `lang` and `country` are the site's locale pair (MITRE `en_us`/`us`, BAE
    `en_global`/`global`); a wrong pair returns no jobs. Descriptions come from
    the same endpoint's jobDetail call, for title-level candidates only.
    """
    if not _valid_host(host) or not _valid_slug(lang) or not _valid_slug(country):
        print(f'  [{company}] invalid phenom host/lang/country — skipping')
        return None
    api = f'https://{host}/widgets'
    headers = {**HEADERS, 'Content-Type': 'application/json',
               'Accept': 'application/json'}
    locale_path = f'{country}/{lang.split("_")[0]}'
    terms = list(PHENOM_TERMS)
    if extra_terms:
        terms += [t for t in extra_terms if t not in terms]

    jobs = []
    seen_ids = set()
    any_ok = False
    # Cleared when a term hits the page cap or a page fails, as in
    # scrape_eightfold. BAE's 'cyber' matches 1,837 postings against 200.
    complete = True
    for term in terms:
        offset = 0
        for _page in range(PHENOM_MAX_PAGES):
            body = _phenom_body(lang, country, pageName='search-results',
                                ddoKey='refineSearch', keywords=term, jobs=True,
                                size=PHENOM_PAGE_SIZE, selected_fields={})
            body.update({'from': offset, 'global': True})
            data = _get_json_patiently(api, method='POST', json=body, headers=headers,
                                       label=f'{company} Phenom "{term}"')
            if data is None:
                complete = False
                break
            any_ok = True
            check_container(data, 'refineSearch', f'{company} Phenom')
            search = data.get('refineSearch') or {}
            postings = (search.get('data') or {}).get('jobs') or []
            if not postings:
                break
            for job in postings:
                job_id = str(job.get('jobId') or job.get('reqId') or '')
                if not _valid_slug(job_id) or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                locations = job.get('multi_location') or [job.get('location', '')]
                jobs.append({
                    'id': f'phenom_{host}_{job_id}',
                    'company': company,
                    'title': job.get('title', ''),
                    'location': '; '.join(dict.fromkeys(x for x in locations if x)),
                    'url': f'https://{host}/{locale_path}/job/{job_id}',
                    'board': 'Phenom',
                    '_seq': job.get('jobSeqNo', ''),
                })
            offset += len(postings)
            total = search.get('totalHits')
            if len(postings) < PHENOM_PAGE_SIZE or (total is not None and offset >= total):
                break
            time.sleep(0.3)
        else:
            complete = False

    if not any_ok or (not complete and not jobs):
        return None

    for job in jobs:
        seq = job.pop('_seq', '')
        if not complete:
            job['partial_sweep'] = True
        if not _needs_detail(job['title'], security_company):
            continue
        job_id = job['id'].rsplit('_', 1)[-1]
        body = _phenom_body(lang, country, pageName='job', ddoKey='jobDetail',
                            jobId=job_id, jobSeqNo=seq)
        data = _get_json_patiently(api, method='POST', json=body, headers=headers,
                                   label=f'{company} Phenom detail')
        detail = ((data or {}).get('jobDetail') or {}).get('data') or {}
        description = (detail.get('job') or {}).get('description', '')
        if description:
            job['description'] = description
        time.sleep(0.3)
    return jobs


# Jibe search matches descriptions as well as titles, so these stay small:
# JHU APL answers 58 for 'cyber', 71 for 'cybersecurity' and 73 for 'intern',
# against 503 for 'security'. 'cyber' alone misses APL's '2027 Internship -
# Cybersecurity - Mission Engineering', which only the whole word matches.
JIBE_TERMS = ('cyber', 'cybersecurity', 'intern')
JIBE_PAGE_SIZE = 100
JIBE_MAX_PAGES = 5
# careers.jhuapl.edu, careers.pnnl.gov and jobs.exeloncorp.com all set
# crawl-delay: 5 in robots.txt.
JIBE_REQUEST_DELAY = 5

# Each tenant files the student track in its own custom field: APL's tags9
# 'Internship', PNNL's tags2 'University Internships', Exelon's category
# 'Intern/Co-Op'. The word boundary keeps 'International' out.
_JIBE_INTERN_RE = re.compile(r'\bintern(?:ships?|s)?\b', re.IGNORECASE)


def _jibe_place(place):
    city, state = place.get('city') or '', place.get('state') or ''
    if city.isupper():
        city = city.title()
    # Exelon files DC as city 'Washington', state 'Washington, DC'.
    if city and state.startswith(f'{city},'):
        return state
    return ', '.join(p for p in (city, state) if p) or 'United States'


def _jibe_location(data):
    parts = []
    for place in (data, *(data.get('additional_locations') or [])):
        country = (place.get('country_code') or place.get('country') or '').upper()
        if country and country not in ('US', 'USA', 'UNITED STATES'):
            continue
        parts.append(_jibe_place(place))
    return '; '.join(dict.fromkeys(parts))


def _jibe_intern_hint(data):
    labels = [c.get('name', '') for c in data.get('categories') or [] if isinstance(c, dict)]
    for key, value in data.items():
        if key.startswith('tags') and isinstance(value, list):
            labels += [str(v) for v in value]
    return any(_JIBE_INTERN_RE.search(label) for label in labels)


def scrape_jibe(company, host, extra_terms=None):
    """iCIMS Jibe career sites, read through the site's own `/api/jobs` search.

    `host` is the branded careers host (careers.jhuapl.edu). Each search page
    already carries the full description, so there is no detail pass. A
    posting's link is the Jibe job page, since the payload's `apply_url` is an
    iCIMS login wall.
    """
    if not _valid_host(host):
        print(f'  [{company}] invalid jibe host {host!r}, skipping')
        return None
    api = f'https://{host}/api/jobs'
    headers = {**HEADERS, 'Accept': 'application/json'}
    terms = list(JIBE_TERMS)
    if extra_terms:
        terms += [t for t in extra_terms if t not in terms]

    jobs = []
    seen_ids = set()
    any_ok = False
    # Cleared when a page fails or a term hits the page cap, so
    # retire_vanished_listings does not read a cut-short sweep as closures.
    complete = True
    first_request = True
    for term in terms:
        for page in range(1, JIBE_MAX_PAGES + 1):
            if not first_request:
                time.sleep(JIBE_REQUEST_DELAY)
            first_request = False
            params = {'keywords': term, 'page': page, 'limit': JIBE_PAGE_SIZE}
            data = _get_json_patiently(api, params=params, headers=headers,
                                       label=f'{company} Jibe "{term}"')
            if data is None:
                complete = False
                break
            any_ok = True
            if not isinstance(data, dict):
                break
            check_container(data, 'jobs', f'{company} Jibe')
            postings = data.get('jobs') or []
            for posting in postings:
                info = (posting or {}).get('data') or {}
                slug = str(info.get('slug') or info.get('req_id') or '')
                if not _valid_slug(slug) or slug in seen_ids:
                    continue
                seen_ids.add(slug)
                location = _jibe_location(info)
                if not location:
                    continue
                jobs.append({
                    'id': f'jibe_{host}_{slug}',
                    'company': company,
                    'title': info.get('title', ''),
                    'location': location,
                    'url': f'https://{host}/jobs/{slug}',
                    'board': 'Jibe',
                    'description': info.get('description') or info.get('qualifications') or '',
                    'intern_hint': _jibe_intern_hint(info),
                })
            total = data.get('totalCount')
            if len(postings) < JIBE_PAGE_SIZE or (total is not None
                                                 and page * JIBE_PAGE_SIZE >= total):
                break
        else:
            complete = False

    if not any_ok or (not complete and not jobs):
        return None
    if not complete:
        for job in jobs:
            job['partial_sweep'] = True
    return jobs


def _has_description(job):
    return bool((job.get('description') or '').strip())


def _index_live_postings(raw_jobs, companies):
    by_fingerprint, by_url, by_key = {}, {}, {}

    def put(index, key, job):
        held = index.get(key)
        # Eightfold and Workday list one req under several search terms, and
        # only some copies carry the detail body; keep the one that does.
        if held is None or (not _has_description(held) and _has_description(job)):
            index[key] = job

    for job in raw_jobs:
        company = job.get('company', '')
        if company not in companies:
            continue
        fingerprint = job_fingerprint(company, job.get('board', ''), job.get('url', ''))
        if fingerprint:
            put(by_fingerprint, fingerprint, job)
        if job.get('url'):
            put(by_url, normalize_url(job['url']), job)
        put(by_key, listing_dedup_key(company, job.get('title', ''),
                                      normalize_location(job.get('location', ''))), job)
    return by_fingerprint, by_url, by_key


def _partial_boards(raw_jobs):
    return {(job.get('company', ''), job.get('board', ''))
            for job in raw_jobs if job.get('partial_sweep')}


def _needs_probe(entry, fingerprint, partial, failed_companies):
    # Only an open Workday row the sweep could have missed: one behind a
    # capped or broken sweep, which absence alone cannot judge.
    company = entry.get('company', '')
    return (fingerprint is not None and entry.get('source') == 'Workday'
            and bool(entry.get('url')) and not entry.get('closed')
            and ((company, 'Workday') in partial or company in failed_companies))


def _probed_posting(answer):
    state, info = answer
    if state != 'live' or not isinstance(info, dict):
        return None
    title = info.get('title')
    if not isinstance(title, str) or not title.strip():
        return None
    locations = [info.get('location')] + list(info.get('additionalLocations') or [])
    description = info.get('jobDescription')
    return {'title': title,
            'location': '; '.join(dict.fromkeys(x for x in locations
                                                if isinstance(x, str) and x)),
            'description': description if isinstance(description, str) else ''}


def reevaluate_stored_listings(listings, raw_jobs, sec_flags, probe=None,
                               failed_companies=frozenset()):
    """Re-judge stored rows against this run's copy of their live posting.

    Returns (kept, dropped, refreshed). `dropped` holds (row, reason) pairs,
    reason one of classify.JUDGE_REASONS; `refreshed` holds
    (row, field, old, new) for each changed `type`, `category` or `clearance`.

    Every run re-scrapes each live posting, so a stored row can go through the
    full `judge_job` pipeline on its live title, raw location and description,
    with the company's current `security_company` flag from `sec_flags`.
    Without this, a rule or flag change reached only rows inserted after it:
    Jumio kept a 'Research Engineer - Machine Learning & Robotics' new-grad row
    after losing the flag, ExtraHop 'Support Engineer I - UK' stayed Remote (US),
    and categories and 🇺🇸 flags froze at insert. A row is matched to its
    posting by `job_fingerprint`, then normalized URL. Only a row with no
    fingerprint falls back to the (company, role, location) dedup key: the key
    also fits a sibling req with the same title, and the capped RTX sweep that
    missed R100 but reached R200, which asks for 5+ years, deleted R100 while
    it was live.

    `probe` is `workday_posting` or a cached copy of it. The capped RTX, CVS and
    Northrop sweeps never reach some stored rows, so those rows were never
    re-judged: RTX 'Security Specialist II' sat open with NISPOM duties in its
    description. An open Workday row the sweep missed on a partial board, or
    at a company in `failed_companies`, is judged on the detail endpoint's
    answer instead, under the same guardrails.

    Guardrails, all in the keep direction:
      * only companies that returned postings this run are judged, and a row
        with no live match is kept, so a broken slug or a `--board` subset
        cannot erase rows;
      * Community rows carry a maintainer's judgment and are never judged;
      * a posting with no description (Workday and Oracle fetch detail only
        for candidate titles) is not evidence: a 'no-level' verdict, the one
        gate an empty body trips alone, keeps the row with its stored type, and
        `clearance` only turns on from the title;
      * a blank live location never drops a row, nor does Workday's list-view
        placeholder ("2 Locations") left behind by a failed detail fetch;
      * intern rows stay interns: their level may come from an ATS hint, and
        the experience gate exempts them, as it does at insert.
    """
    healthy = {job.get('company', '') for job in raw_jobs}
    candidates = [e for e in listings
                  if e.get('source') != 'Community' and e.get('company', '') in healthy]
    if not candidates:
        return listings, [], []
    by_fingerprint, by_url, by_key = _index_live_postings(
        raw_jobs, {e.get('company', '') for e in candidates})
    partial = _partial_boards(raw_jobs)

    dropped, refreshed = [], []
    for entry in candidates:
        company, url = entry.get('company', ''), entry.get('url', '')
        fingerprint = job_fingerprint(company, entry.get('source', ''), url)
        job = ((by_fingerprint.get(fingerprint) if fingerprint else None)
               or (by_url.get(normalize_url(url)) if url else None))
        if job is None and fingerprint is None:
            job = by_key.get(listing_dedup_key(company, entry.get('role', ''),
                                               entry.get('location', '')))
        if (job is None and probe is not None
                and _needs_probe(entry, fingerprint, partial, failed_companies)):
            job = _probed_posting(probe(url))
        if job is None:
            continue
        described = _has_description(job)
        description = job.get('description', '') if described else ''
        was_intern = entry.get('type') == 'intern'
        verdict, reason = judge_job(
            job.get('title', ''), job.get('location', ''), description,
            sec_flags.get(company, False), job.get('intern_hint', False) or was_intern)
        if verdict is None:
            if reason == 'no-level' and not described:
                continue
            live_location = (job.get('location') or '').strip()
            if reason == 'non-us-location' and (
                    not live_location or MULTI_LOCATION_RE.search(live_location)):
                continue
            dropped.append((entry, reason))
            continue
        level, category = verdict
        clearance = requires_clearance(job.get('title', ''), description)
        updates = [('category', category)]
        if not was_intern:
            updates.append(('type', level))
        if described or clearance:
            updates.append(('clearance', clearance))
        for field, new in updates:
            old = entry.get(field)
            if old != new:
                entry[field] = new
                refreshed.append((entry, field, old, new))
    gone = {id(e) for e, _ in dropped}
    kept = [e for e in listings if id(e) not in gone]
    return kept, dropped, refreshed


# A row is retired once its requisition has been missing from a healthy board
# for this many days. The scrape runs twice daily, so this is ~6 consecutive
# misses: long enough to ride out one partial fetch of a paginated board, short
# enough that a student is not sent to a req that closed last week.
VANISHED_DAYS = 3

# Per source, the part of an apply URL that identifies the requisition. Both a
# stored row and a freshly scraped posting are reduced through these, so the
# vanished check compares like with like without parsing the scraper's internal
# id format — which a stored row cannot rebuild anyway, since a company-hosted
# Greenhouse board keeps the board token out of the URL entirely.
_REQ_PATTERNS = {
    'Greenhouse': (r'/jobs/(\d+)',),
    'Lever': (r'/([0-9a-fA-F-]{36})',),
    'Ashby': (r'/([0-9a-fA-F-]{36})',),
    # Workday rewrites the location and title slugs of a live req
    # (/job/Chantilly-VA/X_R123 -> /job/Reston-VA/X_R123), which made the row
    # look vanished and let a duplicate in. The req id follows the first '_'
    # of the last segment, since a title slug never holds one and Arctic
    # Wolf's ids do ('Professional-Services-Engineer-1_R26_1068').
    'Workday': (r'/job/(?:.*/)?[^/_]*_([^/]+)$', r'(/job/.+)$'),
    'Oracle': (r'/job/(\d+)',),
    'Amazon Jobs': (r'/jobs/(\d+)',),
    'SmartRecruiters': (r'/(\d+)/?$',),
    'Workable': (r'/j/([0-9A-F]{8,})',),
    'Recruitee': (r'/o/([\w-]+)$',),
    'Pinpoint': (r'/postings/([0-9a-fA-F-]{36})',),
    'Eightfold': (r'/careers/job/(\d+)',),
    'Phenom': (r'/job/([A-Za-z0-9_-]+)$',),
    'Jibe': (r'/jobs/([A-Za-z0-9_-]+)$',),
}


def job_fingerprint(company, source, url):
    """Identify one requisition by the stable id inside its apply URL.

    Returns a (company, source, id) tuple, or None when the URL carries nothing
    recognizable. None is the safe answer: an unfingerprintable row is never
    judged by `retire_vanished_listings`, so an unfamiliar URL shape keeps a row
    rather than dropping it.

    Scoped by company because req ids are only unique within a board — two
    Greenhouse tenants can both number a posting 12345.
    """
    if not url or not source:
        return None
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    # Greenhouse's company-hosted boards put the req id in gh_jid and leave the
    # path pointing at a generic careers page, so the query string wins there.
    if source == 'Greenhouse':
        jid = (parse_qs(parts.query).get('gh_jid') or [''])[0].strip()
        if jid:
            return (company, source, jid)
    for pattern in _REQ_PATTERNS.get(source, ()):
        match = re.search(pattern, parts.path)
        if match:
            return (company, source, match.group(1))
    return None


# A board silent this many runs in a row is treated as gone, which is twice
# VANISHED_DAYS at two runs a day. Lakera's board sat empty for 24 runs and
# Todyl's answered 404 for 12 while their open rows stayed on the board, since
# retirement only ever judged boards that returned something.
SILENT_BOARD_RUNS = 4 * VANISHED_DAYS


def failed_board_companies(board_stats):
    """Return the companies with at least one FAILED or CRASHED board this run."""
    return {b['label'].rpartition(' (')[0] for b in board_stats
            if b['status'] in ('FAILED', 'CRASHED')}


def _empty_runs(entry):
    # Entries written before `empty_runs` existed carry only `zero_runs`; every
    # board on a zero streak when it landed was a real empty, none failed.
    runs = entry.get('empty_runs')
    return entry.get('zero_runs', 0) if runs is None else runs


def long_silent_boards(board_stats, history):
    """Return the (company, ats) pairs silent for SILENT_BOARD_RUNS runs straight.

    `board_stats` is this run's per-board result and `history` the stored
    board baseline, so a `--board`/`--limit` run only judges what it fetched.
    A company with two boards on one ATS is silent only when both are.

    The streak is `empty_runs`, which only a real empty answer grows. Counting
    FAILED and CRASHED runs let a six-day IP ban or a scraper crash retire
    every row of a board at once, while the board still held them.
    """
    silent = {}
    for b in board_stats:
        name, _, rest = b['label'].rpartition(' (')
        ats = rest.split('/', 1)[0].rstrip(')').lower()
        streak = _empty_runs(history.get(b['label'])
                             or history.get(_legacy_label(b['label'])) or {})
        quiet = b['count'] == 0 and streak >= SILENT_BOARD_RUNS
        silent[(name, ats)] = silent.get((name, ats), True) and quiet
    return {key for key, is_silent in silent.items() if is_silent}


def retire_vanished_listings(listings, raw_jobs, today, silent_boards=frozenset(),
                             probe=workday_posting_state, failed_companies=frozenset()):
    """Close rows whose requisition has left its own board's feed.

    Mutates and returns the rows it retired. A posting that stops appearing
    among its company's results is a closed req. This is the only path that
    can retire a Workday, Ashby, Oracle or Greenhouse row: those hosts all
    answer 200 for a job that no longer exists, so the dead-link sweep never
    marks one closed and such a row would otherwise sit on the board forever.

    `silent_boards` holds the (company, ats) pairs from `long_silent_boards`.
    Their rows retire at once: the board has held nothing for longer than
    VANISHED_DAYS, so no posting behind it is still live.

    Guardrails, all in the keep direction:
      * only companies that returned at least one posting this run are judged,
        so a broken slug, a failed fetch, or a `--board`/`--limit` subset can
        never retire anything it did not actually look at;
      * a board whose sweep was cut short (postings flagged `partial_sweep`,
        from a page cap or a failed page) retires nothing on absence alone,
        since the missing req may sit past the cut. A Workday row there is
        asked about directly through `probe`, and only a 'gone' answer counts
        as a miss; the big tenants (CVS, RTX, Northrop) cut short every run;
      * a company in `failed_companies` had a board fail or crash this run, so
        all its rows are treated as behind a partial sweep. Idaho National
        Laboratory has two Oracle sites, and one failing while the other
        answered used to stamp the failed site's rows missing;
      * a row must be missing for VANISHED_DAYS before it goes, so one partial
        fetch that went unnoticed costs a re-check rather than the listings;
      * Community rows carry a maintainer's judgment and never appear in
        `raw_jobs`, so they are exempt, as are rows with no fingerprint.

    Retirement writes exactly what a dead link writes, blank url plus
    `closed`, so the existing revive path self-heals a false positive and
    `purge_stale_listings` does the eventual removal.
    """
    live, healthy = set(), set()
    for job in raw_jobs:
        company = job.get('company', '')
        healthy.add(company)
        fingerprint = job_fingerprint(company, job.get('board', ''), job.get('url', ''))
        if fingerprint:
            live.add(fingerprint)
    partial = _partial_boards(raw_jobs)

    retired = []
    for entry in listings:
        if (entry.get('source') == 'Community' or entry.get('closed')
                or not entry.get('url')):
            continue
        company, source = entry.get('company', ''), entry.get('source', '')
        if (company, source.lower()) in silent_boards:
            _retire(entry, today)
            retired.append(entry)
            continue
        if company not in healthy:
            continue
        fingerprint = job_fingerprint(company, source, entry.get('url', ''))
        if fingerprint is None:
            continue
        if fingerprint in live:
            # Seen again: drop any half-finished streak so a req that flickers
            # out of one page never accumulates its way to retirement.
            entry.pop('missing_since', None)
            continue
        if (company, source) in partial or company in failed_companies:
            state = probe(entry['url']) if source == 'Workday' else None
            if state == 'live':
                entry.pop('missing_since', None)
            if state != 'gone':
                continue
        first_missed = entry.get('missing_since')
        if not first_missed:
            entry['missing_since'] = today
            continue
        if _days_since(first_missed, today) < VANISHED_DAYS:
            continue
        _retire(entry, today)
        retired.append(entry)
    return retired


# Sources whose rows come from one companies.yml entry each. Amazon Jobs is a
# hardcoded scraper with no entry, so its rows are never orphans.
CONFIGURED_SOURCES = {'greenhouse', 'lever', 'ashby', 'smartrecruiters', 'workable',
                      'recruitee', 'pinpoint', 'workday', 'oracle', 'eightfold', 'phenom',
                      'jibe'}


def _req_identity(source, url):
    # A fingerprint without its company, plus the URL host so Workday req ids,
    # which only count within one tenant, cannot match across employers.
    fingerprint = job_fingerprint('', source, url)
    if fingerprint is None:
        return None
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return None
    return (source, fingerprint[2], host)


def retire_orphaned_listings(listings, config, today, raw_jobs=()):
    """Close open rows whose company no longer has a board for their source.

    Mutates `listings` and returns (retired, renamed). `renamed` holds
    (row, old_company) pairs. `retire_vanished_listings` only judges boards
    this run scraped, and `long_silent_boards` only boards still in the
    baseline, so a row outlives the removal of its board: Todyl 'Site
    Reliability Engineer II' stayed open after 4e80f86 dropped Todyl's Ashby
    entry, since the job page still answers 200.

    A company renamed in companies.yml orphans its rows by name while their
    reqs are still live. An open row whose req is in this run's `raw_jobs`
    under another company takes that company's name instead of retiring, so
    the insert pass does not add the same req again beside a closed copy.

    Guardrails, all in the keep direction:
      * Community rows and any source outside the config-driven ATSs (Amazon
        Jobs, or a new hardcoded scraper) are never judged;
      * a config with no boards at all retires nothing, so an empty or
        truncated companies.yml cannot close the board.
    """
    sources = CONFIGURED_SOURCES | {key.lower() for key in config}
    configured = {(str(entry.get('name', '')).casefold(), kind.lower())
                  for kind, entries in config.items() if isinstance(entries, list)
                  for entry in entries if isinstance(entry, dict)}
    if not configured:
        return [], []
    live_owner = None
    retired, renamed = [], []
    for entry in listings:
        source = (entry.get('source') or '').lower()
        if (source not in sources or entry.get('source') == 'Community'
                or entry.get('closed') or not entry.get('url')):
            continue
        if (entry.get('company', '').casefold(), source) in configured:
            continue
        if live_owner is None:
            live_owner = {}
            for job in raw_jobs:
                identity = _req_identity(job.get('board', ''), job.get('url', ''))
                if identity and job.get('company'):
                    live_owner.setdefault(identity, job['company'])
        owner = live_owner.get(_req_identity(entry.get('source', ''), entry['url']))
        if owner and owner != entry.get('company'):
            renamed.append((entry, entry.get('company', '')))
            entry['company'] = owner
            entry.pop('missing_since', None)
            continue
        _retire(entry, today)
        retired.append(entry)
    return retired, renamed


def _retire(entry, today):
    entry['url'] = ''
    entry['closed'] = True
    entry.setdefault('closed_date', today)
    entry.pop('missing_since', None)


def _location_is_broken(location):
    # Judged per part: is_us_location accepts "Az; Remote (US)" on the strength
    # of its second part, which would leave the wrecked first part on the board.
    # A part the normalizer would decay to something non-US ("Ma, US" -> "Ma")
    # is broken too, so this does not depend on renormalize_locations having
    # run first.
    parts = [p for p in re.split(r'[;|]', location or '') if p.strip()]
    return not parts or any(
        not is_us_location(p) or not is_us_location(normalize_location(p)) for p in parts)


def repair_broken_locations(listings, raw_jobs):
    """Re-derive a stored location that no longer reads as a US one.

    Returns (kept, repaired, folded). `repaired` rows carry their new location
    and come back as (entry, before, after). `folded` rows were removed from
    `kept`: the repair would have given them the (company, role, location) key
    another live row already holds, the duplicate the insert path's dedup
    exists to refuse (RTX posted "Systems Engineer I, V&V Testing" twice at
    Aguadilla, one copy wrecked to "Pr").

    `renormalize_locations` re-runs the normalizer over the string a row already
    holds, which cannot help a row the normalizer itself destroyed:
    "US-AZ-TUCSON-805 ~ 1151 E Hermans Rd" collapsed to "Az" before #18 repaired
    the country-prefix bug, and no later pass recovers Tucson from two letters.
    Such a row also fails the US-only filter, so no other pass can reach it.
    Every run already fetches the live posting, so take the location from there.

    Guardrails follow `retire_vanished_listings`: Community rows carry a
    maintainer's judgment, and a row is matched to its requisition by
    fingerprint so an unfamiliar URL shape is left alone. A live location is
    accepted only when its RAW string passes `is_us_location`, the gate
    ingestion applies; the normalizer is not a gate on its own, since it turns
    "Remote - India (US business hours)" into "Remote (US)", and a req that
    moved abroad must not be rewritten onto a US-only board. Rows whose every
    stored part still reads as US are never touched, so a board that reorders a
    multi-location req cannot churn the file run after run.
    """
    candidates = []
    for entry in listings:
        if entry.get('source') == 'Community':
            continue
        if not _location_is_broken(entry.get('location', '')):
            continue
        fingerprint = job_fingerprint(entry.get('company', ''), entry.get('source', ''),
                                      entry.get('url', ''))
        if fingerprint:
            candidates.append((entry, fingerprint))
    if not candidates:
        return listings, [], []

    # Index only the companies that have a candidate: raw_jobs holds ~45k
    # postings and almost every run has nothing to repair.
    wanted = {entry.get('company', '') for entry, _ in candidates}
    live = {}
    for job in raw_jobs:
        if job.get('company', '') not in wanted:
            continue
        fingerprint = job_fingerprint(job.get('company', ''), job.get('board', ''),
                                      job.get('url', ''))
        if fingerprint:
            live.setdefault(fingerprint, job.get('location', '') or '')

    held = {listing_dedup_key(e.get('company', ''), e.get('role', ''), e.get('location', ''))
            for e in listings}
    repaired, folded = [], []
    for entry, fingerprint in candidates:
        raw = live.get(fingerprint, '')
        after = normalize_location(raw)
        if not is_us_location(raw) or not is_us_location(after):
            continue
        before = entry.get('location', '')
        if after == before:
            continue
        key = listing_dedup_key(entry.get('company', ''), entry.get('role', ''), after)
        if key in held:
            folded.append(entry)
            continue
        held.add(key)
        entry['location'] = after
        repaired.append((entry, before, after))
    gone = {id(e) for e in folded}
    kept = [e for e in listings if id(e) not in gone]
    return kept, repaired, folded


def _days_since(stamp, today):
    """Whole days from `stamp` to `today`, or 0 if either date is unreadable.

    0 keeps an unparseable stamp below every threshold, so bad data delays a
    retirement instead of forcing one.
    """
    try:
        start = datetime.strptime(stamp, '%Y-%m-%d').date()
        end = datetime.strptime(today, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return 0
    return (end - start).days


def _amazon_description(job):
    """Join Amazon's split description fields into one gate-readable body.

    amazon.jobs keeps the years-of-experience bars in `basic_qualifications`
    and `preferred_qualifications`, NOT in `description` — so reading only
    `description` made every Amazon req look like it stated no experience
    floor at all (issue #11). The headings are emitted verbatim because the
    classifier keys off them to tell required bars from preferred ones.
    """
    parts = [job.get('description', '') or '']
    for field, heading in (('basic_qualifications', 'BASIC QUALIFICATIONS'),
                           ('preferred_qualifications', 'PREFERRED QUALIFICATIONS')):
        text = job.get(field) or ''
        if text.strip():
            parts.append(f'{heading}\n{text}')
    return '\n\n'.join(p for p in parts if p.strip())


def scrape_amazon():
    base_url = 'https://www.amazon.jobs/en/search.json'
    params = {
        'base_query': 'security engineer OR "security analyst" OR cybersecurity',
        'loc_query': 'united states',
        # loc_query only ranks: without this filter a page held GBR, AUS, IND
        # and SGP reqs next to the US ones.
        'normalized_country_code[]': 'USA',
        'result_limit': 100,
        'offset': 0,
    }
    jobs = []
    # Cleared on a failed page or the page cap, as in scrape_smartrecruiters.
    complete = True
    for _page in range(MAX_PAGES):
        data = fetch_json(base_url, params=params, label='Amazon')
        if data is None:
            complete = False
            break
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
                'description': _amazon_description(job),
            })
        total = data.get('hits')
        params['offset'] += len(postings)
        if (len(postings) < params['result_limit']
                or (total is not None and params['offset'] >= total)):
            break
        time.sleep(0.5)
    else:
        complete = False

    if not complete and not jobs:
        return None
    if not complete:
        for job in jobs:
            job['partial_sweep'] = True
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


# A board that yields nothing for this many consecutive runs is reported as
# likely dead. The scrape runs twice a day, so this is ~3 days of silence:
# long enough to ride out a genuine hiring pause at a small vendor, short
# enough that a typo'd slug surfaces the week it lands.
ZERO_RUN_ALERT = 6


def load_board_baseline():
    """Return {label: {count, zero_runs, last_nonzero}}, migrating the old shape.

    The file used to hold a bare {label: count}. Those entries carry no history,
    so a board already sitting at zero starts its streak from this run rather
    than pretending to know how long it has been silent.
    """
    if not BOARD_BASELINE_FILE.exists():
        return {}
    try:
        data = json.loads(BOARD_BASELINE_FILE.read_text())
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    history = {}
    for label, value in data.items():
        if isinstance(value, dict):
            history[label] = {
                'count': value.get('count') or 0,
                'zero_runs': value.get('zero_runs') or 0,
                'last_nonzero': value.get('last_nonzero'),
            }
            if isinstance(value.get('empty_runs'), int):
                history[label]['empty_runs'] = value['empty_runs']
        elif isinstance(value, int):
            history[label] = {'count': value, 'zero_runs': 0, 'last_nonzero': None}
    return history


# Oracle labels gained the site once Idaho National Laboratory put two sites
# on one host; a board keeps the history stored under its old label.
_ORACLE_LABEL_RE = re.compile(r'^(.* \(oracle/[^/()]+)/[^/()]+\)$')


def _legacy_label(label):
    m = _ORACLE_LABEL_RE.match(label)
    return f'{m.group(1)})' if m else None


def board_health(board_stats, baseline, today):
    """Fold this run's counts into the stored per-board history.

    Returns (history, regressed, dead): `regressed` boards produced postings
    last run and none this one; `dead` boards have been silent for
    ZERO_RUN_ALERT consecutive runs. `zero_runs` counts every run without
    postings, failures included, since a board that keeps failing still needs
    triage and health_check.py reads it; `empty_runs` counts only real empty
    answers and drives `long_silent_boards`.

    The count-only baseline could report the first case but never the second.
    It overwrote the previous count with 0, so a board that broke warned on
    exactly one run and then compared 0 > 0 forever after — and a board that
    was mis-configured from the day it was added never warned at all. Both
    shapes of breakage are invisible until someone diffs the baseline by hand,
    which is how nine boards stayed broken long enough to need repairing in
    bulk (#14). Keeping the streak makes the warning stick until it is fixed.
    """
    history, regressed, dead = {}, [], []
    for b in board_stats:
        label = b['label']
        prev = baseline.get(label) or baseline.get(_legacy_label(label)) or {}
        if b['count'] > 0:
            history[label] = {'count': b['count'], 'zero_runs': 0, 'empty_runs': 0,
                              'last_nonzero': today}
            continue
        streak = prev.get('zero_runs', 0) + 1
        # A failed fetch says nothing about whether the board is empty, so it
        # holds the silent streak rather than growing or resetting it.
        empty = _empty_runs(prev) + (b['status'] == 'zero')
        history[label] = {'count': 0, 'zero_runs': streak, 'empty_runs': empty,
                          'last_nonzero': prev.get('last_nonzero')}
        if prev.get('count', 0) > 0:
            regressed.append((label, prev['count']))
        if streak >= ZERO_RUN_ALERT:
            dead.append((label, streak, prev.get('last_nonzero')))
    dead.sort(key=lambda d: (-d[1], d[0]))
    return history, regressed, dead


def report_board_health(board_stats, today=None, persist=True):
    """Print a run summary, emit GitHub annotations, roll the board history.

    Breakage is otherwise invisible: a dead board looks identical to one with
    no new cyber jobs. Two annotations cover it — a board that returned
    postings last run and none this one (a fresh broken slug / ATS drift), and
    a board that has been silent for ZERO_RUN_ALERT runs, which keeps being
    reported until someone fixes or drops it.
    """
    today = today or datetime.now().strftime('%Y-%m-%d')
    ok = sum(1 for b in board_stats if b['status'] == 'ok')
    zero = [b for b in board_stats if b['status'] == 'zero']
    broken = [b for b in board_stats if b['status'] in ('FAILED', 'CRASHED')]
    total_raw = sum(b['count'] for b in board_stats)

    history, regressed, dead = board_health(board_stats, load_board_baseline(), today)
    for label, was in regressed:
        print(f'::warning::[{label}] returned 0 postings but had {was} last run '
              f'(broken slug or ATS drift?)')
    if dead:
        # One aggregated annotation, not one per board: a long-neglected config
        # can hold dozens, and 40 warnings bury the regression above them.
        print(f'::warning::{len(dead)} board(s) have returned 0 postings for '
              f'{ZERO_RUN_ALERT}+ consecutive runs — see the run summary')

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
        lines.append('- ⚠️ Regressed to zero: '
                     + ', '.join(label for label, _ in regressed))
    if dead:
        lines += ['', f'<details><summary>💀 Silent for {ZERO_RUN_ALERT}+ runs '
                      f'({len(dead)})</summary>', '']
        lines += [f'- `{label}` — {runs} runs, '
                  + (f'last postings {last}' if last else 'no postings on record')
                  for label, runs, last in dead]
        lines += ['', '</details>']
    summary = '\n'.join(lines)
    print('\n' + summary)

    step_summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if step_summary:
        with open(step_summary, 'a') as f:
            f.write(summary + '\n')

    if persist:
        BOARD_BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        BOARD_BASELINE_FILE.write_text(
            json.dumps(history, indent=2, sort_keys=True))
        HEALTH_FILE.write_text(json.dumps({
            'date': today,
            'boards': len(board_stats),
            'ok': ok,
            'empty': len(zero),
            'failed': sorted(b['label'] for b in broken),
            'regressed': [{'board': label, 'had': was} for label, was in regressed],
            'silent': len(dead),
        }, indent=2) + '\n')


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
# Orchestration
# ---------------------------------------------------------------------------

SIMPLE_BOARDS = {
    'greenhouse': scrape_greenhouse,
    'lever': scrape_lever,
    'ashby': scrape_ashby,
    'smartrecruiters': scrape_smartrecruiters,
    'workable': scrape_workable,
    'recruitee': scrape_recruitee,
    'pinpoint': scrape_pinpoint,
}
# Simple boards whose scraper fetches descriptions for title-level candidates,
# which is_cyber_title judges under the company's security_company flag.
FLAGGED_SIMPLE_BOARDS = {'smartrecruiters'}


class BoardTask(NamedTuple):
    """One configured board: a scraper plus the positional args it takes."""
    label: str
    scraper: Callable[..., list[dict] | None]
    args: tuple
    security_company: bool = False


def build_tasks(config, board=None, limit=None):
    """Turn companies.yml into `BoardTask`s in config order, honoring `--board`/`--limit`."""
    def want(name):
        return not board or board == name

    def limited(entries):
        entries = entries or []
        return entries[:limit] if limit else entries

    tasks = []
    for name, scraper in SIMPLE_BOARDS.items():
        if not want(name):
            continue
        for entry in limited(config.get(name)):
            flag = entry.get('security_company', False)
            args = (entry['name'], entry['slug'])
            if name in FLAGGED_SIMPLE_BOARDS:
                args += (flag,)
            tasks.append(BoardTask(
                f'{entry["name"]} ({name}/{entry["slug"]})', scraper, args, flag))
    if want('workday'):
        for entry in limited(config.get('workday')):
            tasks.append(BoardTask(
                f'{entry["name"]} (workday/{entry["tenant"]})', scrape_workday,
                (entry['name'], entry['tenant'], entry['instance'], entry.get('board', ''),
                 entry.get('security_company', False), entry.get('search_terms')),
                entry.get('security_company', False)))
    if want('oracle'):
        for entry in limited(config.get('oracle')):
            tasks.append(BoardTask(
                f'{entry["name"]} (oracle/{entry["host"]}/{entry["site"]})', scrape_oracle,
                (entry['name'], entry['host'], entry['site'],
                 entry.get('security_company', False)),
                entry.get('security_company', False)))
    if want('eightfold'):
        for entry in limited(config.get('eightfold')):
            tasks.append(BoardTask(
                f'{entry["name"]} (eightfold/{entry["tenant"]})', scrape_eightfold,
                (entry['name'], entry['tenant'], entry['domain'],
                 entry.get('security_company', False), entry.get('search_terms')),
                entry.get('security_company', False)))
    if want('phenom'):
        for entry in limited(config.get('phenom')):
            tasks.append(BoardTask(
                f'{entry["name"]} (phenom/{entry["host"]})', scrape_phenom,
                (entry['name'], entry['host'], entry['lang'], entry['country'],
                 entry.get('security_company', False), entry.get('search_terms')),
                entry.get('security_company', False)))
    if want('jibe'):
        for entry in limited(config.get('jibe')):
            tasks.append(BoardTask(
                f'{entry["name"]} (jibe/{entry["host"]})', scrape_jibe,
                (entry['name'], entry['host'], entry.get('search_terms')),
                entry.get('security_company', False)))
    if want('amazon'):
        tasks.append(BoardTask('Amazon (amazon.jobs)', scrape_amazon, ()))
    return tasks


def run_board(task):
    """Scrape one board and report its status, count, and postings; never raises."""
    started = time.monotonic()
    status, jobs = 'ok', []
    try:
        found = task.scraper(*task.args)
    except Exception as e:
        # One misbehaving board must not take the rest of the run down with it;
        # the summary reports it as CRASHED and the baseline check flags a
        # board that stays broken.
        print(f'  [{task.label}] Scraper crashed: {_oneline(e)}')
        status = 'CRASHED'
    else:
        if found is None:
            status = 'FAILED'
        else:
            jobs = found
            status = 'ok' if jobs else 'zero'
    return {
        'label': task.label,
        'status': status,
        'count': len(jobs),
        'jobs': jobs,
        'security_company': task.security_company,
        'seconds': time.monotonic() - started,
    }


def scrape_boards(tasks, workers=SCRAPE_WORKERS):
    """Run every board on a thread pool, yielding results in `tasks` order.

    Config order (not completion order) keeps `raw_jobs` and the dedup passes
    downstream on the same sequence a sequential sweep produced, and keeps the
    log readable run to run. Results stream as boards finish, so a stalled run
    still shows how far it got.
    """
    with ThreadPoolExecutor(max_workers=workers) as pool:
        yield from pool.map(run_board, tasks)


def insert_new_listings(listings, raw_jobs, seen, sec_flags, today):
    """Add each accepted posting that no row holds yet, and revive blanked rows.

    Mutates `listings` and `seen` and returns (added_rows, revived_rows).
    `sec_flags` maps a posting id to its board's `security_company` flag.

    A seen posting is skipped only while a row already holds its normalized
    URL, dedup key or fingerprint. Skipping every seen id, as this once did,
    meant a row that the stored-row pass dropped or the orphan pass retired
    never came back while its posting stayed live, since each run re-stamped
    the id. Such a posting goes back through `evaluate_job` instead. That is
    stable: a row the re-eval pass drops this run fails the same gates here,
    since `evaluate_job` is `judge_job` without the intern and flag leniency
    the re-eval pass adds, and a row the repair pass folded carries the key
    of the row it folded into.
    """
    existing_urls = {normalize_url(e.get('url', '')) for e in listings if e.get('url')}
    # Secondary key catches the same role reposted per-location under distinct
    # req-ID URLs (e.g. one "Intern - Software Engineer" ×10) that URL dedup
    # can't see. Location stays in the key so genuinely different sites remain
    # separate rows.
    existing_keys = {listing_dedup_key(e.get('company', ''), e.get('role', ''),
                                       e.get('location', '')) for e in listings}
    existing_fps = {job_fingerprint(e.get('company', ''), e.get('source', ''), e['url'])
                    for e in listings if e.get('url')} - {None}
    # Rows a dead-link sweep blanked; a still-live posting revives them so a
    # 403/transient false positive self-heals instead of staying 🔒 forever.
    blanked = {listing_dedup_key(e.get('company', ''), e.get('role', ''),
                                 e.get('location', '')): e
               for e in listings if not e.get('url')}
    added_rows = []
    revived_rows = []

    for job in raw_jobs:
        jid = job['id']
        # seen_jobs.json holds Oracle reqs under their pre-host id; carrying
        # the date over keeps a known req from a second trip through the gates.
        legacy = job.get('legacy_id')
        if jid not in seen and legacy in seen:
            seen[jid] = seen[legacy]
        url = job.get('url', '')
        location = normalize_location(job.get('location', ''))
        key = listing_dedup_key(job['company'], job.get('title', ''), location)
        # The fingerprint catches a Workday req whose URL and location both
        # moved, which neither the URL nor the key can see.
        fingerprint = job_fingerprint(job['company'], job.get('board', ''), url)
        on_board = ((url and normalize_url(url) in existing_urls) or key in existing_keys
                    or (fingerprint and fingerprint in existing_fps))
        if jid in seen and on_board and key not in blanked:
            continue
        verdict = evaluate_job(
            job.get('title', ''), job.get('location', ''),
            job.get('description', ''), sec_flags.get(jid, False),
            job.get('intern_hint', False),
        )
        if verdict is None:
            continue
        level, category = verdict

        if not url:
            # Don't record a URL-less posting as seen — otherwise it's skipped
            # forever even after the ATS later populates the URL.
            continue
        seen[jid] = today
        if key in blanked:
            row = blanked.pop(key)
            row['url'] = url
            # A company that moved ATS revives on its new board; a stale source
            # left the row orphaned again on the next run, RETIRED then REVIVED
            # every run. A Community row keeps the maintainer's provenance.
            if row.get('source') != 'Community' and job.get('board'):
                row['source'] = job['board']
            row.pop('closed', None)
            row.pop('closed_date', None)
            # Clear the absence streak too: a revived row that later vanishes
            # again must earn a fresh VANISHED_DAYS grace period, not inherit a
            # months-old stamp and retire on the next run.
            row.pop('missing_since', None)
            existing_urls.add(normalize_url(url))
            if fingerprint:
                existing_fps.add(fingerprint)
            revived_rows.append(row)
            print(f'  REVIVED {_oneline(job["company"])} — {_oneline(job["title"])}')
            continue
        if on_board:
            continue
        existing_urls.add(normalize_url(url))
        existing_keys.add(key)
        if fingerprint:
            existing_fps.add(fingerprint)

        row = {
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
        }
        listings.append(row)
        added_rows.append(row)
        print(f'  NEW [{level}] {_oneline(job["company"])} — {_oneline(job["title"])} '
              f'@ {_oneline(job.get("location", ""))}')
    return added_rows, revived_rows


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
                             'eightfold, phenom, jibe, amazon) for fast '
                             'local iteration')
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
    # Read from the whole config, not this run's tasks, so a --board run still
    # judges every stored row against its company's current flag.
    company_flags = security_company_flags(config)
    board_stats = []

    tasks = build_tasks(config, board=args.board, limit=args.limit)
    started = time.monotonic()
    for result in scrape_boards(tasks):
        print(f'Checking {result["label"]}... {result["status"]} '
              f'({result["count"]} postings, {result["seconds"]:.1f}s)')
        for job in result['jobs']:
            sec_flags[job['id']] = result['security_company']
            # Amazon has no companies.yml entry to read.
            company_flags.setdefault(job['company'], result['security_company'])
        raw_jobs.extend(result['jobs'])
        board_stats.append({key: result[key] for key in ('label', 'status', 'count')})
    print(f'\nScraped {len(tasks)} board(s) in {time.monotonic() - started:.0f}s')

    # Only a full run may roll the baseline — a --board/--limit run holds counts
    # for a subset and would blind the zero-regression check for the rest.
    full_run = not args.dry_run and not args.board and not args.limit
    today = datetime.now().strftime('%Y-%m-%d')
    report_board_health(board_stats, today, persist=full_run)
    print(f'\nScraped {len(raw_jobs)} raw postings; filtering...')

    listings = load_listings()

    # Drop long-closed rows so the board doesn't accumulate dead postings.
    listings, purged = purge_stale_listings(listings, today)
    if purged:
        print(f'Purged {purged} stale closed listing(s)')

    # Let normalizer improvements reach already-scraped rows; a location is
    # otherwise only normalized once, when the row lands.
    renormalized = renormalize_locations(listings)
    if renormalized:
        print(f'Renormalized {renormalized} location(s)')

    # Let classifier improvements reach already-scraped listings (title-only).
    listings, reclass_changes, rejected = reclassify_listings(listings, company_flags)
    for company, role, old, new in reclass_changes:
        print(f'  RECLASSIFY [{old} -> {new}] {company} — {role}')
    reclassified = len(reclass_changes)

    # Re-judge each row against its live posting with the full pipeline, so a
    # description, location or flag rule reaches rows inserted before it: a
    # pre-gate "Engineer II @ 6 yrs" row would otherwise sit here forever
    # (issue #11), as would a category or 🇺🇸 flag set at insert.
    # A company with any failed or crashed board is judged like a partial sweep.
    failed_companies = failed_board_companies(board_stats)
    # The re-eval and vanished passes both ask Workday about the same missed
    # rows; one detail request per row per run serves both.
    probed = {}

    def probe(url):
        if url not in probed:
            probed[url] = workday_posting(url)
        return probed[url]

    listings, reevaluated, refreshed = reevaluate_stored_listings(
        listings, raw_jobs, company_flags, probe=probe, failed_companies=failed_companies)
    drops = rejected + reevaluated
    for entry, reason in drops:
        print(f'  DROP [{reason}] {_oneline(entry.get("company", ""))} — '
              f'{_oneline(entry.get("role", ""))}')
    for entry, field, old, new in refreshed:
        label = f'{_oneline(entry.get("company", ""))} — {_oneline(entry.get("role", ""))}'
        if field == 'type':
            print(f'  RECLASSIFY [{old} -> {new}] {label}')
        else:
            print(f'  REFRESHED [{field}] {label}: {old!r} -> {new!r}')
    relevelled = sum(1 for _, field, _, _ in refreshed if field == 'type')
    reclassified += relevelled
    drop_counts = Counter(reason for _, reason in drops)

    # A location the normalizer destroyed cannot be recovered by normalizing it
    # again, so re-read it off the live posting. Runs after the drop passes so a
    # row leaving the board this run is not logged as repaired first.
    listings, repaired, folded = repair_broken_locations(listings, raw_jobs)
    for entry, before, after in repaired:
        print(f'  REPAIRED [location] {_oneline(entry.get("company", ""))} — '
              f'{_oneline(entry.get("role", ""))}: {before!r} -> {after!r}')
    for entry in folded:
        print(f'  DROP [duplicate-after-repair] {_oneline(entry.get("company", ""))} — '
              f'{_oneline(entry.get("role", ""))}')

    # Retire rows whose req has left its board's feed. Nothing else can retire a
    # Workday/Ashby/Oracle/Greenhouse row: those hosts answer 200 for a job that
    # no longer exists, so check_links.py never sees one die.
    # On a full run report_board_health has already rolled this run into the
    # baseline; a dry or partial run reads it one run behind, which only keeps.
    vanished = retire_vanished_listings(
        listings, raw_jobs, today, long_silent_boards(board_stats, load_board_baseline()),
        probe=lambda url: probe(url)[0], failed_companies=failed_companies)
    for entry in vanished:
        print(f'  RETIRED [vanished] {_oneline(entry.get("company", ""))} — '
              f'{_oneline(entry.get("role", ""))}')
    orphaned, renamed = retire_orphaned_listings(listings, config, today, raw_jobs)
    for entry in orphaned:
        print(f'  RETIRED [orphaned] {_oneline(entry.get("company", ""))} — '
              f'{_oneline(entry.get("role", ""))}')
    for entry, old in renamed:
        print(f'  RENAMED [company] {_oneline(old)} -> {_oneline(entry.get("company", ""))} — '
              f'{_oneline(entry.get("role", ""))}')

    added_rows, revived_rows = insert_new_listings(listings, raw_jobs, seen, sec_flags, today)

    # Refresh last-seen for every still-live id, then expire the stale ones.
    for job in raw_jobs:
        if job['id'] in seen:
            seen[job['id']] = today
    seen = prune_seen(seen, today)

    added, revived = len(added_rows), len(revived_rows)

    # `missing_since` stamps land on rows that stay, so a run that only starts a
    # streak still has to save listings.json or the streak resets every run.
    pending = sum(1 for e in listings if e.get('missing_since'))
    changed = (added or reclassified or revived or purged or drops or refreshed
               or renormalized or repaired or folded or vanished or orphaned or renamed
               or pending)
    dropped_by = ', '.join(f'{n} {reason}' for reason, n in sorted(drop_counts.items()))
    print(f'\nAdded {added} new listing(s), revived {revived}, '
          f'reclassified {reclassified}, purged {purged}, '
          f'repaired {len(repaired)} location(s) + folded {len(folded)} duplicate(s), '
          f'retired {len(vanished)} vanished ({pending} more missing) + {len(orphaned)} orphaned, '
          f'dropped {len(drops)} ({dropped_by or "none"}), '
          f'refreshed {len(refreshed) - relevelled} category or clearance field(s)')

    if args.dry_run:
        print('[dry-run] no files written; skipping README rebuild')
        print('Done')
        return

    if changed:
        save_listings(listings)
        rebuild_readme.main()

    save_seen_jobs(seen)
    events_file = os.environ.get('RUN_EVENTS_FILE')
    if events_file:
        write_run_events(events_file, added_rows, revived_rows, vanished + orphaned)
    print('Done')


if __name__ == '__main__':
    main()
