#!/usr/bin/env python3
"""Parser tests for the ATS scrapers, run offline against mocked HTTP.

    python .github/scripts/test_scrapers.py

Exercises the response-shape handling that only broke in production before —
location extraction, pagination stops, intern hints, schema drift, and the
retry/backoff fetch layer — without touching the network.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import compare_runs  # noqa: E402
import responses  # noqa: E402
import scrape_jobs as sj  # noqa: E402

# No real backoff/politeness sleeps during tests.
sj.time.sleep = lambda *a, **k: None

failures = 0


def check(name, got, want):
    global failures
    if got != want:
        failures += 1
        print(f'FAIL {name}: got {got!r}, want {want!r}')


# --- greenhouse_location (pure) ------------------------------------------------
check('greenhouse_location prefers offices over a workplace label',
      sj.greenhouse_location({'location': {'name': 'Hybrid'},
                              'offices': [{'name': 'Austin, TX'}]}),
      'Austin, TX')
check('greenhouse_location keeps a real location',
      sj.greenhouse_location({'location': {'name': 'New York, NY'}}),
      'New York, NY')
check('greenhouse_location reads Job Posting Location metadata',
      sj.greenhouse_location({'location': {'name': 'Remote'}, 'offices': [],
                              'metadata': [{'name': 'Job Posting Location',
                                            'value': ['Reston, VA']}]}),
      'Reston, VA')


# --- scrape_greenhouse ---------------------------------------------------------
@responses.activate
def test_greenhouse():
    responses.get(
        'https://boards-api.greenhouse.io/v1/boards/acme/jobs',
        json={'jobs': [{'id': 1, 'title': 'Security Engineer',
                        'location': {'name': 'Remote'},
                        'offices': [{'name': 'Austin, TX'}],
                        'absolute_url': 'https://x/1', 'content': 'desc'}]})
    jobs = sj.scrape_greenhouse('Acme', 'acme')
    check('greenhouse parses one job', len(jobs), 1)
    check('greenhouse uses offices for location', jobs[0]['location'], 'Austin, TX')
    check('greenhouse id', jobs[0]['id'], 'greenhouse_acme_1')


@responses.activate
def test_greenhouse_http_error_returns_none():
    responses.get('https://boards-api.greenhouse.io/v1/boards/dead/jobs', status=404)
    check('greenhouse 404 -> None', sj.scrape_greenhouse('Dead', 'dead'), None)


# --- scrape_lever --------------------------------------------------------------
@responses.activate
def test_lever():
    responses.get(
        'https://api.lever.co/v0/postings/acme',
        json=[
            {'id': 'a', 'text': 'Security Analyst', 'country': 'US',
             'categories': {'location': 'Austin, TX', 'commitment': 'Full-time'},
             'hostedUrl': 'https://jobs.lever.co/acme/a', 'descriptionPlain': ''},
            {'id': 'b', 'text': 'Security Analyst', 'country': 'CA',  # dropped
             'categories': {'location': 'Toronto'}, 'hostedUrl': 'x'},
            {'id': 'c', 'text': 'Security Engineer', 'country': 'US',
             'categories': {'location': 'Remote', 'commitment': 'Internship'},
             'hostedUrl': 'https://jobs.lever.co/acme/c'},
        ])
    jobs = sj.scrape_lever('Acme', 'acme')
    check('lever drops non-US', len(jobs), 2)
    check('lever intern_hint from commitment',
          [j['intern_hint'] for j in jobs], [False, True])


# --- scrape_ashby --------------------------------------------------------------
@responses.activate
def test_ashby():
    responses.get(
        'https://api.ashbyhq.com/posting-api/job-board/acme',
        json={'jobs': [
            {'id': 'x', 'title': 'Security Engineer', 'location': 'Austin, TX',
             'secondaryLocations': [{'location': 'Remote (US)'}],
             'jobUrl': 'https://jobs.ashbyhq.com/acme/x', 'employmentType': 'FullTime'},
            {'id': 'y', 'title': 'Security Intern', 'location': 'Boston, MA',
             'employmentType': 'Intern', 'isListed': False},  # unlisted -> skipped
        ]})
    jobs = sj.scrape_ashby('Acme', 'acme')
    check('ashby skips unlisted', len(jobs), 1)
    check('ashby merges secondary locations',
          jobs[0]['location'], 'Austin, TX; Remote (US)')


@responses.activate
def test_ashby_schema_drift_warns(capsys=None):
    responses.get('https://api.ashbyhq.com/posting-api/job-board/acme',
                  json={'unexpected': []})
    jobs = sj.scrape_ashby('Acme', 'acme')  # missing both jobs/jobPostings keys
    check('ashby unknown schema -> empty list', jobs, [])


# --- scrape_smartrecruiters (pagination) --------------------------------------
@responses.activate
def test_smartrecruiters_pagination_short_page_stops():
    # One short page (< limit) must stop the loop even if totalFound lies.
    responses.get(
        'https://api.smartrecruiters.com/v1/companies/Acme/postings',
        json={'content': [{'id': '1', 'name': 'Security Engineer',
                           'location': {'country': 'us', 'city': 'Austin',
                                        'region': 'TX'}}],
              'totalFound': 999})
    jobs = sj.scrape_smartrecruiters('Acme', 'Acme')
    check('smartrecruiters short page stops', len(jobs), 1)
    check('smartrecruiters location', jobs[0]['location'], 'Austin, TX')


# --- scrape_oracle -------------------------------------------------------------
@responses.activate
def test_oracle():
    responses.get(
        'https://acme.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/'
        'recruitingCEJobRequisitions',
        json={'items': [{'TotalJobsCount': 1, 'requisitionList': [
            {'Id': '77', 'Title': 'Cybersecurity Analyst',
             'PrimaryLocation': 'Austin, TX, United States',
             'secondaryLocations': [{'Name': 'Remote'}]}]}]})
    jobs = sj.scrape_oracle('Acme', 'acme.fa.us2.oraclecloud.com', 'CX_1')
    check('oracle parses one req', len(jobs), 1)
    check('oracle id', jobs[0]['id'], 'oracle_CX_1_77')
    check('oracle merges locations', jobs[0]['location'],
          'Austin, TX, United States; Remote')


# --- fetch_json retry/backoff --------------------------------------------------
@responses.activate
def test_fetch_json_retries_transient():
    responses.get('https://api.test/x', status=503)          # attempt 1: retry
    responses.get('https://api.test/x', json={'ok': True})   # attempt 2: success
    check('fetch_json retries 503 then succeeds',
          sj.fetch_json('https://api.test/x', label='t'), {'ok': True})


@responses.activate
def test_fetch_json_gives_up_on_404():
    responses.get('https://api.test/y', status=404)
    check('fetch_json 404 -> None', sj.fetch_json('https://api.test/y', label='t'), None)


# --- SSRF: config host components must be validated (no network call) ---------
def test_slug_validation_blocks_host_reparenting():
    check('recruitee rejects a slug with /', sj.scrape_recruitee('X', 'evil.com/'), None)
    check('pinpoint rejects a slug with @', sj.scrape_pinpoint('X', 'a@b'), None)
    check('workday rejects a tenant with /', sj.scrape_workday('X', 'evil.com/', 'wd5', 'B'), None)
    check('oracle rejects a host with a path', sj.scrape_oracle('X', 'evil.com/x', 'CX_1'), None)


# --- a total failure is None (FAILED), not [] (empty board) -------------------
@responses.activate
def test_workday_total_failure_returns_none():
    responses.post('https://t.wd5.myworkdayjobs.com/wday/cxs/t/B/jobs', status=500)
    check('workday all-fetch-fail -> None', sj.scrape_workday('X', 't', 'wd5', 'B'), None)


# --- pagination keeps going when the total field is absent --------------------
@responses.activate
def test_smartrecruiters_missing_total_keeps_paging():
    page1 = {'content': [{'id': str(i), 'name': 'Security Engineer',
                          'location': {'country': 'us', 'city': 'Austin', 'region': 'TX'}}
                         for i in range(100)]}  # full page, NO totalFound
    page2 = {'content': [{'id': 'x', 'name': 'Security Analyst',
                          'location': {'remote': True}}]}  # short page -> stop
    responses.get('https://api.smartrecruiters.com/v1/companies/Acme/postings', json=page1)
    responses.get('https://api.smartrecruiters.com/v1/companies/Acme/postings', json=page2)
    jobs = sj.scrape_smartrecruiters('Acme', 'Acme')
    check('smartrecruiters paged past a full first page with no total', len(jobs), 101)


# --- amazon splits its experience bars out of `description` -------------------
def test_amazon_description_includes_qualifications():
    """amazon.jobs keeps the years bars in basic/preferred_qualifications.

    Reading only `description` made every Amazon req look like it stated no
    floor, so "Security Engineer II @ 4+ years" landed on the board as
    earlycareer (issue #11).
    """
    job = {
        'description': 'The AppSec Security Engineer evaluates service design.',
        'basic_qualifications': '4+ years of experience in information security',
        'preferred_qualifications': '2+ years of AWS experience',
    }
    body = sj._amazon_description(job)
    check('amazon description keeps the posting body',
          'AppSec Security Engineer' in body, True)
    check('amazon description carries the basic bar',
          '4+ years of experience in information security' in body, True)
    check('amazon description labels the sections so the gate can tell them apart',
          body.index('BASIC QUALIFICATIONS') < body.index('PREFERRED QUALIFICATIONS'),
          True)
    check('amazon over-experienced req is now rejected',
          sj.evaluate_job('Security Engineer II', 'Seattle, WA', body), None)
    # A posting with only the two optional fields empty must still round-trip.
    check('amazon description with no qualification fields',
          sj._amazon_description({'description': 'Body only.'}), 'Body only.')


# --- stored rows self-heal when the live posting is over the cap --------------
def test_drop_over_experienced():
    listings = [
        {'company': 'Acme', 'role': 'Security Engineer II', 'location': 'Austin, TX',
         'type': 'earlycareer', 'source': 'Greenhouse', 'url': 'https://a.co/1'},
        {'company': 'Acme', 'role': 'SOC Analyst I', 'location': 'Austin, TX',
         'type': 'earlycareer', 'source': 'Greenhouse', 'url': 'https://a.co/2'},
        {'company': 'Acme', 'role': 'Security Intern', 'location': 'Austin, TX',
         'type': 'intern', 'source': 'Greenhouse', 'url': 'https://a.co/3'},
        {'company': 'Acme', 'role': 'Cyber Analyst', 'location': 'Austin, TX',
         'type': 'earlycareer', 'source': 'Community', 'url': 'https://a.co/4'},
        {'company': 'Acme', 'role': 'Threat Analyst II', 'location': 'Austin, TX',
         'type': 'earlycareer', 'source': 'Greenhouse', 'url': 'https://a.co/5'},
        {'company': 'Ghost', 'role': 'Security Engineer II', 'location': 'Austin, TX',
         'type': 'earlycareer', 'source': 'Greenhouse', 'url': 'https://g.co/9'},
    ]
    raw = [
        # over the cap -> drop
        {'company': 'Acme', 'title': 'Security Engineer II', 'location': 'Austin, TX',
         'url': 'https://a.co/1', 'description': 'Requires 6+ years of experience.'},
        # under the cap -> keep
        {'company': 'Acme', 'title': 'SOC Analyst I', 'location': 'Austin, TX',
         'url': 'https://a.co/2', 'description': 'Requires 1+ year of experience.'},
        # intern + community are exempt even though both descriptions are over
        {'company': 'Acme', 'title': 'Security Intern', 'location': 'Austin, TX',
         'url': 'https://a.co/3', 'description': 'Requires 6+ years of experience.'},
        {'company': 'Acme', 'title': 'Cyber Analyst', 'location': 'Austin, TX',
         'url': 'https://a.co/4', 'description': 'Requires 6+ years of experience.'},
        # empty description must never delete a row
        {'company': 'Acme', 'title': 'Threat Analyst II', 'location': 'Austin, TX',
         'url': 'https://a.co/5', 'description': '   '},
    ]
    kept, dropped = sj.drop_over_experienced(listings, raw)
    check('over-experienced row dropped', [e['role'] for e in dropped],
          ['Security Engineer II'])
    check('under-cap, intern, community, blank-description and unscraped rows kept',
          [e['company'] + '/' + e['role'] for e in kept],
          ['Acme/SOC Analyst I', 'Acme/Security Intern', 'Acme/Cyber Analyst',
           'Acme/Threat Analyst II', 'Ghost/Security Engineer II'])
    # A req that moved to a new URL still matches on (company, role, location).
    moved = [{'company': 'Acme', 'title': 'Security Engineer II',
              'location': 'Austin, TX', 'url': 'https://a.co/1-v2',
              'description': 'Requires 6+ years of experience.'}]
    _, dropped_moved = sj.drop_over_experienced(listings[:1], moved)
    check('row matched by dedup key when the req URL changed',
          len(dropped_moved), 1)


# --- a req that leaves its board's feed is retired ----------------------------
def test_job_fingerprint_reads_every_ats_url_shape():
    cases = [
        # Greenhouse's own host, and a company-hosted board where the path
        # carries no req id at all and only gh_jid identifies the posting.
        ('Acme', 'Greenhouse', 'https://boards.greenhouse.io/acme/jobs/4242', '4242'),
        ('Acme', 'Greenhouse', 'https://acme.com/careers/jobs/99?gh_jid=4242', '4242'),
        ('Acme', 'Lever',
         'https://jobs.lever.co/acme/59991dcb-2ca8-46d4-9b89-0d1b00e3e5eb',
         '59991dcb-2ca8-46d4-9b89-0d1b00e3e5eb'),
        ('Acme', 'Ashby',
         'https://jobs.ashbyhq.com/acme/b9dee2a0-9bb3-447e-9bce-2b1bed784e5b',
         'b9dee2a0-9bb3-447e-9bce-2b1bed784e5b'),
        ('Acme', 'Workday',
         'https://acme.wd1.myworkdayjobs.com/External/job/Austin-TX/Cyber-Eng_R123',
         '/job/Austin-TX/Cyber-Eng_R123'),
        ('Acme', 'Oracle',
         'https://x.fa.us8.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/2615114',
         '2615114'),
        ('Amazon', 'Amazon Jobs',
         'https://www.amazon.jobs/en/jobs/10464931/security-engineer-ii', '10464931'),
        ('Acme', 'SmartRecruiters',
         'https://jobs.smartrecruiters.com/Acme/114671999', '114671999'),
    ]
    for company, source, url, want in cases:
        check(f'fingerprint {source}', sj.job_fingerprint(company, source, url),
              (company, source, want))
    # Anything unrecognized yields None, which exempts the row from retirement
    # rather than guessing an id and deleting the wrong listing.
    check('unknown URL shape has no fingerprint',
          sj.job_fingerprint('Acme', 'Greenhouse', 'https://acme.com/careers'), None)
    check('blank url has no fingerprint', sj.job_fingerprint('Acme', 'Lever', ''), None)
    # Two boards may both number a req 4242; the company keeps them apart.
    check('fingerprints are company-scoped',
          sj.job_fingerprint('Other', 'Greenhouse',
                             'https://boards.greenhouse.io/other/jobs/4242')
          != sj.job_fingerprint('Acme', 'Greenhouse',
                                'https://boards.greenhouse.io/acme/jobs/4242'), True)


def _listing(company, role, url, source='Greenhouse', **extra):
    row = {'company': company, 'role': role, 'location': 'Austin, TX',
           'type': 'earlycareer', 'source': source, 'url': url}
    row.update(extra)
    return row


def test_retire_vanished_listings():
    listings = [
        _listing('Acme', 'Still Open', 'https://boards.greenhouse.io/acme/jobs/1'),
        _listing('Acme', 'Just Vanished', 'https://boards.greenhouse.io/acme/jobs/2'),
        _listing('Acme', 'Long Gone', 'https://boards.greenhouse.io/acme/jobs/3',
                 missing_since='2026-09-01'),
        _listing('Acme', 'Briefly Missing', 'https://boards.greenhouse.io/acme/jobs/4',
                 missing_since='2026-09-09'),
        _listing('Acme', 'Back Again', 'https://boards.greenhouse.io/acme/jobs/5',
                 missing_since='2026-09-01'),
        _listing('Acme', 'Maintainer Pick', 'https://boards.greenhouse.io/acme/jobs/6',
                 source='Community'),
        _listing('Acme', 'Already Closed', '', closed=True),
        _listing('Acme', 'No Req Id', 'https://acme.com/careers'),
        # Ghost's board returned nothing this run — a broken slug must not
        # retire everything behind it.
        _listing('Ghost', 'Unverifiable', 'https://boards.greenhouse.io/ghost/jobs/9',
                 missing_since='2026-09-01'),
    ]
    raw = [
        {'company': 'Acme', 'board': 'Greenhouse',
         'url': 'https://boards.greenhouse.io/acme/jobs/1'},
        {'company': 'Acme', 'board': 'Greenhouse',
         'url': 'https://boards.greenhouse.io/acme/jobs/5'},
    ]
    retired = sj.retire_vanished_listings(listings, raw, '2026-09-10')
    check('only the long-missing row retires', [e['role'] for e in retired],
          ['Long Gone'])

    by_role = {e['role']: e for e in listings}
    check('retired row is blanked and closed',
          (by_role['Long Gone']['url'], by_role['Long Gone']['closed'],
           by_role['Long Gone']['closed_date'], 'missing_since' in by_role['Long Gone']),
          ('', True, '2026-09-10', False))
    check('a row still in the feed carries no streak',
          by_role['Still Open'].get('missing_since'), None)
    check('a newly missing row only starts its streak',
          (by_role['Just Vanished'].get('missing_since'),
           by_role['Just Vanished'].get('closed')),
          ('2026-09-10', None))
    check('a row missing for under VANISHED_DAYS keeps its original stamp',
          (by_role['Briefly Missing']['missing_since'],
           by_role['Briefly Missing'].get('closed')),
          ('2026-09-09', None))
    check('a reappearing row has its streak cleared',
          by_role['Back Again'].get('missing_since'), None)
    check('community, already-closed, unfingerprintable and unhealthy-board rows '
          'are untouched',
          [(e['role'], e.get('missing_since'), e.get('closed')) for e in listings
           if e['role'] in ('Maintainer Pick', 'Already Closed', 'No Req Id',
                            'Unverifiable')],
          [('Maintainer Pick', None, None), ('Already Closed', None, True),
           ('No Req Id', None, None), ('Unverifiable', '2026-09-01', None)])


def test_retire_vanished_needs_the_whole_board_not_one_posting():
    """A board that returns SOME postings still retires only what it did not return."""
    listings = [
        _listing('Acme', 'Gone', 'https://boards.greenhouse.io/acme/jobs/1',
                 missing_since='2026-09-01'),
        _listing('Acme', 'Present', 'https://boards.greenhouse.io/acme/jobs/2',
                 missing_since='2026-09-01'),
    ]
    raw = [{'company': 'Acme', 'board': 'Greenhouse',
            'url': 'https://boards.greenhouse.io/acme/jobs/2'}]
    retired = sj.retire_vanished_listings(listings, raw, '2026-09-10')
    check('partial board retires only the absent req',
          [e['role'] for e in retired], ['Gone'])


# --- board orchestration: pooled scraping keeps config order -------------------
def test_scrape_boards_preserves_config_order():
    import threading
    release = threading.Event()

    def slow():
        release.wait(5)  # finishes after `fast`; map() must still yield it first
        return [{'id': 'slow_1'}]

    def fast():
        release.set()
        return [{'id': 'fast_1'}, {'id': 'fast_2'}]

    def crashes():
        raise RuntimeError('boom')

    tasks = [
        sj.BoardTask('Slow', slow, (), True),
        sj.BoardTask('Fast', fast, ()),
        sj.BoardTask('Broken', lambda: None, ()),
        sj.BoardTask('Crash', crashes, ()),
        sj.BoardTask('Empty', lambda: [], ()),
    ]
    results = list(sj.scrape_boards(tasks, workers=2))
    check('scrape_boards yields in config order, not completion order',
          [r['label'] for r in results], ['Slow', 'Fast', 'Broken', 'Crash', 'Empty'])
    check('scrape_boards statuses: None -> FAILED, raise -> CRASHED, [] -> zero',
          [r['status'] for r in results], ['ok', 'ok', 'FAILED', 'CRASHED', 'zero'])
    check('scrape_boards counts', [r['count'] for r in results], [1, 2, 0, 0, 0])
    check('scrape_boards carries the security_company flag',
          [r['security_company'] for r in results], [True, False, False, False, False])
    check('a crashed board contributes no postings', results[3]['jobs'], [])


def test_build_tasks_honors_board_and_limit():
    config = {
        'greenhouse': [{'name': 'A', 'slug': 'a', 'security_company': True},
                       {'name': 'B', 'slug': 'b'}, {'name': 'C', 'slug': 'c'}],
        'workday': [{'name': 'W', 'tenant': 'w', 'instance': 'wd5', 'board': 'Ext',
                     'search_terms': ['grc']}],
        'oracle': [{'name': 'O', 'host': 'o.fa.us2.oraclecloud.com', 'site': 'CX_1'}],
    }
    check('build_tasks walks boards in config order and ends with the fixed sources',
          [t.label for t in sj.build_tasks(config)],
          ['A (greenhouse/a)', 'B (greenhouse/b)', 'C (greenhouse/c)', 'W (workday/w)',
           'O (oracle/o.fa.us2.oraclecloud.com)', 'Amazon (amazon.jobs)',
           'USAJOBS (data.usajobs.gov)'])
    subset = sj.build_tasks(config, board='greenhouse', limit=2)
    check('--board/--limit narrow the task list',
          [(t.label, t.args, t.security_company) for t in subset],
          [('A (greenhouse/a)', ('A', 'a'), True), ('B (greenhouse/b)', ('B', 'b'), False)])
    workday = sj.build_tasks(config, board='workday')[0]
    check('workday task passes board and per-company search terms through',
          workday.args, ('W', 'w', 'wd5', 'Ext', False, ['grc']))


def test_board_health_streaks():
    """A dead board has to keep warning, not warn once and go quiet."""
    zero = [{'label': 'Acme', 'status': 'zero', 'count': 0}]
    # Regression: postings last run, none now.
    history, regressed, dead = sj.board_health(
        zero, {'Acme': {'count': 12, 'zero_runs': 0, 'last_nonzero': '2026-09-13'}},
        '2026-09-14')
    check('board_health reports a fresh regression', regressed, [('Acme', 12)])
    check('a one-run outage is not yet called dead', dead, [])
    check('the streak starts at one', history['Acme']['zero_runs'], 1)
    check('the last healthy date is carried forward',
          history['Acme']['last_nonzero'], '2026-09-13')

    # Keep it silent: the streak grows and the alert eventually fires, and
    # keeps firing — the old count-only baseline reported nothing from here on.
    for run in range(2, sj.ZERO_RUN_ALERT + 2):
        history, regressed, dead = sj.board_health(zero, history, '2026-09-14')
        check(f'run {run}: no repeat regression once the count is already 0',
              regressed, [])
        check(f'run {run}: streak', history['Acme']['zero_runs'], run)
        want = [('Acme', run, '2026-09-13')] if run >= sj.ZERO_RUN_ALERT else []
        check(f'run {run}: dead list', dead, want)

    # Recovery resets everything.
    history, regressed, dead = sj.board_health(
        [{'label': 'Acme', 'status': 'ok', 'count': 7}], history, '2026-09-20')
    check('a recovered board clears its streak', history['Acme'],
          {'count': 7, 'zero_runs': 0, 'last_nonzero': '2026-09-20'})
    check('a recovered board is not reported dead', dead, [])


def test_board_health_migrates_and_survives_a_corrupt_baseline():
    original = sj.BOARD_BASELINE_FILE
    try:
        sj.BOARD_BASELINE_FILE = Path(tempfile.mkdtemp()) / 'baseline.json'
        check('a missing baseline reads as empty', sj.load_board_baseline(), {})
        sj.BOARD_BASELINE_FILE.write_text('{not json')
        check('a corrupt baseline reads as empty', sj.load_board_baseline(), {})
        # The legacy {label: count} shape has no history to inherit.
        sj.BOARD_BASELINE_FILE.write_text(json.dumps({'Old': 3, 'Dead': 0}))
        check('legacy int entries migrate', sj.load_board_baseline(),
              {'Old': {'count': 3, 'zero_runs': 0, 'last_nonzero': None},
               'Dead': {'count': 0, 'zero_runs': 0, 'last_nonzero': None}})
        # A board configured with a bad slug from day one has no prior count, so
        # it never regressed and the old baseline could never flag it.
        stats = [{'label': 'Dead', 'status': 'zero', 'count': 0}]
        history = sj.load_board_baseline()
        for _run in range(1, sj.ZERO_RUN_ALERT + 1):
            history, regressed, dead = sj.board_health(stats, history, '2026-09-14')
        check('a never-healthy board is eventually reported dead',
              dead, [('Dead', sj.ZERO_RUN_ALERT, None)])
        check('...with no phantom regression', regressed, [])
    finally:
        sj.BOARD_BASELINE_FILE = original


def test_compare_runs_reports_flips_only():
    before = compare_runs.parse_log(
        'Checking Acme (greenhouse/acme)... ok (12 postings, 1.0s)\n'
        'Checking Beta (lever/beta)... ok (3 postings, 0.4s)\n'
        '  RECLASSIFY [earlycareer -> newgrad] Acme — Analyst\n'
        '  NEW [newgrad] Acme — Security Analyst - New Grad @ Austin, TX\n'
        '  NEW [earlycareer] Acme — Security Engineer II @ Remote (US)\n'
        '  NEW [intern] Beta — SOC Intern @ Boston, MA\n')
    after = compare_runs.parse_log(
        'Checking Acme (greenhouse/acme)... ok (12 postings, 1.0s)\n'
        'Checking Beta (lever/beta)... FAILED (0 postings, 0.4s)\n'
        '  RECLASSIFY [earlycareer -> newgrad] Acme — Analyst\n'
        '  DROP [rejected-title] Acme — Physical Security Guard\n'
        '  NEW [newgrad] Acme — Security Analyst - New Grad @ Austin, TX\n'
        '  NEW [intern] Acme — Security Engineer II @ Remote (US)\n')
    check('compare_runs parses board status and count',
          before.boards['Beta (lever/beta)'], ('ok', 3))
    report = compare_runs.diff(before, after)
    check('compare_runs lists the row only the first run accepted',
          '  - [intern] Beta — SOC Intern @ Boston, MA' in report, True)
    check('compare_runs lists a level change',
          '  - Acme — Security Engineer II @ Remote (US): earlycareer -> intern' in report, True)
    check('compare_runs lists a new drop of an existing row',
          '  - Acme — Physical Security Guard [rejected-title]' in report, True)
    check('compare_runs ignores a reclassify present in both runs',
          any('Analyst:' in line for line in report), False)
    check('compare_runs lists a board status change',
          '  - Beta (lever/beta): ok (3) -> FAILED (0)' in report, True)
    check('compare_runs reports identical runs as equivalent',
          compare_runs.diff(before, before), [])


for fn in (test_greenhouse, test_greenhouse_http_error_returns_none, test_lever,
           test_ashby, test_ashby_schema_drift_warns,
           test_smartrecruiters_pagination_short_page_stops, test_oracle,
           test_fetch_json_retries_transient, test_fetch_json_gives_up_on_404,
           test_slug_validation_blocks_host_reparenting,
           test_workday_total_failure_returns_none,
           test_smartrecruiters_missing_total_keeps_paging,
           test_amazon_description_includes_qualifications,
           test_drop_over_experienced, test_job_fingerprint_reads_every_ats_url_shape,
           test_retire_vanished_listings,
           test_retire_vanished_needs_the_whole_board_not_one_posting,
           test_scrape_boards_preserves_config_order,
           test_build_tasks_honors_board_and_limit, test_board_health_streaks,
           test_board_health_migrates_and_survives_a_corrupt_baseline,
           test_compare_runs_reports_flips_only):
    fn()

if failures:
    print(f'\n{failures} scraper test(s) failed')
    sys.exit(1)
print('All scraper parser tests passed')
