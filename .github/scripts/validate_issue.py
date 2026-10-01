#!/usr/bin/env python3
"""Check a community job submission and post one advisory verdict comment.

The comment carries a marker and is edited in place on every issue edit, so a
submitter who fixes the form sees the verdict change instead of a new comment
per edit. The verdict never blocks approval: with only a title and a location,
evaluate_job rejects real student roles a maintainer has vetted (#13).
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter

sys.path.insert(0, str(Path(__file__).parent))
from classify import (  # noqa: E402
    CATEGORY_ALLOWLIST,
    classify_level,
    evaluate_job,
    infer_category,
    is_cyber_title,
    is_us_location,
    listing_dedup_key,
    rejected_title_rule,
)
from common import (  # noqa: E402
    fetch_target_problem,
    gh_headers,
    host_matches,
    link_host,
    md_code,
    md_escape,
    normalize_url,
    oneline,
    parse_issue_body,
    public_address,
    validate_location,
)
from process_approved import fields_to_listing, load_security_companies  # noqa: E402
from rebuild_readme import is_open  # noqa: E402

API = 'https://api.github.com'
MARKER = '<!-- validate-issue -->'
BOT_LOGIN = 'github-actions[bot]'
LISTINGS_FILE = Path('listings.json')

REQUIRED_FIELDS = [
    'Company Name',
    'Role / Job Title',
    'Listing Type',
    'Location',
    'Direct Application Link',
]
LEVEL_LABELS = {
    'intern': 'Internship / Co-op',
    'newgrad': 'New Grad / University Program',
    'earlycareer': 'Early Career / Entry-Level',
}
# The verdict echoes a submitted Listing Type or Category only when it is one
# of the form's options, since an edited issue body can put any markdown in
# either field.
CATEGORY_OPTIONS = CATEGORY_ALLOWLIST | {'Not sure'}
LABEL_VALID = 'valid'
LABEL_FIX = 'needs-fix'
LABELS = {
    LABEL_VALID: ('0e8a16', 'Submission check found no problems'),
    LABEL_FIX: ('d93f0b', 'Submission check found something to fix or review'),
}

LINK_TIMEOUT = 10
MAX_REDIRECTS = 5
# Only these mean the posting is gone. Job boards answer bots with 403 and 429
# while the posting is live, so those are reported but not held against it.
DEAD_STATUSES = {404, 410}

# Applicant tracking systems that host postings for many employers. A link on
# any other host gets a warning unless a row of the same company already uses
# it, since a lookalike domain otherwise read as "no problems found".
ATS_DOMAINS = frozenset({
    'adp.com', 'applytojob.com', 'ashbyhq.com', 'avature.net', 'bamboohr.com',
    'brassring.com', 'breezy.hr', 'careers-page.com', 'csod.com', 'dayforcehcm.com',
    'eightfold.ai', 'gem.com', 'greenhouse.io', 'icims.com', 'jazzhr.com',
    'jibeapply.com', 'jobvite.com', 'lever.co', 'myworkdayjobs.com',
    'myworkdaysite.com', 'oraclecloud.com', 'paycomonline.net', 'paylocity.com',
    'personio.com', 'phenompeople.com', 'pinpointhq.com', 'recruitee.com',
    'sapsf.com', 'smartrecruiters.com', 'successfactors.com', 'successfactors.eu',
    'taleo.net', 'teamtailor.com', 'ultipro.com', 'usajobs.gov', 'workable.com',
})
# parse_issue_body joins a field's lines, and a row's role, company and
# location each render on one line of the board.
ONE_LINE_FIELDS = ('Company Name', 'Role / Job Title')


def form_errors(fields):
    """Problems in the form itself, each a markdown bullet body."""
    errors = [f'**{f}** is missing or empty' for f in REQUIRED_FIELDS
              if not fields.get(f, '').strip()]
    errors += [f'**{f}** must be one line' for f in ONE_LINE_FIELDS
               if re.search(r'[\r\n]', fields.get(f, '').strip())]
    link = fields.get('Direct Application Link', '').strip()
    if link and not re.match(r'^https?://\S+$', link):
        errors.append('**Direct Application Link** must be a single-line URL '
                      'starting with `http://` or `https://`')
    location = fields.get('Location', '').strip()
    if location:
        errors.extend(f'**Location**: {e}' for e in validate_location(location))
    return errors


def charter_gate(title, location, security_company=False):
    """The first evaluate_job gate a title and location fail, or None."""
    if not title:
        return 'there is no job title'
    reason = rejected_title_rule(title)
    if reason:
        return md_escape(reason)
    if not is_cyber_title(title, security_company):
        return 'the title has no cybersecurity keyword'
    if evaluate_job(title, 'Remote (US)', '', security_company) is None:
        return 'the title has no intern, new grad or early-career signal'
    if not is_us_location(location):
        return 'the location is not in the US'
    return None


def find_duplicates(listing, listings):
    """Rows matching by normalized URL or by listing_dedup_key, with the reason."""
    url = normalize_url(listing['url']) if listing['url'] else None
    key = listing_dedup_key(listing['company'], listing['role'], listing['location'])
    matches = []
    for entry in listings:
        if url and entry.get('url') and normalize_url(entry['url']) == url:
            matches.append((entry, 'same link'))
        elif listing_dedup_key(entry.get('company', ''), entry.get('role', ''),
                               entry.get('location', '')) == key:
            matches.append((entry, 'same company, role and location'))
    return matches


class BlockedAddress(requests.ConnectionError):
    """The link's host resolves to an address that is not public."""


