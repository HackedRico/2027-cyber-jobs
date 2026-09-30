#!/usr/bin/env python3
"""Shared helpers used by more than one script.

Kept dependency-free (stdlib only) so the classification test suite and the
scraper both import the SAME url/issue logic. The URL dedup guard only works
if the scraper and the community-submission flow normalize identically.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from classify import DC_SPELLING_RE, REGION_CODE_RE, US_STATES, normalize_location

# Tracking params stripped before URL comparison so the same posting under
# different campaign tags dedupes to one listing.
STRIP_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term',
    'utm_id', 'source', 'src', 'ref', 'referer', 'lever-source',
    'lever-origin', 'gh_src',
}

# Greenhouse serves the same board under two hostnames; canonicalize so a job
# under both doesn't dedupe as two.
_GREENHOUSE_HOST_RE = re.compile(r'^job-boards\.greenhouse\.io$')
# Trailing application-step suffixes that don't change the posting identity.
_TRAILING_STEP_RE = re.compile(r'/(?:apply|application)$', re.IGNORECASE)


def normalize_url(url):
    try:
        p = urlparse(url.strip())
        params = {k: v for k, v in parse_qs(p.query, keep_blank_values=True).items()
                  if k.lower() not in STRIP_PARAMS}
        netloc = _GREENHOUSE_HOST_RE.sub('boards.greenhouse.io', p.netloc.lower())
        path = _TRAILING_STEP_RE.sub('', p.path.rstrip('/'))
        u = urlunparse(p._replace(
            scheme=p.scheme.lower(),
            netloc=netloc,
            path=path,
            query=urlencode(sorted(params.items()), doseq=True),
            fragment='',
        ))
        # Collapse every segment before /job/, locale or not: a submitter pastes
        # ".../en-US/NW/job/..." while the scraper builds ".../NW/job/...", and
        # requiring the locale left the scraper's form uncollapsed, so the two
        # never matched.
        return re.sub(r'(myworkdayjobs\.com)/(?:[^/]+/)*?job/',
                      r'\1/job/', u, flags=re.IGNORECASE)
    except Exception:
        return url


def gh_headers(token):
    return {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json',
    }


def parse_issue_body(body):
    """Parse a GitHub issue form body into {field header: value}.

    First occurrence of each `### Header` wins, so a free-text field appended
    later in the body cannot override a structured value that already passed
    validation. A missing/null body (GitHub returns body: null for a bodyless
    issue) yields an empty field set rather than raising.
    """
    fields = {}
    for section in re.split(r'^### ', body or '', flags=re.MULTILINE):
        if not section.strip():
            continue
        lines = section.strip().split('\n')
        header = lines[0].strip()
        if header in fields:
            continue
        value = '\n'.join(lines[1:]).strip()
        fields[header] = '' if value == '_No response_' else value
    return fields


REMOTE_RE = re.compile(r'^remote\s*(\(us\)|\(usa\)|\(united states\))?$', re.IGNORECASE)
BARE_COUNTRY_RE = re.compile(r'^(us|usa|united states|nationwide)$', re.IGNORECASE)
CITY_STATE_RE = re.compile(r'^.+,\s*([A-Z]{2})$')
SUBMITTED_SPLIT_RE = re.compile(r'[;\n]')


def _fix_typed_spelling(part):
    # normalize_location reads scraper shapes; a person types "Washington,
    # D.C." and "McLean, Va", which it passes through unchanged.
    part = DC_SPELLING_RE.sub('Washington, DC', part.strip())
    return REGION_CODE_RE.sub(lambda m: f', {m.group(1).upper()}', part)


def normalize_submitted_location(location):
    """Return a form location in the board's "City, ST; Remote (US)" shape.

    Runs the scraper's normalize_location over the whole string, so a foreign
    option beside a US one is dropped the same way it is for scraped rows.
    """
    parts = [_fix_typed_spelling(p) for p in SUBMITTED_SPLIT_RE.split(location or '')
             if p.strip()]
    return normalize_location('; '.join(parts)) or ''


def validate_location(location):
    """Return a list of error strings for a submitted location, empty if valid.

    The location is normalized first, so the spellings the scraper's location
    rules accept for a US place ("Arlington, Virginia", "Arlington VA",
    "Washington, D.C.") pass here too.
    """
    parts = [p.strip() for p in normalize_submitted_location(location).split(';')
             if p.strip()]
    if not parts:
        return ['location is empty']
    errors = []
    for part in parts:
        if REMOTE_RE.match(part) or BARE_COUNTRY_RE.match(part):
            continue
        m = CITY_STATE_RE.match(part)
        if not m:
            errors.append(
                f'`{part}`: use "City, ST" format (e.g. "Arlington, VA") '
                f'or "Remote (US)"'
            )
        elif m.group(1) not in US_STATES:
            errors.append(
                f'`{part}`: `{m.group(1)}` is not a US state code. '
                f'This board is US-only.'
            )
    return errors


def security_company_names(config):
    """Lowercased names of companies.yml entries flagged `security_company`.

    Takes the parsed config so this module stays stdlib-only.
    """
    names = set()
    for entries in (config or {}).values():
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get('security_company'):
                names.add(str(entry.get('name', '')).strip().lower())
    return names


def security_company_flags(config):
    """Map each companies.yml entry's name to its `security_company` flag.

    A company listed under two ATSes counts as a security company when either
    entry says so. Names keep their case, since they must equal the `company`
    field on stored rows.
    """
    flags = {}
    for entries in (config or {}).values():
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get('name'):
                name = str(entry['name'])
                flags[name] = flags.get(name, False) or bool(entry.get('security_company'))
    return flags


def write_run_events(path, added, revived=(), retired=()):
    """Write one run's inserted, revived and retired rows for notify.py.

    Only the writer knows which rows are new: diffing listings.json
    over-reports whenever renormalisation rewrites a location or a closure
    blanks a url. The scrape and the community path both write this shape.
    """
    events = {
        'schema_version': 1,
        'run_at': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'added': list(added),
        'revived': list(revived),
        'retired': list(retired),
    }
    Path(path).write_text(json.dumps(events, indent=2))
