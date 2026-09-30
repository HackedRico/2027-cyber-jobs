#!/usr/bin/env python3
"""Tests for the community submission path, run offline.

    python .github/scripts/test_community.py

Covers issue-form parsing, the location check, the approved-issue ingest and
notify steps, and the validator's verdict comment.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import classify  # noqa: E402
import common  # noqa: E402
import process_approved as pa  # noqa: E402
import requests  # noqa: E402
import responses  # noqa: E402
import validate_issue as vi  # noqa: E402

pa.time.sleep = lambda *a, **k: None

failures = 0


def check(name, got, want):
    global failures
    if got != want:
        failures += 1
        print(f'FAIL {name}: got {got!r}, want {want!r}')


def form(company='Acme', role='Security Analyst Intern', listing_type='Internship / Co-op',
         category='SOC & Detection', location='Austin, TX',
         link='https://boards.greenhouse.io/acme/jobs/1', clearance='No'):
    return (f'### Company Name\n\n{company}\n\n'
            f'### Role / Job Title\n\n{role}\n\n'
            f'### Listing Type\n\n{listing_type}\n\n'
            f'### Category\n\n{category}\n\n'
            f'### Location\n\n{location}\n\n'
            f'### Security Clearance / U.S. Citizenship Required?\n\n{clearance}\n\n'
            f'### Direct Application Link\n\n{link}\n\n'
            f'### Additional Notes (Optional)\n\n_No response_\n')


# --- parse_issue_body ----------------------------------------------------------
PARSE = [
    ('a form body', form(),
     {'Company Name': 'Acme', 'Location': 'Austin, TX', 'Additional Notes (Optional)': ''}),
    ('the first header wins over a later duplicate',
     '### Location\n\nAustin, TX\n\n### Location\n\nLondon, UK\n', {'Location': 'Austin, TX'}),
    ('a null body', None, {}),
    ('a body with no headers', 'just text', {'just text': ''}),
    ('a multi-line value', '### Location\n\nAustin, TX\nReston, VA\n',
     {'Location': 'Austin, TX\nReston, VA'}),
]
for name, body, want in PARSE:
    got = common.parse_issue_body(body)
    check(f'parse_issue_body {name}', {k: got.get(k) for k in want}, want)


# --- normalize_url -------------------------------------------------------------
# A submitter's link and the scraper's link for one req must compare equal, or
# the approved issue lands as a second row.
WORKDAY = 'https://nwis.wd12.myworkdayjobs.com/job/Springfield-VA/X_JR1'
URLS = [
    ('a Workday link with locale and site', 'https://nwis.wd12.myworkdayjobs.com/en-US/NW/job/Springfield-VA/X_JR1',
     WORKDAY),
    ('the scraper Workday link with a site and no locale',
     'https://nwis.wd12.myworkdayjobs.com/NW/job/Springfield-VA/X_JR1', WORKDAY),
    ('a Workday link with a lowercase locale',
     'https://nwis.wd12.myworkdayjobs.com/en-us/NW/job/Springfield-VA/X_JR1/apply', WORKDAY),
    ('a bare Workday link', WORKDAY, WORKDAY),
]
for name, url, want in URLS:
    check(f'normalize_url {name}', common.normalize_url(url), want)


# --- validate_location ---------------------------------------------------------
# (submitted, stored form, valid). Every accepted spelling must also pass the
# scraper's is_us_location, so the form never admits a row the scraper would
# call foreign.
LOCATIONS = [
    ('Arlington, VA', 'Arlington, VA', True),
    ('Washington, D.C.', 'Washington, DC', True),
    ('Washington DC', 'Washington, DC', True),
    ('Arlington, Virginia', 'Arlington, VA', True),
    ('McLean, Va', 'McLean, VA', True),
    ('Reston, va.', 'Reston, VA', True),
    ('Arlington VA', 'Arlington, VA', True),
    ('New York, New York', 'New York, NY', True),
    ('San Juan, PR', 'San Juan, PR', True),
    ('Remote (US)', 'Remote (US)', True),
    ('remote', 'Remote (US)', True),
    ('Remote - US', 'Remote (US)', True),
    ('United States', 'United States', True),
    ('Seattle, WA\nAustin, TX', 'Seattle, WA; Austin, TX', True),
    ('Austin, TX; London, UK', 'Austin, TX', True),
    ('London, UK', 'London, UK', False),
    ('Toronto, ON', 'Toronto, ON', False),
    ('Bangalore, India', 'Bangalore, India', False),
    ('Remote (Canada)', 'Remote (Canada)', False),
    ('Springfield, XX', 'Springfield, XX', False),
    ('Paris', 'Paris', False),
    # Stricter than is_us_location on purpose: a bare state code is no place.
    ('DC', 'DC', False),
    ('', '', False),
    (' ; ', '', False),
]
for submitted, stored, valid in LOCATIONS:
    errors = common.validate_location(submitted)
    check(f'validate_location {submitted!r}', not errors, valid)
    check(f'normalize_submitted_location {submitted!r}',
          common.normalize_submitted_location(submitted), stored)
    if not errors:
        check(f'validate_location agrees with is_us_location on {submitted!r}',
              classify.is_us_location(submitted), True)
    if not classify.is_us_location(submitted):
        check(f'is_us_location rejects, so validate_location must too: {submitted!r}',
              bool(errors), True)


# --- security_company_names ----------------------------------------------------
check('security_company_names reads flagged entries across platforms',
      common.security_company_names({
          'greenhouse': [{'name': 'Acme Sec', 'security_company': True},
                         {'name': 'Bank'}],
          'lever': [{'name': 'Other Sec', 'security_company': True}],
          'workday': None}),
      {'acme sec', 'other sec'})


# --- fields_to_listing ---------------------------------------------------------
def to_listing(body, security=frozenset()):
    return pa.fields_to_listing(common.parse_issue_body(body), security, today='2026-09-27')


row = to_listing(form(clearance='Yes, clearance or U.S. citizenship required (adds 🇺🇸)',
                      location='Arlington, Virginia; Remote'))
check('fields_to_listing builds a full row', row, {
    'company': 'Acme', 'role': 'Security Analyst Intern',
    'location': 'Arlington, VA; Remote (US)', 'type': 'intern',
    'category': 'SOC & Detection', 'clearance': True,
    'url': 'https://boards.greenhouse.io/acme/jobs/1', 'source': 'Community',
    'date_added': '2026-09-27'})
LEVELS = [('New Grad / University Program', 'newgrad'),
          ('Internship / Co-op', 'intern'),
          ('Early Career / Entry-Level', 'earlycareer'),
          ('', 'earlycareer')]
for listing_type, want in LEVELS:
    check(f'fields_to_listing level for {listing_type!r}',
          to_listing(form(listing_type=listing_type))['type'], want)
CATEGORIES = [
    # (submitted category, role, security companies, stored category)
    ('Not sure', 'Penetration Tester I', frozenset(), 'Offensive Security'),
    ('Not sure', 'Software Engineer, New Grad', {'acme'}, 'Engineering @ Security Co'),
    ('Not sure', 'Software Engineer, New Grad', frozenset(), 'Security Engineering'),
    ('Made Up Category', 'SOC Analyst I', frozenset(), 'SOC & Detection'),
    ('GRC & Risk', 'SOC Analyst I', frozenset(), 'GRC & Risk'),
]
for category, role, security, want in CATEGORIES:
    check(f'fields_to_listing category {category!r} for {role!r}',
          to_listing(form(category=category, role=role), security)['category'], want)
check('fields_to_listing clearance No', to_listing(form())['clearance'], False)
check('fields_to_listing clearance Unknown', to_listing(form(clearance='Unknown'))['clearance'],
      False)


# --- ingest --------------------------------------------------------------------
EXISTING = [
    {'company': 'Acme', 'role': 'SOC Analyst I', 'location': 'Austin, TX',
     'url': 'https://boards.greenhouse.io/acme/jobs/9', 'type': 'earlycareer'},
    {'company': 'Beta', 'role': 'Security Engineer Intern', 'location': 'Reston, VA',
     'url': '', 'closed': True, 'closed_date': '2026-09-01', 'missing_since': '2026-08-20',
     'dead_since': '2026-08-30', 'source': 'Greenhouse', 'type': 'intern'},
    # Closed and open rows under one key: the open one makes it a duplicate.
    {'company': 'Gamma', 'role': 'SOC Analyst Intern', 'location': 'Austin, TX',
     'url': '', 'closed': True, 'type': 'intern'},
    {'company': 'Gamma', 'role': 'SOC Analyst Intern', 'location': 'Austin, TX',
     'url': 'https://gamma.example/jobs/5', 'type': 'intern'},
]
issues = [
    {'number': 1, 'body': form()},
    # Same posting under a campaign tag and the other Greenhouse host.
    {'number': 2, 'body': form(role='Other', link='https://job-boards.greenhouse.io/acme/jobs/9?gh_src=x')},
    # Same company, role and location as a closed row under a new link.
    {'number': 3, 'body': form(company='Beta', role='Security Engineer Intern',
                               location='Reston, Virginia',
                               link='https://beta.example/jobs/2')},
    {'number': 4, 'body': form(location='London, UK', link='https://x.example/4')},
    {'number': 5, 'body': form(link='javascript:alert(1)')},
    {'number': 6, 'body': form(company='')},
    # A second submission of issue 1's row in the same batch.
    {'number': 7, 'body': form()},
    {'number': 8, 'body': form(company='Gamma', role='SOC Analyst Intern',
                               link='https://gamma.example/jobs/6')},
    # Issue 3 reopened Beta's row, so a repeat of it is now an open duplicate.
    {'number': 9, 'body': form(company='Beta', role='Security Engineer Intern',
                               location='Reston, VA', link='https://beta.example/jobs/3')},
]
listings = [dict(e) for e in EXISTING]
results = pa.ingest(issues, listings, today='2026-09-27')
check('ingest outcomes', [(r['number'], r['outcome']) for r in results],
      [(1, 'added'), (2, 'duplicate'), (3, 'revived'), (4, 'skipped'), (5, 'skipped'),
       (6, 'skipped'), (7, 'duplicate'), (8, 'duplicate'), (9, 'duplicate')])
check('ingest appends only the added row', len(listings), len(EXISTING) + 1)
check('ingest names the location problem', 'London, UK' in results[3]['detail'], True)
check('ingest names the url problem', 'http(s)' in results[4]['detail'], True)
check('ingest revives a closed row in place as a Community row', listings[1], {
    'company': 'Beta', 'role': 'Security Engineer Intern', 'location': 'Reston, VA',
    'url': 'https://beta.example/jobs/2', 'source': 'Community', 'type': 'intern'})
check('ingest calls an open twin of a closed row a duplicate',
      results[7]['detail'], 'Gamma: SOC Analyst Intern, open')
check('ingest leaves the closed twin closed', listings[2].get('closed'), True)


# --- run_notify ----------------------------------------------------------------
@responses.activate
def test_notify_after_push():
    api = 'https://api.github.com/repos/o/r/issues'
    responses.post(f'{api}/1/comments', json={}, status=201)
    responses.patch(f'{api}/1', json={})
    responses.post(f'{api}/2/comments', json={}, status=201)
    responses.patch(f'{api}/2', json={})
    responses.post(f'{api}/4/comments', json={}, status=201)
    responses.delete(f'{api}/4/labels/approved', status=404)
    results = [{'number': 1, 'outcome': 'added', 'detail': 'Acme: X'},
               {'number': 2, 'outcome': 'duplicate', 'detail': 'Acme: Y, open'},
               {'number': 4, 'outcome': 'skipped', 'detail': 'the location is not valid'}]
    failed = pa.run_notify('t', 'o/r', results, pushed=True)
    check('notify counts no failures (a 404 label delete is fine)', failed, 0)
    calls = [(c.request.method, c.request.url.rsplit('/repos/o/r/', 1)[1])
             for c in responses.calls]
    check('notify comments then closes or unlabels', calls, [
        ('POST', 'issues/1/comments'), ('PATCH', 'issues/1'),
        ('POST', 'issues/2/comments'), ('PATCH', 'issues/2'),
        ('POST', 'issues/4/comments'), ('DELETE', 'issues/4/labels/approved')])
    check('notify tells the submitter why it was skipped',
          b'the location is not valid' in responses.calls[4].request.body, True)


@responses.activate
def test_notify_leaves_added_issue_open_when_push_failed():
    api = 'https://api.github.com/repos/o/r/issues'
    responses.post(f'{api}/4/comments', json={}, status=201)
    responses.delete(f'{api}/4/labels/approved', status=200)
    results = [{'number': 1, 'outcome': 'added', 'detail': 'Acme: X'},
               {'number': 4, 'outcome': 'skipped', 'detail': 'bad'}]
    pa.run_notify('t', 'o/r', results, pushed=False)
    check('notify skips an added issue whose push failed',
          [c.request.url.rsplit('/repos/o/r/', 1)[1] for c in responses.calls],
          ['issues/4/comments', 'issues/4/labels/approved'])


APPROVED_AT = '2026-09-27T10:00:00Z'


def _mock_approval(edits, approved=None):
    """Mock the events API and GraphQL for issues keyed by number.

    `edits` maps an issue number to its lastEditedAt (None when never edited,
    or 'error' for a GraphQL failure). `approved` maps a number to its labeled
    time; a number missing from it gets APPROVED_AT.
    """
    approved = approved or {}
    for number in edits:
        responses.get(f'https://api.github.com/repos/o/r/issues/{number}/events', json=[
            {'event': 'labeled', 'label': {'name': 'approved'},
             'created_at': '2026-09-20T09:00:00Z'},
            {'event': 'labeled', 'label': {'name': 'valid'}, 'created_at': '2026-09-28T09:00:00Z'},
            {'event': 'labeled', 'label': {'name': 'approved'},
             'created_at': approved.get(number, APPROVED_AT)},
        ])

    def graphql(request):
        number = json.loads(request.body)['variables']['number']
        if edits[number] == 'error':
            return 200, {}, json.dumps({'errors': [{'message': 'boom'}]})
        return 200, {}, json.dumps(
            {'data': {'repository': {'issue': {'lastEditedAt': edits[number]}}}})
    responses.add_callback(responses.POST, 'https://api.github.com/graphql', callback=graphql)


@responses.activate
def test_screen_edited_skips_an_issue_edited_after_approval():
    _mock_approval({1: None, 2: '2026-09-26T08:00:00Z', 3: '2026-09-27T10:05:00Z',
                    4: 'error'})
    issues = [{'number': n, 'body': form()} for n in (1, 2, 3, 4)]
    current, results = pa.screen_edited('t', 'o/r', issues)
    check('screen_edited keeps a never-edited issue and one edited before approval',
          [i['number'] for i in current], [1, 2])
    check('screen_edited skips an issue edited after approval and holds an unreadable one',
          [(r['number'], r['outcome']) for r in results], [(3, 'skipped'), (4, 'held')])
    check('screen_edited says why it skipped', results[0]['detail'], pa.EDITED_REASON)


@responses.activate
def test_notify_revived_and_held():
    api = 'https://api.github.com/repos/o/r/issues'
    responses.post(f'{api}/3/comments', json={}, status=201)
    responses.patch(f'{api}/3', json={})
    results = [{'number': 3, 'outcome': 'revived', 'detail': 'Beta: X'},
               {'number': 4, 'outcome': 'held', 'detail': 'could not read'}]
    failed = pa.run_notify('t', 'o/r', results, pushed=True)
    check('notify closes a revived issue and leaves a held one alone',
          (failed, [(c.request.method, c.request.url.rsplit('/repos/o/r/', 1)[1])
                    for c in responses.calls]),
          (0, [('POST', 'issues/3/comments'), ('PATCH', 'issues/3')]))
    check('notify tells the submitter the row reopened',
          b'reopened' in responses.calls[0].request.body, True)
    responses.calls.reset()
    pa.run_notify('t', 'o/r', results[:1], pushed=False)
    check('notify leaves a revived issue open when the push failed', len(responses.calls), 0)


@responses.activate
def test_ingest_writes_run_events_for_added_rows():
    responses.get('https://api.github.com/repos/o/r/issues', json=[
        {'number': 1, 'body': form()},
        {'number': 2, 'body': form(link='https://boards.greenhouse.io/acme/jobs/9')},
        {'number': 3, 'body': form(company='Beta', role='Security Engineer Intern',
                                   location='Reston, VA', link='https://beta.example/jobs/2')},
        # Edited after approval, so its new link never reaches the board.
        {'number': 4, 'body': form(role='Swapped Link Intern', link='https://evil.example/')},
    ])
    responses.get('https://api.github.com/repos/o/r/issues', json=[])
    _mock_approval({1: None, 2: None, 3: None, 4: '2026-09-27T10:05:00Z'})
    saved = (pa.LISTINGS_FILE, pa.load_security_companies, pa.rebuild_readme.main)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        events_file = tmp / 'run_events.json'
        (tmp / 'listings.json').write_text(json.dumps(EXISTING))
        os.environ['RUN_EVENTS_FILE'] = str(events_file)
        try:
            pa.LISTINGS_FILE = tmp / 'listings.json'
            pa.load_security_companies = lambda: set()
            pa.rebuild_readme.main = lambda: None
            pa.run_ingest('t', 'o/r')
            events = json.loads(events_file.read_text())
        finally:
            del os.environ['RUN_EVENTS_FILE']
            pa.LISTINGS_FILE, pa.load_security_companies, pa.rebuild_readme.main = saved
    check('ingest events carry the schema notify.py reads',
          sorted(events), ['added', 'retired', 'revived', 'run_at', 'schema_version'])
    check('ingest events list only the added row, not the duplicate',
          [(r['company'], r['role'], r['source']) for r in events['added']],
          [('Acme', 'Security Analyst Intern', 'Community')])
    check('ingest events list the reopened row as revived',
          [(r['company'], r['url']) for r in events['revived']],
          [('Beta', 'https://beta.example/jobs/2')])
    check('ingest events have no retired rows',
          (events['schema_version'], events['retired']), (1, []))
    check('ingest never adds the link from an edit made after approval',
          'Swapped Link Intern' in json.dumps(events), False)


@responses.activate
def test_notify_reports_api_failure():
    responses.post('https://api.github.com/repos/o/r/issues/1/comments', status=403)
    responses.patch('https://api.github.com/repos/o/r/issues/1', json={})
    failed = pa.run_notify('t', 'o/r', [{'number': 1, 'outcome': 'added', 'detail': ''}],
                           pushed=True)
    check('notify counts a failed API call', failed, 1)


# --- charter_gate agrees with evaluate_job -----------------------------------
GATES = [
    # (title, location, security company, gate substring or None)
    ('Security Analyst Intern', 'Austin, TX', False, None),
    ('Senior Staff Security Engineering Manager', 'Remote (US)', False, 'seniority term "senior"'),
    ('SOC Analyst III', 'Austin, TX', False, 'senior level'),
    ('Security Sales Associate', 'Austin, TX', False, '"sales" marks a non-cyber function'),
    ('Security Officer I', 'Austin, TX', False, 'guard role'),
    ('Security Architect', 'Austin, TX', False, 'architect'),
    ('Associate Solutions Architect, Security', 'Austin, TX', False, None),
    ('Security Sales Engineer I', 'Austin, TX', False, None),
    # Rules added to is_rejected_title after the verdict's own copy of them,
    # which passed these titles while the scraper rejected them.
    ('Food Security Analyst I', 'Austin, TX', True, '"food security" is not information'),
    ('Homeland Security Intern', 'Austin, TX', False, '"homeland security" is not'),
    ('Treasury Operations Analyst I', 'Austin, TX', True, '"treasury" names a department'),
    ('Security Specialist II', 'Austin, TX', True, 'security "specialist" with no other'),
    ('Associate Program Analyst (New Grad)', 'Austin, TX', True, 'program analyst'),
    ('Summer 2024 Security Analyst Intern', 'Austin, TX', False, 'season in the title has passed'),
    ('Information Systems Security Officer I', 'Austin, TX', False, None),
    ('Member of Technical Staff, Security Intern', 'Austin, TX', False, None),
    ('Software Engineer, New Grad', 'Austin, TX', False, 'no cybersecurity keyword'),
    ('Software Engineer, New Grad', 'Austin, TX', True, None),
    ('Security Engineer', 'Austin, TX', False, 'no intern, new grad'),
    ('Security Analyst Intern', 'London, UK', False, 'not in the US'),
    ('', 'Austin, TX', False, 'no job title'),
]
for title, location, security, want in GATES:
    gate = vi.charter_gate(title, location, security)
    check(f'charter_gate {title!r} at {location!r}',
          gate is None if want is None else (gate is not None and want in gate), True)
    check(f'charter_gate agrees with evaluate_job on {title!r} at {location!r}',
          gate is None, classify.evaluate_job(title, location, '', security) is not None)


# --- check_link ----------------------------------------------------------------
class FakeResponse:
    def __init__(self, status, location=None):
        self.status_code = status
        self.headers = {'Location': location} if location else {}

    def close(self):
        pass


def fake_get(chain):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        result = chain[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result
    get.calls = calls
    return get


public = lambda host: True  # noqa: E731
get = fake_get([FakeResponse(301, '/jobs/1b'), FakeResponse(200)])
link = vi.check_link('https://x.example/jobs/1', get=get, resolves_public=public)
check('check_link follows a redirect to 200', (link['dead'], link['summary'][:8]),
      (False, 'HTTP 200'))
check('check_link resolves a relative redirect', get.calls[1], 'https://x.example/jobs/1b')
check('check_link calls a 404 dead',
      vi.check_link('https://x.example/', get=fake_get([FakeResponse(404)]),
                    resolves_public=public)['dead'], True)
check('check_link does not hold a 403 against the posting',
      vi.check_link('https://x.example/', get=fake_get([FakeResponse(403)]),
                    resolves_public=public)['dead'], False)
check('check_link survives a timeout',
      vi.check_link('https://x.example/', get=fake_get([requests.Timeout()]),
                    resolves_public=public)['summary'], 'could not connect (Timeout)')
never = fake_get([])
check('check_link refuses a redirect to a non-http scheme',
      vi.check_link('https://x.example/',
                    get=fake_get([FakeResponse(302, 'file:///etc/passwd')]),
                    resolves_public=public)['summary'],
      'not checked: only http(s) links are fetched')
check('check_link refuses a private address',
      vi.check_link('http://169.254.169.254/', get=never,
                    resolves_public=lambda h: False)['dead'], True)
check('check_link never fetched the private address', never.calls, [])
check('_public_host rejects link-local metadata', vi._public_host('169.254.169.254'), False)
check('_public_host rejects loopback', vi._public_host('127.0.0.1'), False)


# --- build_verdict -------------------------------------------------------------
ALIVE = {'dead': False, 'summary': 'HTTP 200 in 0.2s'}
fields = common.parse_issue_body(form())
body, ok = vi.build_verdict(fields, EXISTING, frozenset(), ALIVE)
check('build_verdict passes a clean submission', ok, True)
check('build_verdict starts with the edit-in-place marker', body.startswith(vi.MARKER), True)
check('build_verdict reports the inferred level',
      'title reads as Internship / Co-op' in body, True)

body, ok = vi.build_verdict(
    common.parse_issue_body(form(role='Senior Staff Security Engineering Manager',
                                 listing_type='New Grad / University Program',
                                 location='Remote')), EXISTING, frozenset(), ALIVE)
check('build_verdict flags a senior title', ok, False)
check('build_verdict names the failed gate', 'seniority term "senior"' in body, True)
check('build_verdict shows the level mismatch',
      'submitted New Grad / University Program, title reads as no level signal' in body, True)

body, ok = vi.build_verdict(
    common.parse_issue_body(form(role='SOC Analyst I', listing_type='Early Career / Entry-Level',
                                 link='https://boards.greenhouse.io/acme/jobs/2')),
    EXISTING, frozenset(), ALIVE)
check('build_verdict flags a duplicate by company, role and location', ok, False)
check('build_verdict names the duplicate', 'same company, role and location, open' in body, True)

body, ok = vi.build_verdict(fields, EXISTING, frozenset(),
                            {'dead': True, 'summary': 'HTTP 404, the posting looks gone'})
check('build_verdict flags a dead link', (ok, 'HTTP 404' in body), (False, True))

body, ok = vi.build_verdict(common.parse_issue_body(form(location='London, UK', link='')),
                            EXISTING, frozenset(), None)
check('build_verdict flags form errors', ok, False)
check('build_verdict lists the missing link',
      '**Direct Application Link** is missing or empty' in body, True)
check('build_verdict lists the location error', 'is not a US state code' in body, True)
check('build_verdict says the link was not checked', '- Not checked' in body, True)

body, ok = vi.build_verdict(common.parse_issue_body(form(category='Not sure')),
                            EXISTING, frozenset(), ALIVE)
check('build_verdict shows the category a Not sure row gets',
      'The row will use `Security Engineering`' in body, True)

body, ok = vi.build_verdict(common.parse_issue_body(form(location='Arlington, Virginia')),
                            EXISTING, frozenset(), ALIVE)
check('build_verdict shows the stored location',
      'Location will be stored as `Arlington, VA`' in body, True)
check('build_verdict body has no em or en dashes',
      '\u2014' in body or '\u2013' in body, False)


# --- escaping echoed fields ----------------------------------------------------
check('md_code wraps plain text in one backtick', common.md_code('Austin, TX'), '`Austin, TX`')
check('md_code outruns a backtick in the text', common.md_code('a`b'), '`` a`b ``')
check('md_code outruns a double backtick', common.md_code('``x'), '``` ``x ```')
check('md_code shows an empty value', common.md_code('  '), '(empty)')
check('md_escape breaks a bare url autolink',
      common.md_escape('see https://evil.example or www.evil.example'),
      'see https&#8203;://evil.example or www&#8203;.evil.example')

# A submitted Category of this shape once closed the verdict's code span and
# rendered a phishing link and @mentions in the bot's comment.
EVIL = 'x` [Verify your account](https://evil.example/login) @octocat `'
EVIL_LOCATION = 'Austin` [a](https://evil.example) @octocat, ZZ'
body, ok = vi.build_verdict(
    common.parse_issue_body(form(listing_type=EVIL, category=EVIL, location=EVIL_LOCATION)),
    [{'company': 'Acme', 'role': 'Security Analyst Intern', 'location': EVIL_LOCATION,
      'url': 'https://boards.greenhouse.io/acme/jobs/1'}], frozenset(),
    vi.check_link('http://a`b.example/', get=never, resolves_public=lambda h: False))
check('build_verdict echoes no free text from an allowlisted field',
      'Verify your account' in body, False)
check('build_verdict names an off-form Listing Type and Category',
      body.count('submitted a value that is not a form option'), 2)
check('build_verdict quotes a location part in a span it cannot close',
      f'`` {EVIL_LOCATION} ``: `ZZ` is not a US state code' in body, True)
check('build_verdict escapes a duplicate row',
      'Austin\\` \\[a\\](https&#8203;://evil.example) @&#8203;octocat, ZZ' in body, True)
check('build_verdict quotes a link host in a span it cannot close',
      '`` a`b.example `` does not resolve' in body, True)
outside_code = ''.join(body.split('``')[::2])
check('build_verdict pings nobody outside a code span', '@octocat' in outside_code, False)
check('_describe escapes a duplicate for the issue comment',
      pa._describe({'company': '@octocat', 'role': '[x](https://evil)', 'url': 'u'}),
      '@&#8203;octocat: \\[x\\](https&#8203;://evil), open')


body, ok = vi.build_verdict(
    common.parse_issue_body(form(company='Beta', role='Security Engineer Intern',
                                 location='Reston, VA', link='https://beta.example/jobs/2')),
    EXISTING, frozenset(), ALIVE)
check('build_verdict passes a submission that reopens a closed row', ok, True)
check('build_verdict says approval reopens the closed row',
      'Approving reopens it with this link' in body, True)


for fn in (test_notify_after_push, test_notify_leaves_added_issue_open_when_push_failed,
           test_notify_reports_api_failure, test_screen_edited_skips_an_issue_edited_after_approval,
           test_notify_revived_and_held, test_ingest_writes_run_events_for_added_rows):
    fn()

if failures:
    print(f'\n{failures} community test(s) failed')
    sys.exit(1)
print('All community tests passed')