class PublicOnlyAdapter(HTTPAdapter):
    """Connect only to a public address, and to the one that was checked.

    Resolving the name for the check and letting urllib3 resolve it again to
    connect leaves a DNS rebinding gap: the second answer can be a metadata
    address. This resolves once, connects to that IP, and keeps the hostname
    for the Host header, SNI and certificate verification. `resolve` takes
    (host, port) and returns an IP string or None, like
    common.public_address.
    """

    def __init__(self, resolve=public_address, **kwargs):
        self._resolve = resolve
        super().__init__(**kwargs)

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        parts = urlsplit(request.url)
        host = parts.hostname or ''
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        try:
            address = self._resolve(host, port)
        except (OSError, UnicodeError) as e:
            raise requests.ConnectionError(f'{host} does not resolve: {e}',
                                           request=request) from e
        if address is None:
            raise BlockedAddress(f'{host} does not resolve to a public address',
                                 request=request)
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(
            request, verify, cert)
        host_params['host'] = address
        if host_params['scheme'] == 'https':
            # urllib3 keys its pools on both, so two names on one IP never
            # share a TLS connection.
            pool_kwargs['server_hostname'] = host
            pool_kwargs['assert_hostname'] = host
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=pool_kwargs)

    def add_headers(self, request, **kwargs):
        # The pool's host is now the IP, which http.client would otherwise
        # send as the Host header.
        request.headers['Host'] = urlsplit(request.url).netloc.rsplit('@', 1)[-1]


def public_session(resolve=public_address):
    """A requests session that only reaches public addresses.

    It ignores proxy settings from the environment, since a proxy would
    resolve the name itself. Callers follow redirects by hand and check each
    hop with common.fetch_target_problem.
    """
    session = requests.Session()
    session.trust_env = False
    adapter = PublicOnlyAdapter(resolve)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


def check_link(url, get=None):
    """GET an http(s) link with a timeout. Returns {'dead': bool, 'summary': str}.

    Redirects are followed by hand so every hop gets the scheme, port and
    public-address checks.
    """
    get = get or public_session().get
    started = time.monotonic()
    for _ in range(MAX_REDIRECTS + 1):
        problem = fetch_target_problem(url)
        if problem:
            return {'dead': True, 'summary': f'not checked: {problem}'}
        try:
            resp = get(url, timeout=LINK_TIMEOUT, allow_redirects=False, stream=True,
                       headers={'User-Agent': 'Mozilla/5.0 (2027-cyber-jobs link check)'})
        except BlockedAddress:
            return {'dead': True, 'summary': f'{md_code(link_host(url))} does not resolve '
                                             'to a public address'}
        except requests.RequestException as e:
            return {'dead': False, 'summary': f'could not connect ({type(e).__name__})'}
        resp.close()
        location = resp.headers.get('Location')
        if resp.status_code in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            continue
        elapsed = time.monotonic() - started
        if resp.status_code in DEAD_STATUSES:
            return {'dead': True, 'summary': f'HTTP {resp.status_code}, the posting looks gone'}
        if resp.status_code >= 400:
            return {'dead': False,
                    'summary': f'HTTP {resp.status_code}, could not confirm the posting'}
        return {'dead': False, 'summary': f'HTTP {resp.status_code} in {elapsed:.1f}s'}
    return {'dead': False, 'summary': f'more than {MAX_REDIRECTS} redirects, not followed'}


