#!/usr/bin/env python3
"""Shared helpers used by more than one script.

Kept dependency-free (stdlib only) so the classification test suite and the
scraper both import the SAME url/issue logic — the URL dedup guard only works
if the scraper and the community-submission flow normalize identically.
"""

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from classify import US_STATES

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
        # Collapse Workday locale + any board segment(s) before /job/ so
        # ".../en-US/External/job/..." and ".../job/..." match. Case-insensitive
        # locale (en-US, en-us) and one-or-more pre-/job/ segments.
        return re.sub(r'(myworkdayjobs\.com)/[a-z]{2}-[a-z]{2}/(?:[^/]+/)*?job/',
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


def validate_location(location):
    """Return a list of error strings for a submitted location, empty if valid."""
    parts = [p.strip() for p in re.split(r'[;\n]', location) if p.strip()]
    if not parts:
        return ['location is empty']
    errors = []
    for part in parts:
        if REMOTE_RE.match(part) or BARE_COUNTRY_RE.match(part):
            continue
        m = CITY_STATE_RE.match(part)
        if not m:
            errors.append(
                f'`{part}` — use "City, ST" format (e.g. "Arlington, VA") '
                f'or "Remote (US)"'
            )
            continue
        if m.group(1) not in US_STATES:
            errors.append(
                f'`{part}` — `{m.group(1)}` is not a US state code. '
                f'This board is US-only.'
            )
    return errors