def company_hosts(company, listings):
    """Hosts the rows of `company` link to, open or closed, as link_host gives them."""
    key = company.strip().casefold()
    return {link_host(entry.get(field)) for entry in listings
            if str(entry.get('company', '')).strip().casefold() == key
            for field in ('url', 'last_url') if entry.get(field)} - {''}


def domain_warning(url, company, listings):
    """A warning when the link's host is neither an ATS nor one the company uses."""
    host = link_host(url)
    if not host or host_matches(host, ATS_DOMAINS) or host in company_hosts(company, listings):
        return None
    return (f"{md_code(host)} is not a known job-board host or one this company's rows "
            f'use, so check that this domain belongs to {md_escape(company) or "the company"}')


def _option(value, options):
    value = value.strip()
    if not value:
        return 'none'
    return value if value in options else 'a value that is not a form option'


def build_verdict(fields, listings, security_companies=frozenset(), link=None):
    """Return (comment body, ok) for a parsed submission.

    `link` is check_link's result, or None when the link was not fetched.
    `ok` is False when the form has errors, the row is a duplicate, the link
    is dead or on a host `domain_warning` flags, or evaluate_job rejects the
    title and location.
    """
    listing = fields_to_listing(fields, security_companies)
    security_company = listing['company'].lower() in security_companies
    errors = form_errors(fields)
    gate = charter_gate(listing['role'], listing['location'], security_company)
    dupes = find_duplicates(listing, listings) if listing['company'] else []
    # process_approved reopens a closed match rather than rejecting it.
    open_dupes = [entry for entry, _ in dupes if is_open(entry)]
    host = link_host(listing['url'])
    warning = domain_warning(listing['url'], listing['company'], listings)
    ok = (not errors and gate is None and not open_dupes and not warning
          and not (link and link['dead']))

    lines = [MARKER,
             '### Submission check: ' + ('✅ no problems found' if ok else '⚠️ needs a look'),
             '',
             'This check is advisory and updates when you edit the issue. '
             'A maintainer makes the final call and adds the `approved` label.',
             '',
             '**Form**']
    lines += [f'- ❌ {e}' for e in errors] or ['- ✅ Required fields are filled in']
    if listing['location'] and not validate_location(fields.get('Location', '')):
        lines.append(f"- Location will be stored as {md_code(listing['location'])}")

    lines += ['', '**Charter** (title and location only)']
    if gate:
        lines.append(f'- ❌ Rejected by the board rules: {gate}.')
    else:
        lines.append('- ✅ The title and location pass the board rules.')
    lines.append('- The posting text is not read here, so the reviewer checks that it '
                 'asks for 2 years of experience or less.')

    submitted_level = _option(fields.get('Listing Type', ''), LEVEL_LABELS.values())
    inferred_level = classify_level(listing['role']) if listing['role'] else None
    submitted_category = fields.get('Category', '').strip()
    inferred_category = infer_category(listing['role'], security_company)
    lines += ['', '**Level and category**',
              f'- Level: submitted {submitted_level}, title reads as '
              f'{LEVEL_LABELS.get(inferred_level, "no level signal")}',
              f'- Category: submitted {_option(submitted_category, CATEGORY_OPTIONS)}, '
              f'title reads as {inferred_category}']
    if submitted_category != listing['category']:
        lines.append(f"- The row will use `{listing['category']}`")

    lines += ['', '**Duplicates**']
    if dupes:
        for entry, why in dupes:
            row = ', '.join(md_escape(entry.get(f, '')) for f in ('company', 'role', 'location'))
            if is_open(entry):
                lines.append(f'- ❌ Already listed ({why}, open): {row}')
            else:
                lines.append(f'- ↩️ Matches a closed row ({why}): {row}. '
                             'Approving reopens it with this link.')
    else:
        lines.append('- ✅ No row with the same link or company, role and location')

    lines += ['', '**Link**']
    if host:
        lines.append(f'- Host: {md_code(host)}')
    if warning:
        lines.append(f'- ⚠️ {warning}')
    if link is None:
        lines.append('- Not checked')
    else:
        lines.append(f"- {'❌' if link['dead'] else '✅'} {link['summary']}")
    return '\n'.join(lines) + '\n', ok


def _request(method, token, path, **kwargs):
    return requests.request(method, f'{API}{path}', headers=gh_headers(token),
                            timeout=10, **kwargs)


def find_verdict_comment(token, repo, number):
    """Id of this bot's earlier verdict comment on the issue, or None."""
    page = 1
    while True:
        resp = _request('GET', token, f'/repos/{repo}/issues/{number}/comments',
                        params={'per_page': 100, 'page': page})
        resp.raise_for_status()
        batch = resp.json()
        for comment in batch:
            # Match the author as well: a submitter can paste the marker.
            if (comment.get('user') or {}).get('login') == BOT_LOGIN \
                    and MARKER in (comment.get('body') or ''):
                return comment['id']
        if len(batch) < 100:
            return None
        page += 1


def publish(token, repo, number, body, ok):
    """Create or edit the verdict comment and swap the valid/needs-fix label."""
    comment_id = find_verdict_comment(token, repo, number)
    if comment_id:
        resp = _request('PATCH', token, f'/repos/{repo}/issues/comments/{comment_id}',
                        json={'body': body})
    else:
        resp = _request('POST', token, f'/repos/{repo}/issues/{number}/comments',
                        json={'body': body})
    resp.raise_for_status()

    add, remove = (LABEL_VALID, LABEL_FIX) if ok else (LABEL_FIX, LABEL_VALID)
    color, description = LABELS[add]
    # 422 means the label already exists; creating it here keeps the workflow
    # working on a fork or after someone deletes the label.
    resp = _request('POST', token, f'/repos/{repo}/labels',
                    json={'name': add, 'color': color, 'description': description})
    if resp.status_code not in (201, 422):
        resp.raise_for_status()
    _request('POST', token, f'/repos/{repo}/issues/{number}/labels',
             json={'labels': [add]}).raise_for_status()
    resp = _request('DELETE', token, f'/repos/{repo}/issues/{number}/labels/{remove}')
    if resp.status_code not in (200, 404):
        resp.raise_for_status()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dry-run', action='store_true',
                        help='print the verdict instead of posting it')
    parser.add_argument('--no-link', action='store_true', help='skip fetching the link')
    args = parser.parse_args()

    token = os.environ.get('GITHUB_TOKEN')
    repo = os.environ.get('GITHUB_REPOSITORY')
    number = os.environ.get('ISSUE_NUMBER')
    body = os.environ.get('ISSUE_BODY', '')

    fields = parse_issue_body(body)
    listings = json.loads(LISTINGS_FILE.read_text()) if LISTINGS_FILE.exists() else []
    link_url = fields.get('Direct Application Link', '').strip()
    link = None
    if link_url and not args.no_link and re.match(r'^https?://\S+$', link_url):
        link = check_link(link_url)
    verdict, ok = build_verdict(fields, listings, load_security_companies(), link)

    print('\n'.join(oneline(line) for line in verdict.splitlines()))
    print(f'Verdict: {"valid" if ok else "needs-fix"}')
    if args.dry_run:
        return
    if not token or not repo or not number:
        print('GITHUB_TOKEN, GITHUB_REPOSITORY or ISSUE_NUMBER not set, not posting')
        sys.exit(1)
    publish(token, repo, number, verdict, ok)


if __name__ == '__main__':
    main()
