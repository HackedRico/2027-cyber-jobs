#!/usr/bin/env python3
"""Parser tests for the ATS scrapers, run offline against mocked HTTP.

    python .github/scripts/test_scrapers.py

Exercises the response-shape handling that only broke in production before —
location extraction, pagination stops, intern hints, schema drift, and the
retry/backoff fetch layer — without touching the network.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))
import check_links  # noqa: E402
import check_slugs  # noqa: E402
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


# --- check_slugs SmartRecruiters id probe --------------------------------------
@responses.activate
def test_check_slugs_flags_unknown_smartrecruiters_id():
    # The postings API returns 200 and an empty list for a made-up id, so only
    # the careers page's 302 tells a typo from a quiet board.
    responses.get('https://careers.smartrecruiters.com/zzqnotarealco987', status=302,
                  headers={'Location': 'https://jobs.smartrecruiters.com/'})
    responses.get('https://careers.smartrecruiters.com/Acme', body='<html></html>')
    check('check_slugs flags a redirecting SmartRecruiters id',
          check_slugs._smartrecruiters_id_unknown('zzqnotarealco987'), True)
    check('check_slugs passes a real SmartRecruiters id',
          check_slugs._smartrecruiters_id_unknown('Acme'), False)


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
    check('oracle id', jobs[0]['id'], 'oracle_acme.fa.us2.oraclecloud.com_CX_1_77')
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


# --- stored rows are re-judged against their live posting ---------------------
GH_JOBS = 'https://boards.greenhouse.io/{}/jobs/{}'


def _stored(company, role, n, kind='earlycareer', **extra):
    row = {'company': company, 'role': role, 'location': 'Austin, TX', 'type': kind,
           'category': 'Security Engineering', 'clearance': False,
           'url': GH_JOBS.format(company.lower(), n), 'source': 'Greenhouse'}
    row.update(extra)
    return row


def _live(company, title, n, description='', location='Austin, TX', **extra):
    job = {'id': f'gh-{company}-{n}', 'company': company, 'title': title,
           'location': location, 'url': GH_JOBS.format(company.lower(), n),
           'board': 'Greenhouse', 'description': description}
    job.update(extra)
    return job


def _reevaluate(listings, raw, flags=None):
    kept, dropped, refreshed = sj.reevaluate_stored_listings(listings, raw, flags or {})
    return (kept, [(e['company'], e['role'], reason) for e, reason in dropped],
            [(e['role'], field, old, new) for e, field, old, new in refreshed])


def test_reevaluate_drops_rows_the_pipeline_now_rejects():
    listings = [
        # Admitted on a security_company flag the company has since lost.
        _stored('Jumio', 'Research Engineer - Machine Learning & Robotics', 1,
                kind='newgrad', category='Engineering @ Security Co'),
        # An AI flat title whose posting states no years.
        _stored('Anthropic', 'Safeguards Enforcement Analyst, Child Safety', 2),
        _stored('Acme', 'Security Engineer II', 3),
        _stored('ExtraHop', 'Support Engineer I - UK', 4, location='Remote (US)'),
        _stored('Acme', 'SOC Analyst I', 5),
    ]
    raw = [
        _live('Jumio', 'Research Engineer - Machine Learning & Robotics', 1,
              'Join our new grad research team.'),
        _live('Anthropic', 'Safeguards Enforcement Analyst, Child Safety', 2,
              'Minimum years of experience: Years of experience required will correlate '
              'with the internal job level requirements.'),
        _live('Acme', 'Security Engineer II', 3, 'Requires 6+ years of experience.'),
        _live('ExtraHop', 'Support Engineer I - UK', 4, 'Support our customers.',
              location='Remote | United Kingdom'),
        _live('Acme', 'SOC Analyst I', 5, 'Requires 1+ year of experience.'),
    ]
    kept, dropped, _ = _reevaluate(listings, raw, {'Jumio': False, 'ExtraHop': True})
    check('re-evaluation drops each row with the gate that failed', dropped, [
        ('Jumio', 'Research Engineer - Machine Learning & Robotics', 'not-cyber'),
        ('Anthropic', 'Safeguards Enforcement Analyst, Child Safety', 'no-level'),
        ('Acme', 'Security Engineer II', 'over-experienced'),
        ('ExtraHop', 'Support Engineer I - UK', 'non-us-location'),
    ])
    check('re-evaluation keeps the row that still passes', [e['role'] for e in kept],
          ['SOC Analyst I'])


def test_reevaluate_refreshes_category_type_and_clearance():
    listings = [
        _stored('Acme', 'SOC Analyst I', 1, category='Security Engineering'),
        _stored('Acme', 'Cyber Analyst', 2, kind='earlycareer'),
        _stored('Acme', 'Security Engineer I', 3, clearance=False),
        _stored('Acme', 'Security Engineer I', 4, location='Reston, VA', clearance=True),
    ]
    raw = [
        _live('Acme', 'SOC Analyst I', 1, 'Monitor alerts.'),
        _live('Acme', 'Cyber Analyst', 2, 'Open to new graduates of 2026 programs.'),
        _live('Acme', 'Security Engineer I', 3,
              'Must hold an active TS/SCI clearance.'),
        _live('Acme', 'Security Engineer I', 4, 'Build detection tooling.',
              location='Reston, VA'),
    ]
    kept, dropped, refreshed = _reevaluate(listings, raw)
    check('refresh drops nothing', dropped, [])
    check('refresh rewrites category, type and clearance from the live posting', refreshed, [
        ('SOC Analyst I', 'category', 'Security Engineering', 'SOC & Detection'),
        ('Cyber Analyst', 'type', 'earlycareer', 'newgrad'),
        ('Security Engineer I', 'clearance', False, True),
        ('Security Engineer I', 'clearance', True, False),
    ])
    check('refresh writes the new values onto the rows',
          [(e['category'], e['type'], e['clearance']) for e in kept],
          [('SOC & Detection', 'earlycareer', False),
           ('Security Engineering', 'newgrad', False),
           ('Security Engineering', 'earlycareer', True),
           ('Security Engineering', 'earlycareer', False)])


def test_reevaluate_guardrails_keep_rows():
    listings = [
        # No live match on a board that did return postings.
        _stored('Acme', 'SOC Analyst I', 1),
        # A company whose board returned nothing this run.
        _stored('Ghost', 'Research Engineer - Machine Learning & Robotics', 2,
                kind='newgrad'),
        # Flat title whose level came from a description the live copy lacks.
        _stored('Acme', 'Security Engineer', 3),
        # Community rows carry a maintainer's judgment.
        _stored('Acme', 'Senior Staff Security Architect', 4, source='Community'),
        # Interns keep their type even when the live title reads otherwise.
        _stored('Acme', 'Security Engineer', 5, kind='intern'),
        # A blank live location says nothing about where the job is.
        _stored('Acme', 'Cyber Analyst I', 6),
    ]
    raw = [
        _live('Acme', 'Security Engineer', 3, '   '),
        _live('Acme', 'Senior Staff Security Architect', 4, 'Requires 10+ years.'),
        _live('Acme', 'Security Engineer', 5, 'Requires 6+ years of experience.'),
        _live('Acme', 'Cyber Analyst I', 6, 'Entry level.', location=''),
    ]
    kept, dropped, refreshed = _reevaluate(listings, raw, {'Ghost': False})
    check('guardrails drop nothing', dropped, [])
    check('guardrails keep every row', len(kept), len(listings))
    check('guardrails refresh nothing on a Community row or an intern type',
          [r for r in refreshed if r[1] == 'type' or r[0].startswith('Senior')], [])
    check('an intern row stays an intern', kept[4]['type'], 'intern')


def test_reevaluate_matches_a_moved_req_by_dedup_key():
    listings = [_stored('Acme', 'Security Engineer II', 1)]
    moved = [dict(_live('Acme', 'Security Engineer II', 1,
                        'Requires 6+ years of experience.'),
                  url='https://acme.example/careers?id=77', board='')]
    _, dropped, _ = _reevaluate(listings, moved)
    check('row matched by dedup key when the req URL changed', len(dropped), 1)


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
         'R123'),
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


def test_repair_broken_locations():
    """A location the normalizer destroyed is re-read off the live posting."""
    wd = ('https://globalhr.wd5.myworkdayjobs.com/REC_RTX_Ext_Gateway/job/'
          'US-AZ-TUCSON-801--1151-E-Hermans-Rd--BLDG-801-External-Site/'
          'Systems-Security-Engineer-I_01874755')
    gh = 'https://boards.greenhouse.io/acme/jobs/'
    listings = [
        # The real regression: "US-AZ-TUCSON-801 ~ ..." became "Az" (#18) and
        # no amount of re-normalizing two letters brings Tucson back.
        _listing('RTX', 'Systems Security Engineer I', wd, source='Workday', location='Az'),
        # Already fine, and the live posting disagrees: must not be rewritten,
        # or a board that reorders a multi-location req churns the file forever.
        _listing('Acme', 'Healthy', gh + '1', location='Austin, TX'),
        # Broken, but a maintainer wrote it.
        _listing('Acme', 'Maintainer Pick', gh + '2', source='Community', location='Zz'),
        # Broken, but this run never saw the req / the live location is blank.
        _listing('Acme', 'Unseen', gh + '3', location='Zz'),
        _listing('Acme', 'No Live Location', gh + '4', location='Zz'),
        # Broken, but the URL carries no req id to match on.
        _listing('Acme', 'No Req Id', 'https://acme.com/careers', location='Zz'),
        # Broken, and the req has moved abroad: a US-only board must not gain a
        # London row because a repair pass rewrote it.
        _listing('Acme', 'Moved Abroad', gh + '5', location='Zz'),
        # Broken, and the live string only looks US once normalized. Ingestion
        # judges the raw string, so the repair must too.
        _listing('Acme', 'Offshore Remote', gh + '6', location='Zz'),
        # One wrecked part beside a healthy one is still a candidate.
        _listing('Acme', 'Half Wrecked', gh + '7', location='Az; Remote (US)'),
        # Repairing this row would give it the key 'Healthy' already holds, so it
        # is folded away instead of becoming a duplicate row on the board.
        _listing('Acme', 'Healthy', gh + '8', location='Tx'),
        # Mid-decay: still reads as US today, but the normalizer turns it into
        # "Ma" next run, so it is repaired now rather than after that happens.
        _listing('Acme', 'Decaying', gh + '9', location='Ma, US'),
    ]
    raw = [
        {'company': 'RTX', 'board': 'Workday', 'url': wd,
         'location': 'US-AZ-TUCSON-801 ~ 1151 E Hermans Rd ~ BLDG 801'},
        {'company': 'Acme', 'board': 'Greenhouse', 'url': gh + '1', 'location': 'Remote (US)'},
        {'company': 'Acme', 'board': 'Greenhouse', 'url': gh + '2', 'location': 'Austin, TX'},
        {'company': 'Acme', 'board': 'Greenhouse', 'url': gh + '4', 'location': '   '},
        {'company': 'Acme', 'board': 'Greenhouse', 'url': gh + '5', 'location': 'London, UK'},
        {'company': 'Acme', 'board': 'Greenhouse', 'url': gh + '6',
         'location': 'Remote - India (US business hours)'},
        {'company': 'Acme', 'board': 'Greenhouse', 'url': gh + '7',
         'location': 'US-AZ-TUCSON-801 ~ 1151 E Hermans Rd; Remote (US)'},
        {'company': 'Acme', 'board': 'Greenhouse', 'url': gh + '8', 'location': 'Austin, TX'},
        {'company': 'Acme', 'board': 'Greenhouse', 'url': gh + '9',
         'location': 'US-MA-TEWKSBURY-TB1 ~ 50 Apple Hill Dr ~ ASSABET BLDG'},
    ]
    kept, repaired, folded = sj.repair_broken_locations(listings, raw)
    check('only destroyed locations with a US live posting are repaired',
          [(e['role'], before, after) for e, before, after in repaired],
          [('Systems Security Engineer I', 'Az', 'Tucson, AZ'),
           ('Half Wrecked', 'Az; Remote (US)', 'Tucson, AZ; Remote (US)'),
           ('Decaying', 'Ma, US', 'Tewksbury, MA')])
    check('a repair that would duplicate another row folds the row away',
          [(e['role'], e['location']) for e in folded], [('Healthy', 'Tx')])
    check('every other row keeps its stored location',
          {e['role']: e['location'] for e in kept},
          {'Systems Security Engineer I': 'Tucson, AZ', 'Healthy': 'Austin, TX',
           'Maintainer Pick': 'Zz', 'Unseen': 'Zz', 'No Live Location': 'Zz',
           'No Req Id': 'Zz', 'Moved Abroad': 'Zz', 'Offshore Remote': 'Zz',
           'Half Wrecked': 'Tucson, AZ; Remote (US)', 'Decaying': 'Tewksbury, MA'})
    check('the folded duplicate is gone from the kept rows', len(kept), len(listings) - 1)
    # Idempotent: a repaired row now reads as US, so a second pass skips it.
    check('a repaired row is not repaired again',
          sj.repair_broken_locations(kept, raw)[1:], ([], []))


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
    check('build_tasks walks boards in config order and ends with Amazon',
          [t.label for t in sj.build_tasks(config)],
          ['A (greenhouse/a)', 'B (greenhouse/b)', 'C (greenhouse/c)', 'W (workday/w)',
           'O (oracle/o.fa.us2.oraclecloud.com/CX_1)', 'Amazon (amazon.jobs)'])
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
        "  REPAIRED [location] Acme — Systems Engineer I: 'Az' -> 'Tucson, AZ'\n"
        "  REPAIRED [location] Acme — Lost Repair: 'Ma' -> 'Woburn, MA'\n"
        '  NEW [newgrad] Acme — Security Analyst - New Grad @ Austin, TX\n'
        '  NEW [earlycareer] Acme — Security Engineer II @ Remote (US)\n'
        '  NEW [intern] Beta — SOC Intern @ Boston, MA\n')
    after = compare_runs.parse_log(
        'Checking Acme (greenhouse/acme)... ok (12 postings, 1.0s)\n'
        'Checking Beta (lever/beta)... FAILED (0 postings, 0.4s)\n'
        '  RECLASSIFY [earlycareer -> newgrad] Acme — Analyst\n'
        '  DROP [rejected-title] Acme — Physical Security Guard\n'
        '  NEW [newgrad] Acme — Security Analyst - New Grad @ Austin, TX\n'
        '  NEW [intern] Acme — Security Engineer II @ Remote (US)\n'
        "  REPAIRED [location] Acme — Systems Engineer I: 'Az' -> 'Tucson, AZ'\n"
        "  REPAIRED [location] Acme — Intern: Cybersecurity Undergrad: 'Ar' -> 'Bentonville, AR'\n"
        "  REPAIRED [location] Acme — Site Intern: 'Ri' -> 'Portsmouth, RI'\n"
        "  REPAIRED [location] Acme — Site Intern: 'Ma' -> 'Tewksbury, MA'\n"
        "  REFRESHED [category] Acme — SOC Analyst I: 'Security Engineering' -> "
        "'SOC & Detection'\n"
        '  REFRESHED [clearance] Acme — Cyber Analyst I: False -> True\n')
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
    check('compare_runs ignores a repair present in both runs',
          any('Systems Engineer I' in line for line in report), False)
    check('compare_runs keeps a title containing ": " whole in a repair line',
          "  - Acme — Intern: Cybersecurity Undergrad: 'Ar' -> 'Bentonville, AR'" in report, True)
    check('compare_runs lists same-title repairs at different sites separately',
          [line for line in report if 'Site Intern' in line],
          ["  - Acme — Site Intern: 'Ma' -> 'Tewksbury, MA'",
           "  - Acme — Site Intern: 'Ri' -> 'Portsmouth, RI'"])
    check('compare_runs lists a repair only the first run made',
          "  - Acme — Lost Repair: 'Ma' -> 'Woburn, MA'" in report, True)
    check('compare_runs lists refreshed categories and clearance flags',
          [line for line in report if '[category]' in line or '[clearance]' in line],
          ["  - Acme — Cyber Analyst I [clearance]: False -> True",
           "  - Acme — SOC Analyst I [category]: 'Security Engineering' -> 'SOC & Detection'"])
    check('compare_runs lists a board status change',
          '  - Beta (lever/beta): ok (3) -> FAILED (0)' in report, True)
    check('compare_runs reports identical runs as equivalent',
          compare_runs.diff(before, before), [])


@responses.activate
def test_check_links_closes_after_two_dead_days():
    # Only rows nothing else can close are checked: a fingerprinted Greenhouse
    # row is the scraper's job, so it must not cost a request here.
    community = {'company': 'Acme', 'role': 'Analyst', 'source': 'Community',
                 'url': 'https://acme.com/careers/analyst', 'missing_since': '2026-09-20'}
    amazon = {'company': 'Amazon', 'role': 'Security Engineer', 'source': 'Amazon Jobs',
              'url': 'https://www.amazon.jobs/en/jobs/123'}
    greenhouse = {'company': 'Acme', 'role': 'SOC Analyst', 'source': 'Greenhouse',
                  'url': 'https://boards.greenhouse.io/acme/jobs/9'}
    closed = {'company': 'Acme', 'role': 'Old', 'source': 'Community', 'url': '',
              'closed': True}
    check('link check targets community and amazon rows only',
          [check_links.is_check_target(e) for e in (community, amazon, greenhouse, closed)],
          [True, True, False, False])

    responses.add(responses.GET, community['url'], status=404)
    responses.add(responses.GET, amazon['url'], status=403)
    check('403 is not dead', check_links.record_result(
        amazon, check_links.fetch_status(amazon['url']), '2026-09-26'), False)
    check('403 starts no streak', 'dead_since' in amazon, False)

    status = check_links.fetch_status(community['url'])
    check('first dead day only starts the streak',
          check_links.record_result(community, status, '2026-09-26'), False)
    check('dead_since stamped', community.get('dead_since'), '2026-09-26')
    check('a second run the same day does not close',
          check_links.record_result(community, status, '2026-09-26'), False)
    check('second dead day closes', check_links.record_result(community, 410, '2026-09-27'), True)
    check('closed row keeps its link and sheds its streaks',
          {k: community.get(k) for k in ('url', 'last_url', 'closed', 'closed_date',
                                        'dead_since', 'missing_since')},
          {'url': '', 'last_url': 'https://acme.com/careers/analyst', 'closed': True,
           'closed_date': '2026-09-27', 'dead_since': None, 'missing_since': None})

    flaky = {'url': 'https://x/j', 'dead_since': '2026-09-25'}
    check('a live answer resets the streak',
          (check_links.record_result(flaky, 200, '2026-09-26'), 'dead_since' in flaky),
          (False, False))


# --- Workday paging: `total` only arrives on the first page ------------------
WD_API = 'https://t.wd5.myworkdayjobs.com/wday/cxs/t/B/jobs'


def _workday_pages(pages_by_term, fail_at=None):
    """A cxs /jobs callback serving `pages_by_term[term][offset // 20]`."""
    def callback(request):
        body = json.loads(request.body)
        term, page = body['searchText'], body['offset'] // body['limit']
        if fail_at == (term, page):
            return 500, {}, '{}'
        pages = pages_by_term.get(term, [])
        return 200, {}, json.dumps(pages[page] if page < len(pages)
                                   else {'total': 0, 'jobPostings': []})
    return callback


def _wd_page(total, start, count):
    # Non-cyber titles, so no detail fetch is attempted.
    return {'total': total, 'jobPostings': [
        {'title': f'Accountant {i}', 'externalPath': f'/job/Austin-TX/Acct_R{i}',
         'locationsText': 'Austin, TX'} for i in range(start, start + count)]}


@responses.activate
def test_workday_total_only_on_first_page():
    """Leidos 'security' reports 1662 at offset 0 and 0 at offset 20."""
    responses.add_callback(responses.POST, WD_API, callback=_workday_pages({
        'cyber': [_wd_page(45, 0, 20), _wd_page(0, 20, 20), _wd_page(0, 40, 5)]}))
    jobs = sj.scrape_workday('X', 't', 'wd5', 'B')
    check('workday pages past a zero total on page 2', len(jobs), 45)
    check('a sweep that reached every short page is complete',
          any(j.get('partial_sweep') for j in jobs), False)
    check('workday url keeps the board segment', jobs[0]['url'],
          'https://t.wd5.myworkdayjobs.com/B/job/Austin-TX/Acct_R0')


@responses.activate
def test_workday_flags_a_cut_short_sweep():
    original = sj.WORKDAY_MAX_PAGES
    try:
        sj.WORKDAY_MAX_PAGES = 2
        responses.add_callback(responses.POST, WD_API, callback=_workday_pages({
            'cyber': [_wd_page(100, 0, 20), _wd_page(0, 20, 20), _wd_page(0, 40, 20)]}))
        jobs = sj.scrape_workday('X', 't', 'wd5', 'B')
        check('workday stops at the page cap', len(jobs), 40)
        check('a capped sweep flags every posting partial',
              all(j.get('partial_sweep') for j in jobs), True)
    finally:
        sj.WORKDAY_MAX_PAGES = original

    responses.reset()
    responses.add_callback(responses.POST, WD_API, callback=_workday_pages(
        {'cyber': [_wd_page(60, 0, 20), _wd_page(0, 20, 20), _wd_page(0, 40, 20)]},
        fail_at=('cyber', 1)))
    jobs = sj.scrape_workday('X', 't', 'wd5', 'B')
    check('a page that fails mid-sweep keeps what came before it', len(jobs), 20)
    check('...and flags the sweep partial', all(j.get('partial_sweep') for j in jobs), True)

    responses.reset()
    responses.add_callback(responses.POST, WD_API, callback=_workday_pages(
        {}, fail_at=('security', 0)))
    check('a partial sweep that found nothing is a failure, not an empty board',
          sj.scrape_workday('X', 't', 'wd5', 'B'), None)


def test_incomplete_sweep_retires_nothing():
    wd = 'https://acme.wd1.myworkdayjobs.com/Ext/job/Austin-TX/'
    gh = 'https://boards.greenhouse.io/acme/jobs/'
    listings = [
        _listing('Acme', 'Seen', wd + 'Seen_R1', source='Workday',
                 missing_since='2026-09-01'),
        _listing('Acme', 'Past The Cap', wd + 'Deep_R2', source='Workday',
                 missing_since='2026-09-01'),
        _listing('Acme', 'Not Yet Missing', wd + 'Deep_R3', source='Workday'),
        # The same company's complete Greenhouse board is still judged.
        _listing('Acme', 'Greenhouse Gone', gh + '7', missing_since='2026-09-01'),
    ]
    raw = [{'company': 'Acme', 'board': 'Workday', 'url': wd + 'Seen_R1',
            'partial_sweep': True},
           {'company': 'Acme', 'board': 'Greenhouse', 'url': gh + '8'}]
    retired = sj.retire_vanished_listings(listings, raw, '2026-09-10',
                                          probe=lambda url: None)
    check('a partial sweep retires none of its rows',
          [e['role'] for e in retired], ['Greenhouse Gone'])
    check('rows behind a partial sweep keep their streak as it was',
          [(e['role'], e.get('missing_since'), e.get('closed')) for e in listings[:3]],
          [('Seen', None, None), ('Past The Cap', '2026-09-01', None),
           ('Not Yet Missing', None, None)])


def test_partial_workday_sweep_asks_the_detail_endpoint():
    """CVS, RTX and Northrop stop at the page cap every run."""
    wd = 'https://acme.wd1.myworkdayjobs.com/Ext/job/Austin-TX/'
    listings = [
        _listing('Acme', 'Closed Deep', wd + 'Gone_R1', source='Workday',
                 missing_since='2026-09-01'),
        _listing('Acme', 'Live Deep', wd + 'Live_R2', source='Workday',
                 missing_since='2026-09-01'),
        _listing('Acme', 'Closed Fresh', wd + 'Gone_R3', source='Workday'),
    ]
    raw = [{'company': 'Acme', 'board': 'Workday', 'url': wd + 'Other_R9',
            'partial_sweep': True}]
    asked = []

    def probe(url):
        asked.append(url)
        return 'gone' if 'Gone' in url else 'live'

    retired = sj.retire_vanished_listings(listings, raw, '2026-09-10', probe=probe)
    check('a gone answer retires a row whose streak has run',
          [e['role'] for e in retired], ['Closed Deep'])
    check('a live answer clears the streak, a fresh gone one starts it',
          [(e['role'], e.get('missing_since')) for e in listings[1:]],
          [('Live Deep', None), ('Closed Fresh', '2026-09-10')])
    check('only rows missing from the sweep are asked about', len(asked), 3)


@responses.activate
def test_workday_posting_state():
    base = 'https://acme.wd1.myworkdayjobs.com'
    api = base + '/wday/cxs/acme/Ext/job/Austin-TX/'
    responses.add(responses.GET, api + 'Live_R1',
                  json={'jobPostingInfo': {'canApply': True}})
    responses.add(responses.GET, api + 'Closed_R2', status=403,
                  json={'errorCode': 'S22', 'httpStatus': 403})
    responses.add(responses.GET, api + 'Unknown_R3', status=404,
                  json={'errorCode': 'S21', 'httpStatus': 404})
    responses.add(responses.GET, api + 'Flaky_R4', status=500, json={})
    public = base + '/Ext/job/Austin-TX/'
    check('a 200 with canApply is live', sj.workday_posting_state(public + 'Live_R1'), 'live')
    check('403 S22 is a closed req', sj.workday_posting_state(public + 'Closed_R2'), 'gone')
    check('404 S21 is an unknown path', sj.workday_posting_state(public + 'Unknown_R3'), 'gone')
    check('a 500 says nothing', sj.workday_posting_state(public + 'Flaky_R4'), None)
    check('a locale prefix is skipped',
          sj.workday_posting_state(base + '/en-US/Ext/job/Austin-TX/Live_R1'), 'live')
    check('a non-workday url says nothing',
          sj.workday_posting_state('https://boards.greenhouse.io/acme/jobs/1'), None)


def test_long_silent_board_retires_its_rows():
    """Lakera's board sat empty for 24 runs while its open row stayed up."""
    stats = [
        {'label': 'Lakera (ashby/lakera.ai)', 'status': 'zero', 'count': 0},
        {'label': 'Todyl (ashby/Todyl)', 'status': 'FAILED', 'count': 0},
        {'label': 'Quiet (greenhouse/quiet)', 'status': 'zero', 'count': 0},
        # One of two tenants is still posting, so the pair is not silent.
        {'label': 'Duo (workday/a)', 'status': 'zero', 'count': 0},
        {'label': 'Duo (workday/b)', 'status': 'ok', 'count': 5},
    ]
    history = {'Lakera (ashby/lakera.ai)': {'zero_runs': 24},
               'Todyl (ashby/Todyl)': {'zero_runs': sj.SILENT_BOARD_RUNS},
               'Quiet (greenhouse/quiet)': {'zero_runs': sj.SILENT_BOARD_RUNS - 1},
               'Duo (workday/a)': {'zero_runs': 30}}
    silent = sj.long_silent_boards(stats, history)
    check('only boards silent for SILENT_BOARD_RUNS on every tenant are silent',
          sorted(silent), [('Lakera', 'ashby'), ('Todyl', 'ashby')])

    ashby = 'https://jobs.ashbyhq.com/lakera.ai/b9dee2a0-9bb3-447e-9bce-2b1bed784e5b'
    listings = [
        _listing('Lakera', 'AI Security Engineer', ashby, source='Ashby'),
        _listing('Lakera', 'Maintainer Pick', ashby, source='Community'),
        _listing('Lakera', 'Other Board Row', 'https://boards.greenhouse.io/l/jobs/1'),
        _listing('Quiet', 'Not Silent Long Enough', 'https://boards.greenhouse.io/q/jobs/2'),
    ]
    retired = sj.retire_vanished_listings(listings, [], '2026-09-27', silent)
    check('a long-silent board retires its rows at once',
          [e['role'] for e in retired], ['AI Security Engineer'])
    check('the retired row is blanked the way the revive path expects',
          (listings[0]['url'], listings[0]['closed'], listings[0]['closed_date']),
          ('', True, '2026-09-27'))
    check('community, other-ATS and short-silence rows keep their url',
          [bool(e['url']) for e in listings[1:]], [True, True, True])


def test_greenhouse_remote_keeps_a_remote_label():
    def loc(label, *offices):
        return sj.greenhouse_location({'location': {'name': label},
                                       'offices': [{'name': o} for o in offices]})
    check('a department-style office does not replace Remote',
          loc('Remote', 'GuidePoint University (GPSU)'), 'Remote')
    check('a foreign remote scope still replaces Remote', loc('Remote', 'UK Remote'),
          'UK Remote')
    check('a foreign office still replaces Remote', loc('Remote', 'London'), 'London')
    check('only the place-like office parts survive',
          loc('Remote', 'Headquarters', 'Reston, VA'), 'Reston, VA')
    check('the office fallback for a non-remote label is unchanged',
          loc('Hybrid', 'Professional Services'), 'Professional Services')
    check('GPSU internship is accepted once its location stays Remote',
          sj.evaluate_job('GPSU Cybersecurity Spring Internship',
                          loc('Remote', 'GuidePoint University (GPSU)'), '', True),
          ('intern', 'Security Engineering'))


@responses.activate
def test_pinpoint_remote_is_us_only_for_usa_locations():
    responses.get('https://acme.pinpointhq.com/postings.json', json={'data': [
        {'id': '1', 'title': 'Associate SOC Analyst', 'workplace_type': 'remote',
         'location': {'city': 'Manchester', 'province': 'Greater Manchester',
                      'name': 'GBR Manchester Hardman Boulevard'}},
        {'id': '2', 'title': 'SOC Analyst', 'workplace_type': 'remote',
         'location': {'city': 'Remote', 'province': 'Illinois',
                      'name': 'USA Remote - Central Time'}},
        {'id': '3', 'title': 'SOC Analyst', 'workplace_type': 'remote',
         'location': {'city': 'Remote', 'province': 'Greater London',
                      'name': 'GBR Remote'}},
    ]})
    jobs = sj.scrape_pinpoint('Acme', 'acme')
    check('pinpoint keeps the real location of a non-US remote posting',
          [j['location'] for j in jobs],
          ['Manchester, Greater Manchester', 'Remote', 'Remote, Greater London'])
    check('only the USA remote posting reads as US',
          [sj.is_us_location(j['location']) for j in jobs], [False, True, False])


@responses.activate
def test_oracle_fetches_descriptions_for_candidates():
    host = 'acme.fa.us2.oraclecloud.com'
    base = f'https://{host}/hcmRestApi/resources/latest/'
    responses.get(base + 'recruitingCEJobRequisitions', json={'items': [{
        'TotalJobsCount': 3, 'requisitionList': [
            {'Id': '1', 'Title': 'Cyber Engineer Associate', 'PrimaryLocation': 'Reston, VA'},
            {'Id': '2', 'Title': 'Accountant', 'PrimaryLocation': 'Reston, VA'},
            {'Id': '3', 'Title': 'DevSecOps Engineer Associate',
             'PrimaryLocation': 'Reston, VA'}]}]})
    responses.get(base + 'recruitingCEJobRequisitionDetails', json={'items': [{
        'ExternalDescriptionStr': '<p>Requires an active TS/SCI with polygraph.</p>',
        'ExternalQualificationsStr': "<p>Bachelor's degree and 5 years of experience</p>"}]})
    original = sj.ORACLE_DETAIL_CAP
    try:
        sj.ORACLE_DETAIL_CAP = 1
        jobs = sj.scrape_oracle('Acme', host, 'CX_1')
    finally:
        sj.ORACLE_DETAIL_CAP = original
    detail_calls = [c.request.url for c in responses.calls
                    if 'recruitingCEJobRequisitionDetails' in c.request.url]
    check('oracle fetches detail for the first candidate only, within the cap',
          len(detail_calls), 1)
    check('oracle detail uses the ById finder',
          'ById%3BId%3D%221%22%2CsiteNumber%3DCX_1' in detail_calls[0], True)
    check('oracle description joins the body and qualifications',
          jobs[0].get('description'),
          "<p>Requires an active TS/SCI with polygraph.</p>\n\n"
          "<p>Bachelor's degree and 5 years of experience</p>")
    check('non-candidates and reqs past the cap carry no description',
          ['description' in j for j in jobs[1:]], [False, False])
    check('the description reaches the clearance flag',
          sj.requires_clearance(jobs[0]['title'], jobs[0]['description']), True)
    check('oracle keeps its internal req id out of the posting', '_req' in jobs[0], False)


# --- scrape_eightfold ----------------------------------------------------------
EF_SEARCH = 'https://acme.eightfold.ai/api/pcsx/search'
EF_DETAIL = 'https://acme.eightfold.ai/api/pcsx/position_details'


def _ef_page(positions, count):
    return {'status': 200, 'data': {'positions': positions, 'count': count}}


def _ef_pos(pid, name, level=None, locations=('Orlando, FL, US',)):
    pos = {'id': pid, 'name': name, 'locations': ['Orlando, FL'],
           'standardizedLocations': list(locations),
           'positionUrl': f'/careers/job/{pid}'}
    if level:
        pos['efcustomTextLevelofexperience'] = [level]
    return pos


def _ef_search(term, start, **kwargs):
    responses.get(EF_SEARCH, match=[responses.matchers.query_param_matcher(
        {'query': term, 'start': str(start), 'domain': 'acme.com',
         'location': 'United States'})], **kwargs)


@responses.activate
def test_eightfold_paginates_backs_off_and_gates_levels():
    first = [_ef_pos(i, f'Mechanical Engineer {i}', 'Experienced Professional')
             for i in range(1, 9)]
    first += [_ef_pos(9, 'Cybersecurity Intern', 'Co-op/Summer Intern'),
              _ef_pos(10, 'Associate Cyber Software Engineer', 'Experienced Professional')]
    _ef_search('cyber', 0, json=_ef_page(first, 12))
    _ef_search('cyber', 10, json=_ef_page(
        [_ef_pos(11, 'Cyber Systems Security Engineering Associate - Early Career',
                 '4 yr and up College', ('Colorado Springs, CO, US', 'Remote, US')),
         _ef_pos(12, 'Security Rep Sr - E3', 'Hourly/Non-Exempt')], 12))
    _ef_search('intern', 0, json=_ef_page([], 0))
    # microsoft.eightfold.ai answers 429 mid-sweep; the page must be retried,
    # not dropped, and a repeat of an id already read must not duplicate it.
    _ef_search('early career', 0, status=429)
    _ef_search('early career', 0, json=_ef_page(
        [_ef_pos(9, 'Cybersecurity Intern', 'Co-op/Summer Intern'),
         _ef_pos(13, 'Cyber Analyst I - Early Career', '4 yr and up College')], 2))
    responses.get(EF_DETAIL, json={'data': {'jobDescription': '<p>Pursuing a BS.</p>'}})

    jobs = sj.scrape_eightfold('Acme', 'acme', 'acme.com')
    check('eightfold skips the experienced-hire track and dedupes ids',
          [j['title'] for j in jobs],
          ['Cybersecurity Intern',
           'Cyber Systems Security Engineering Associate - Early Career',
           'Security Rep Sr - E3', 'Cyber Analyst I - Early Career'])
    check('eightfold id, url and board', (jobs[0]['id'], jobs[0]['url'], jobs[0]['board']),
          ('eightfold_acme_9', 'https://acme.eightfold.ai/careers/job/9', 'Eightfold'))
    check('eightfold joins standardized locations', jobs[1]['location'],
          'Colorado Springs, CO, US; Remote, US')
    detail_ids = [parse_qs(urlparse(c.request.url).query)['position_id'][0]
                  for c in responses.calls if c.request.url.startswith(EF_DETAIL)]
    check('eightfold fetches detail for title-level candidates only',
          sorted(detail_ids), ['11', '13', '9'])
    check('eightfold attaches the description', jobs[0].get('description'),
          '<p>Pursuing a BS.</p>')
    check('eightfold leaves a non-candidate without a description',
          'description' in jobs[2], False)


@responses.activate
def test_eightfold_none_vs_empty():
    for term in sj.EIGHTFOLD_TERMS:
        _ef_search(term, 0, json=_ef_page([], 0))
    check('eightfold empty board -> []', sj.scrape_eightfold('Acme', 'acme', 'acme.com'), [])
    responses.reset()
    responses.get(EF_SEARCH, status=403, json={'message': 'Not authorized for PCSX'})
    check('eightfold PCSX disabled -> None',
          sj.scrape_eightfold('Acme', 'acme', 'acme.com'), None)


@responses.activate
def test_eightfold_gives_up_after_sustained_429():
    responses.get(EF_SEARCH, status=429)
    check('eightfold sustained 429 -> None',
          sj.scrape_eightfold('Acme', 'acme', 'acme.com'), None)
    attempts = len(sj.RATE_LIMIT_DELAYS) + 1
    check('eightfold retries each term through every backoff step',
          len(responses.calls), attempts * len(sj.EIGHTFOLD_TERMS))


@responses.activate
def test_eightfold_schema_drift_is_empty_not_crash():
    responses.get(EF_SEARCH, json={'status': 200, 'data': None})
    check('eightfold null data -> []', sj.scrape_eightfold('Acme', 'acme', 'acme.com'), [])


# --- scrape_phenom -------------------------------------------------------------
PH_API = 'https://careers.acme.org/widgets'


def _ph_job(n, title='Mechanical Engineer', locations=('McLean, Virginia, United States',)):
    return {'jobId': f'R{n}', 'reqId': f'R{n}', 'jobSeqNo': f'ACMEUSR{n}EXTERNAL',
            'title': title, 'location': locations[0], 'multi_location': list(locations)}


def _ph_search(term, offset, jobs, total):
    responses.post(PH_API, match=[responses.matchers.json_params_matcher(
        {'ddoKey': 'refineSearch', 'keywords': term, 'from': offset,
         'lang': 'en_us', 'country': 'us'}, strict_match=False)],
        json={'refineSearch': {'status': 200, 'totalHits': total,
                               'data': {'jobs': jobs}}})


@responses.activate
def test_phenom_paginates_and_fetches_details():
    size = sj.PHENOM_PAGE_SIZE
    full = [_ph_job(n) for n in range(size - 1)]
    full.append(_ph_job(900, 'Embedded Security Intern - Electronics Prototype',
                        ('Bedford, Massachusetts, United States',
                         'McLean, Virginia, United States')))
    _ph_search('cyber', 0, full, size + 1)
    _ph_search('cyber', size, [_ph_job(901, 'Cyber Analyst I')], size + 1)
    _ph_search('intern', 0, [_ph_job(900, 'Embedded Security Intern - Electronics Prototype')], 1)
    responses.post(PH_API, match=[responses.matchers.json_params_matcher(
        {'ddoKey': 'jobDetail'}, strict_match=False)],
        json={'jobDetail': {'data': {'job': {'description': 'Requires 1 year.'}}}})

    jobs = sj.scrape_phenom('Acme', 'careers.acme.org', 'en_us', 'us')
    check('phenom pages past a full first page and dedupes across terms',
          len(jobs), size + 1)
    intern = next(j for j in jobs if j['id'] == 'phenom_careers.acme.org_R900')
    check('phenom url uses the site locale path', intern['url'],
          'https://careers.acme.org/us/en/job/R900')
    check('phenom joins multi_location', intern['location'],
          'Bedford, Massachusetts, United States; McLean, Virginia, United States')
    check('phenom attaches the jobDetail description', intern.get('description'),
          'Requires 1 year.')
    details = [json.loads(c.request.body) for c in responses.calls
               if json.loads(c.request.body).get('ddoKey') == 'jobDetail']
    check('phenom fetches detail for title-level candidates only',
          sorted(d['jobId'] for d in details), ['R900', 'R901'])
    check('phenom detail carries the job sequence number',
          next(d['jobSeqNo'] for d in details if d['jobId'] == 'R900'),
          'ACMEUSR900EXTERNAL')
    check('phenom drops its private fields', any('_seq' in j for j in jobs), False)


@responses.activate
def test_phenom_caps_pages_on_a_fuzzy_match():
    # BAE's 'cyber' query matches 1,843 postings; only the ranked prefix is read.
    size = sj.PHENOM_PAGE_SIZE
    for page in range(sj.PHENOM_MAX_PAGES + 2):
        _ph_search('cyber', page * size, [_ph_job(page * size + n) for n in range(size)],
                   5000)
    _ph_search('intern', 0, [], 0)
    jobs = sj.scrape_phenom('Acme', 'careers.acme.org', 'en_us', 'us')
    check('phenom stops at PHENOM_MAX_PAGES', len(jobs), sj.PHENOM_MAX_PAGES * size)


@responses.activate
def test_phenom_none_vs_empty():
    _ph_search('cyber', 0, [], 0)
    _ph_search('intern', 0, [], 0)
    check('phenom empty board -> []',
          sj.scrape_phenom('Acme', 'careers.acme.org', 'en_us', 'us'), [])
    responses.reset()
    responses.post(PH_API, status=404)
    check('phenom dead host -> None',
          sj.scrape_phenom('Acme', 'careers.acme.org', 'en_us', 'us'), None)


# --- scrape_jibe ---------------------------------------------------------------
JB_API = 'https://careers.acme.org/api/jobs'


def _jb_job(n, title='Mechanical Engineer', city='Laurel', state='Maryland',
            country='US', **extra):
    data = {'slug': str(n), 'req_id': str(n), 'title': title, 'city': city,
            'state': state, 'country_code': country,
            'description': f'<p>Posting {n}.</p>',
            'apply_url': f'https://careers-acme.icims.com/jobs/{n}/login', **extra}
    return {'data': data}


def _jb_search(term, page, jobs=(), total=None, **kwargs):
    if 'status' not in kwargs:
        kwargs['json'] = {'jobs': list(jobs), 'totalCount': total, 'count': total}
    responses.get(JB_API, match=[responses.matchers.query_param_matcher(
        {'keywords': term, 'page': str(page), 'limit': str(sj.JIBE_PAGE_SIZE)})], **kwargs)


@responses.activate
def test_jibe_paginates_maps_fields_and_keeps_the_crawl_delay():
    size = sj.JIBE_PAGE_SIZE
    _jb_search('cyber', 1, [_jb_job(n) for n in range(size)], size + 1)
    _jb_search('cyber', 2, [_jb_job(900, '2027 Internship - Cyber Security - Cyber Dominance',
                                    tags9=['Internship'])], size + 1)
    # Exelon's '(Various Exelon Locations)' reqs list their other sites in
    # additional_locations, one of them foreign here.
    _jb_search('cybersecurity', 1, [
        _jb_job(900, '2027 Internship - Cyber Security - Cyber Dominance'),
        _jb_job(901, '2027 Summer Internship - Cyber Security', city='OAKBROOK TERRACE',
                state='Illinois', categories=[{'name': 'Intern/Co-Op'}],
                additional_locations=[
                    {'city': 'Washington', 'state': 'Washington, DC', 'country_code': 'US'},
                    {'city': 'Toronto', 'state': 'Ontario', 'country_code': 'CA'}]),
        _jb_job(902, 'Cyber Analyst', city=None, state=None,
                tags2=['International Programs'])], 3)
    _jb_search('intern', 1, [_jb_job(903, 'Security Intern', city='London', state='England',
                                     country='GB')], 1)
    delays = []
    sj.time.sleep = delays.append
    try:
        jobs = sj.scrape_jibe('Acme', 'careers.acme.org')
    finally:
        sj.time.sleep = lambda *a, **k: None

    check('jibe pages past a full first page, dedupes, skips a foreign-only req',
          len(jobs), size + 3)
    intern = next(j for j in jobs if j['id'] == 'jibe_careers.acme.org_900')
    check('jibe links the Jibe job page, not the iCIMS login wall',
          (intern['url'], intern['board']), ('https://careers.acme.org/jobs/900', 'Jibe'))
    check('jibe keeps the payload description', intern['description'], '<p>Posting 900.</p>')
    check('jibe reads an intern tag as an intern hint', intern['intern_hint'], True)
    multi = next(j for j in jobs if j['id'].endswith('_901'))
    check('jibe joins US locations and drops foreign ones', multi['location'],
          'Oakbrook Terrace, Illinois; Washington, DC')
    check('jibe reads an intern category as an intern hint', multi['intern_hint'], True)
    bare = next(j for j in jobs if j['id'].endswith('_902'))
    check('jibe falls back to the country for a placeless US req', bare['location'],
          'United States')
    check('jibe does not read International as intern', bare['intern_hint'], False)
    check('jibe waits the crawl delay between requests', delays,
          [sj.JIBE_REQUEST_DELAY] * 3)
    check('jibe sweep that finished is not partial',
          any(j.get('partial_sweep') for j in jobs), False)


@responses.activate
def test_jibe_none_vs_empty():
    for term in sj.JIBE_TERMS:
        _jb_search(term, 1, [], 0)
    check('jibe empty board -> []', sj.scrape_jibe('Acme', 'careers.acme.org'), [])
    responses.reset()
    responses.get(JB_API, status=404)
    check('jibe dead host -> None', sj.scrape_jibe('Acme', 'careers.acme.org'), None)
    check('jibe rejects a host with a path', sj.scrape_jibe('X', 'evil.com/x'), None)


@responses.activate
def test_jibe_retries_429_and_flags_a_cut_short_sweep():
    _jb_search('cyber', 1, status=429)
    _jb_search('cyber', 1, [_jb_job(1, 'Cyber Analyst I')], 1)
    _jb_search('cybersecurity', 1, [], 0)
    # A term that fails outright leaves postings unseen, so the sweep is partial
    # and retire_vanished_listings must not read the gap as closures.
    _jb_search('intern', 1, status=500)
    jobs = sj.scrape_jibe('Acme', 'careers.acme.org')
    check('jibe retries a 429 page', [j['title'] for j in jobs], ['Cyber Analyst I'])
    check('jibe flags a sweep with a failed term', jobs[0].get('partial_sweep'), True)
    responses.reset()
    responses.get(JB_API, status=429)
    check('jibe sustained 429 -> None', sj.scrape_jibe('Acme', 'careers.acme.org'), None)
    check('jibe retries each term through every backoff step', len(responses.calls),
          (len(sj.RATE_LIMIT_DELAYS) + 1) * len(sj.JIBE_TERMS))


@responses.activate
def test_jibe_caps_pages():
    size = sj.JIBE_PAGE_SIZE
    for page in range(1, sj.JIBE_MAX_PAGES + 2):
        _jb_search('cyber', page, [_jb_job(page * size + n) for n in range(size)], 5000)
    _jb_search('cybersecurity', 1, [], 0)
    _jb_search('intern', 1, [], 0)
    jobs = sj.scrape_jibe('Acme', 'careers.acme.org')
    check('jibe stops at JIBE_MAX_PAGES', len(jobs), sj.JIBE_MAX_PAGES * size)
    check('jibe flags a capped sweep', all(j.get('partial_sweep') for j in jobs), True)


def test_jibe_plumbs_through_config_and_retirement():
    config = {'jibe': [{'name': 'J', 'host': 'careers.j.org', 'search_terms': ['reverse']}]}
    check('build_tasks passes jibe entries through',
          [(t.label, t.args) for t in sj.build_tasks(config, board='jibe')],
          [('J (jibe/careers.j.org)', ('J', 'careers.j.org', ['reverse']))])
    check('fingerprint Jibe',
          sj.job_fingerprint('Acme', 'Jibe', 'https://careers.acme.org/jobs/59772'),
          ('Acme', 'Jibe', '59772'))


def test_new_boards_validate_config_and_plumb_through():
    check('eightfold rejects a tenant with /',
          sj.scrape_eightfold('X', 'evil.com/', 'x.com'), None)
    check('eightfold rejects a domain with @',
          sj.scrape_eightfold('X', 'acme', 'a@b.com'), None)
    check('phenom rejects a host with a path',
          sj.scrape_phenom('X', 'evil.com/x', 'en_us', 'us'), None)
    check('phenom rejects a lang with /', sj.scrape_phenom('X', 'a.org', 'en/us', 'us'), None)
    config = {
        'eightfold': [{'name': 'E', 'tenant': 'e', 'domain': 'e.com',
                       'search_terms': ['security']}],
        'phenom': [{'name': 'P', 'host': 'jobs.p.com', 'lang': 'en_global',
                    'country': 'global'}],
    }
    check('build_tasks passes eightfold and phenom entries through',
          [(t.label, t.args) for t in sj.build_tasks(config)][:2],
          [('E (eightfold/e)', ('E', 'e', 'e.com', False, ['security'])),
           ('P (phenom/jobs.p.com)', ('P', 'jobs.p.com', 'en_global', 'global', False, None))])
    check('fingerprint Eightfold',
          sj.job_fingerprint('Acme', 'Eightfold',
                             'https://acme.eightfold.ai/careers/job/996476900832'),
          ('Acme', 'Eightfold', '996476900832'))
    check('fingerprint Phenom',
          sj.job_fingerprint('Acme', 'Phenom', 'https://careers.acme.org/us/en/job/R117365'),
          ('Acme', 'Phenom', 'R117365'))


@responses.activate
def test_main_writes_run_events_with_inserted_row():
    responses.get(
        'https://boards-api.greenhouse.io/v1/boards/acme/jobs',
        json={'jobs': [{'id': 7, 'title': 'Security Engineering Intern',
                        'location': {'name': 'Austin, TX'},
                        'absolute_url': 'https://boards.greenhouse.io/acme/jobs/7',
                        'content': 'Summer 2027 internship'}]})
    saved = (sj.LISTINGS_FILE, sj.SEEN_JOBS_FILE, sj.BOARD_BASELINE_FILE,
             sj.rebuild_readme.main, sys.argv, os.getcwd())
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'companies.yml').write_text('greenhouse:\n  - name: Acme\n    slug: acme\n')
        events_file = tmp / 'run_events.json'
        os.environ['RUN_EVENTS_FILE'] = str(events_file)
        try:
            os.chdir(tmp)
            sj.LISTINGS_FILE = tmp / 'listings.json'
            sj.SEEN_JOBS_FILE = tmp / 'seen_jobs.json'
            sj.BOARD_BASELINE_FILE = tmp / 'board_baseline.json'
            sj.rebuild_readme.main = lambda: None
            sys.argv = ['scrape_jobs.py', '--board', 'greenhouse']
            sj.main()
            events = json.loads(events_file.read_text())
        finally:
            del os.environ['RUN_EVENTS_FILE']
            (sj.LISTINGS_FILE, sj.SEEN_JOBS_FILE, sj.BOARD_BASELINE_FILE,
             sj.rebuild_readme.main, sys.argv, cwd) = saved
            os.chdir(cwd)
    check('events file carries the schema version', events['schema_version'], 1)
    check('events file lists the inserted row',
          [(r['company'], r['role'], r['type']) for r in events['added']],
          [('Acme', 'Security Engineering Intern', 'intern')])
    check('events file has no revived or retired rows',
          (events['revived'], events['retired']), ([], []))


# --- scrape_workable: the widget API has flat fields and locations[] ---------
WORKABLE_API = 'https://apply.workable.com/api/v1/widget/accounts/trailofbits'


def _workable_job(shortcode, title, *sites, telecommuting=False, city='', state='',
                  country='United States', description=''):
    return {'title': title, 'shortcode': shortcode, 'code': '',
            'employment_type': 'Full-time', 'telecommuting': telecommuting,
            'department': 'Assurance', 'url': f'https://apply.workable.com/j/{shortcode}',
            'shortlink': f'https://apply.workable.com/j/{shortcode}',
            'application_url': f'https://apply.workable.com/j/{shortcode}/apply',
            'published_on': '2026-08-27', 'created_at': '2026-08-27',
            'country': country, 'city': city, 'state': state, 'education': '',
            'experience': 'Entry level', 'function': '', 'industry': '',
            'locations': [{'country': c, 'countryCode': code, 'city': town,
                           'region': region, 'hidden': hidden}
                          for c, code, town, region, hidden in sites],
            'description': description}


US_ANYWHERE = ('United States', 'US', '', None, False)


@responses.activate
def test_workable_reads_locations_and_description():
    """Trail of Bits 'Security Engineer I, Application Security' never landed."""
    responses.get(WORKABLE_API, json={'name': 'Trail of Bits', 'description': '', 'jobs': [
        _workable_job('A1B2C3D4E5', 'Security Engineer I, Application Security', US_ANYWHERE,
                      telecommuting=True,
                      description='<p>Entry-level role on the application security team.</p>'),
        _workable_job('B1B2C3D4E5', 'Principal Scientist',
                      ('United States', 'US', 'Arlington', 'Virginia', False),
                      city='Arlington', state='Virginia'),
        _workable_job('C1B2C3D4E5', 'Security Engineer, Research',
                      ('United States', 'US', 'Portland', 'Oregon', False),
                      ('Croatia', 'HR', 'Zagreb', 'Grad Zagreb', False),
                      ('United States', 'US', 'Boston', 'Massachusetts', True)),
        _workable_job('D1B2C3D4E5', 'Senior Security Engineer Cryptography',
                      ('United Kingdom', 'GB', '', None, False),
                      telecommuting=True, country='United Kingdom'),
        _workable_job('E1B2C3D4E5', 'Security Analyst', city='Austin', state='Texas'),
        _workable_job('F1B2C3D4E5', 'SOC Analyst', US_ANYWHERE),
    ]})
    jobs = sj.scrape_workable('Trail of Bits', 'trailofbits')
    check('workable asks for descriptions in the same request',
          'details=true' in responses.calls[0].request.url, True)
    check('workable builds each location from locations[] and the flat fields',
          [j['location'] for j in jobs],
          ['Remote (US)', 'Arlington, Virginia',
           'Portland, Oregon; Zagreb, Grad Zagreb, Croatia',
           'United Kingdom', 'Austin, Texas', 'United States'])
    check('workable keeps the posting description',
          jobs[0]['description'], '<p>Entry-level role on the application security team.</p>')
    check('workable url and id use the shortcode',
          (jobs[0]['id'], jobs[0]['url']),
          ('workable_trailofbits_A1B2C3D4E5',
           'https://apply.workable.com/trailofbits/j/A1B2C3D4E5/'))
    check('a Workable req now passes the US filter, a UK one still fails',
          [sj.is_us_location(j['location']) for j in jobs[:4]], [True, True, True, False])
    check('the Trail of Bits entry-level req is accepted',
          sj.evaluate_job(jobs[0]['title'], jobs[0]['location'], jobs[0]['description'],
                          True), ('earlycareer', 'AppSec & ProdSec'))


# --- scrape_lever: requirement bullets live in `lists` ------------------------
@responses.activate
def test_lever_appends_lists_to_description():
    """Immuta 'Software Engineer II (Marketplace)' asks for 3 to 5 years in `lists`."""
    required = ('<div>\n\n<li><strong>Professional Experience:&nbsp;</strong>Typically '
                '3\u20135 years of professional software engineering experience.</li>\n'
                '<li>Proficiency with TypeScript.</li>\n\n</div>')
    responses.get('https://api.lever.co/v0/postings/immuta', json=[{
        'id': '7f6f1d3a-8f64-4a4e-9b1c-1a2b3c4d5e6f', 'text': 'Software Engineer II (Marketplace)',
        'country': 'US', 'categories': {'location': 'College Park, MD', 'commitment': 'Full Time'},
        'hostedUrl': 'https://jobs.lever.co/immuta/7f6f1d3a-8f64-4a4e-9b1c-1a2b3c4d5e6f',
        'descriptionPlain': 'Immuta is hiring a software engineer.',
        'lists': [{'text': 'CORE RESPONSIBILITIES', 'content': '<div><li>Build APIs.</li></div>'},
                  {'text': 'REQUIRED EXPERIENCE', 'content': required}]}])
    job = sj.scrape_lever('Immuta', 'immuta')[0]
    check('lever description keeps the intro and appends each list with its heading',
          job['description'].startswith('Immuta is hiring a software engineer.\n\n'
                                        'CORE RESPONSIBILITIES\n'), True)
    check('lever list html is stripped',
          ('<li>' in job['description'], 'Typically 3\u20135 years' in job['description']),
          (False, True))
    check('the years in lists now reach the experience gate',
          sj.evaluate_job(job['title'], job['location'], job['description'], True), None)
    check('...where the intro alone let the req through',
          sj.evaluate_job(job['title'], job['location'], 'Immuta is hiring.', True),
          ('earlycareer', 'Engineering @ Security Co'))


# --- scrape_smartrecruiters: descriptions for title-level candidates ---------
SR_POSTINGS = 'https://api.smartrecruiters.com/v1/companies/KudelskiSecurityInc/postings'
KUDELSKI_QUALIFICATIONS = (
    '<p>Qualifications<br />Education<br />*High School diploma, or equivalent '
    'experience/combined education, with additional specialized technical training '
    'equivalent to a technical Associate degree and/or demonstrated ability to perform '
    'assigned technical/para-engineering tasks and 3 years of experience<br />Experience'
    '<br />*2-3 years&apos; experience working with LAN and WAN topologies, TCP/IP '
    'protocol, SSL/TLS, OSI Model, firewalls, routers and switches required.</p>')


def _sr_posting(pid, name):
    return {'id': pid, 'name': name, 'uuid': f'uuid-{pid}', 'refNumber': f'REF{pid}',
            'ref': f'{SR_POSTINGS}/{pid}',
            'location': {'city': 'Atlanta', 'region': 'GA', 'country': 'us', 'remote': False},
            'typeOfEmployment': {'id': 'permanent', 'label': 'Full-time'}}


@responses.activate
def test_smartrecruiters_fetches_descriptions_for_candidates():
    """Kudelski 'Network Support Engineer I/II' wants 2 to 3 years."""
    responses.get(SR_POSTINGS, json={'offset': 0, 'limit': 100, 'totalFound': 3, 'content': [
        _sr_posting('114671999', 'Network Support Engineer I/II'),
        _sr_posting('114672000', 'Account Executive'),
        _sr_posting('114672001', 'Security Analyst I')]})
    responses.get(f'{SR_POSTINGS}/114671999', json={'id': '114671999', 'jobAd': {'sections': {
        'companyDescription': {'title': 'Company Description',
                               'text': '<p>Kudelski Security, Inc.</p>'},
        'jobDescription': {'title': 'Job Description', 'text': '<p>Support F5 customers.</p>'},
        'qualifications': {'title': 'Qualifications',
                           'text': KUDELSKI_QUALIFICATIONS},
        'additionalInformation': {'title': 'Additional Information', 'text': ''}}}})
    original = sj.SMARTRECRUITERS_DETAIL_CAP
    try:
        sj.SMARTRECRUITERS_DETAIL_CAP = 1
        jobs = sj.scrape_smartrecruiters('Kudelski Security', 'KudelskiSecurityInc')
    finally:
        sj.SMARTRECRUITERS_DETAIL_CAP = original
    detail_calls = [c.request.url for c in responses.calls if '/postings/' in c.request.url]
    check('smartrecruiters fetches detail for the first candidate only, within the cap',
          detail_calls, [f'{SR_POSTINGS}/114671999'])
    check('smartrecruiters description joins the jobAd sections with their titles',
          jobs[0]['description'],
          '<h3>Company Description</h3><p>Kudelski Security, Inc.</p>\n'
          '<h3>Job Description</h3><p>Support F5 customers.</p>\n'
          f'<h3>Qualifications</h3>{KUDELSKI_QUALIFICATIONS}')
    check('non-candidates and reqs past the cap carry no description',
          ['description' in j for j in jobs[1:]], [False, False])
    check('the posting years now reach the experience gate',
          sj.evaluate_job(jobs[0]['title'], jobs[0]['location'], jobs[0]['description'],
                          True), None)


# --- Workday: Motorola writes "More..." where others write "N Locations" -----
@responses.activate
def test_workday_more_suffix_fetches_locations():
    check('multi-location labels',
          [bool(sj.MULTI_LOCATION_RE.search(x)) for x in
           ('3 Locations', 'Chicago, IL, More...', 'Chicago, IL', 'Elmore...')],
          [True, True, False, False])
    path = '/job/Chicago-IL/Cybersecurity-Analyst-I_R59000'
    responses.add_callback(responses.POST, WD_API, callback=_workday_pages({'cyber': [{
        'total': 1, 'jobPostings': [{'title': 'Cybersecurity Analyst I', 'externalPath': path,
                                     'locationsText': 'Chicago, IL, More...'}]}]}))
    responses.get(f'https://t.wd5.myworkdayjobs.com/wday/cxs/t/B{path}', json={
        'jobPostingInfo': {'location': 'Chicago, IL',
                           'additionalLocations': ['Plantation, FL', 'Allen, TX'],
                           'jobDescription': '<p>Entry-level SOC role.</p>'}})
    jobs = sj.scrape_workday('Motorola Solutions', 't', 'wd5', 'B')
    check('a trailing More... takes every site from the detail endpoint',
          jobs[0]['location'], 'Chicago, IL; Plantation, FL; Allen, TX')


# --- amazon.jobs: loc_query ranks, the country filter restricts --------------
@responses.activate
def test_amazon_restricts_to_us_reqs():
    responses.get('https://www.amazon.jobs/en/search.json', json={'hits': 0, 'jobs': []})
    check('an empty amazon search is an empty board', sj.scrape_amazon(), [])
    query = parse_qs(urlparse(responses.calls[0].request.url).query)
    check('amazon search filters to US reqs', query.get('normalized_country_code[]'), ['USA'])


# --- rows whose board left companies.yml --------------------------------------
def test_retire_orphaned_listings():
    """Todyl's Ashby entry was dropped in 4e80f86 and its row stayed open."""
    config = {'ashby': [{'name': 'Lakera', 'slug': 'lakera.ai'}],
              'greenhouse': [{'name': 'Acme', 'slug': 'acme'}]}
    ashby = 'https://jobs.ashbyhq.com/Todyl/7ebf4aa1-b1eb-451d-a81e-c1f49336a8a3'
    listings = [
        _listing('Todyl', 'Site Reliability Engineer II', ashby, source='Ashby'),
        _listing('Lakera', 'AI Security Engineer', ashby, source='Ashby'),
        _listing('ACME', 'Case Differs', 'https://boards.greenhouse.io/acme/jobs/1'),
        _listing('Acme', 'Moved Off Workday', 'https://acme.wd1.myworkdayjobs.com/x/job/y',
                 source='Workday'),
        _listing('Amazon', 'Security Engineer I', 'https://www.amazon.jobs/en/jobs/1',
                 source='Amazon Jobs'),
        _listing('Todyl', 'Maintainer Pick', 'https://todyl.com/careers', source='Community'),
        _listing('Todyl', 'Unknown Feed', 'https://todyl.com/jobs/1', source='Some Feed'),
        _listing('Todyl', 'Already Closed', '', source='Ashby', closed=True),
    ]
    retired = sj.retire_orphaned_listings(listings, config, '2026-09-28')
    check('rows whose company has no board for their source retire',
          [e['role'] for e in retired], ['Site Reliability Engineer II', 'Moved Off Workday'])
    check('an orphaned row is blanked the way the revive path expects',
          (listings[0]['url'], listings[0]['closed'], listings[0]['closed_date']),
          ('', True, '2026-09-28'))
    check('configured, Amazon, Community, unknown-source and closed rows are untouched',
          [bool(e['url']) for e in listings[1:3] + listings[4:7]], [True] * 5)
    check('an empty config retires nothing',
          sj.retire_orphaned_listings([_listing('Todyl', 'X', ashby, source='Ashby')], {},
                                      '2026-09-28'), [])


# --- check_links: a 200 whose title says the job is gone ----------------------
@responses.activate
def test_check_links_soft_404():
    def page(url, title, content_type='text/html;charset=utf-8'):
        body = '<html><head>' + ('' if title is None else f'<title>{title}</title>')
        responses.get(url, status=200, body=body + '</head><body></body></html>',
                      content_type=content_type)

    bofa = 'https://careers.bankofamerica.com/en-us/students/job-detail/99999/x'
    page(bofa, '404 Page not found')
    page('https://jobs.hii-tsd.com/job/x/1391900000/', '')
    page('https://jobs.hii-tsd.com/job/x/1391937800/',
         'Cyberspace Operations Analyst 1 Job Details | HII&#39;s Mission Technologies')
    page('https://acme.com/expired', ' This job has expired\n')
    page('https://acme.com/no-title', None)
    page('https://acme.com/pdf', '', content_type='application/pdf')
    check('a 200 with a not-found title or an empty title is a soft 404',
          [check_links.fetch_status(r.url, soft_404=True) for r in responses.registered()],
          [404, 404, 200, 404, 200, 200])
    check('without soft_404 the raw status stands',
          check_links.fetch_status(bofa), 200)

    row = {'company': 'Bank of America', 'role': 'Analyst', 'source': 'Community', 'url': bofa}
    check('a soft 404 starts the streak like a real one',
          check_links.record_result(row, check_links.fetch_status(bofa, soft_404=True),
                                    '2026-09-27'), False)
    check('and closes the row on the next day',
          check_links.record_result(row, check_links.fetch_status(bofa, soft_404=True),
                                    '2026-09-28'), True)


# --- a board that leaves the config leaves the baseline -----------------------
def test_board_health_forgets_a_removed_board():
    """USAJOBS left the scrape with a 26-run zero streak in the baseline."""
    baseline = {'USAJOBS (data.usajobs.gov)': {'count': 0, 'zero_runs': 26,
                                               'last_nonzero': None},
                'Acme (greenhouse/acme)': {'count': 4, 'zero_runs': 0,
                                           'last_nonzero': '2026-09-27'}}
    stats = [{'label': 'Acme (greenhouse/acme)', 'status': 'ok', 'count': 5}]
    history, regressed, dead = sj.board_health(stats, baseline, '2026-09-28')
    check('a removed board drops out of the rolled baseline', sorted(history),
          ['Acme (greenhouse/acme)'])
    check('a removed board is neither regressed nor dead', (regressed, dead), ([], []))
    check('a removed board is not silent', sj.long_silent_boards(stats, baseline), set())
    check('the scrape no longer builds a USAJOBS task',
          [t.label for t in sj.build_tasks({}, board='usajobs')], [])


def test_compare_runs_reports_retirements():
    board = 'Checking Acme (greenhouse/acme)... ok (12 postings, 1.0s)\n'
    before = compare_runs.parse_log(board + '  RETIRED [vanished] Acme — Old Req\n')
    after = compare_runs.parse_log(board + '  RETIRED [vanished] Acme — Old Req\n'
                                   '  RETIRED [orphaned] Todyl — Site Reliability Engineer II\n')
    check('compare_runs lists a retirement only one run made',
          compare_runs.diff(before, after),
          ['Existing rows retired only after (1):',
           '  - Todyl — Site Reliability Engineer II [orphaned]', ''])


# --- Oracle: one host can carry two sites, one site name two hosts ------------
def test_oracle_ids_and_labels_carry_host_and_site():
    config = {'oracle': [
        {'name': 'Idaho National Laboratory', 'host': 'inl.fa.us2.oraclecloud.com',
         'site': 'CX_1001'},
        {'name': 'Idaho National Laboratory', 'host': 'inl.fa.us2.oraclecloud.com',
         'site': 'CX_1002'}]}
    check('two sites on one host get two labels, so two baseline keys',
          [t.label for t in sj.build_tasks(config, board='oracle')],
          ['Idaho National Laboratory (oracle/inl.fa.us2.oraclecloud.com/CX_1001)',
           'Idaho National Laboratory (oracle/inl.fa.us2.oraclecloud.com/CX_1002)'])
    check('an oracle label maps back to its pre-site key',
          sj._legacy_label('SAIC (oracle/eihu.fa.us8.oraclecloud.com/CX)'),
          'SAIC (oracle/eihu.fa.us8.oraclecloud.com)')
    check('other labels have no legacy key',
          [sj._legacy_label(x) for x in ('SAIC (oracle/eihu.fa.us8.oraclecloud.com)',
                                         'Acme (greenhouse/acme)', 'Amazon (amazon.jobs)')],
          [None, None, None])


@responses.activate
def test_oracle_ids_differ_across_hosts_on_one_site():
    for host in ('amex.fa.us2.oraclecloud.com', 'honeywell.fa.us2.oraclecloud.com'):
        responses.get(f'https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions',
                      json={'items': [{'TotalJobsCount': 1, 'requisitionList': [
                          {'Id': '77', 'Title': 'Accountant', 'PrimaryLocation': 'Austin, TX'}]}]})
    amex = sj.scrape_oracle('Amex', 'amex.fa.us2.oraclecloud.com', 'CX_1')[0]
    honeywell = sj.scrape_oracle('Honeywell', 'honeywell.fa.us2.oraclecloud.com', 'CX_1')[0]
    check('the same req number on site CX_1 of two hosts gets two ids',
          (amex['id'], honeywell['id']),
          ('oracle_amex.fa.us2.oraclecloud.com_CX_1_77',
           'oracle_honeywell.fa.us2.oraclecloud.com_CX_1_77'))
    check('each keeps its pre-host id for seen_jobs.json',
          (amex['legacy_id'], honeywell['legacy_id']), ('oracle_CX_1_77', 'oracle_CX_1_77'))


def test_board_health_carries_an_oracle_board_across_the_label_change():
    old = 'SAIC (oracle/eihu.fa.us8.oraclecloud.com)'
    new = 'SAIC (oracle/eihu.fa.us8.oraclecloud.com/CX)'
    baseline = {old: {'count': 40, 'zero_runs': 0, 'last_nonzero': '2026-09-27'},
                'JPMorgan Chase (oracle/jpmc.fa.oraclecloud.com)': {
                    'count': 0, 'zero_runs': sj.SILENT_BOARD_RUNS, 'last_nonzero': None}}
    healthy = [{'label': new, 'status': 'ok', 'count': 41}]
    history, regressed, dead = sj.board_health(healthy, baseline, '2026-09-28')
    check('a renamed healthy board raises nothing and is stored under its new label',
          (regressed, dead, sorted(history)), ([], [], [new]))
    history, regressed, _ = sj.board_health(
        [{'label': new, 'status': 'zero', 'count': 0}], baseline, '2026-09-28')
    check('a renamed board that empties still reports a real regression',
          (regressed, history[new]['last_nonzero']), ([(new, 40)], '2026-09-27'))
    stats = [{'label': 'JPMorgan Chase (oracle/jpmc.fa.oraclecloud.com/CX_1001)',
              'status': 'zero', 'count': 0}]
    check('a silent streak survives the rename',
          sj.long_silent_boards(stats, baseline), {('JPMorgan Chase', 'oracle')})
    check('the streak carries into the new key',
          sj.board_health(stats, baseline, '2026-09-28')[0][stats[0]['label']]['zero_runs'],
          sj.SILENT_BOARD_RUNS + 1)


@responses.activate
def test_main_carries_seen_oracle_reqs_to_the_new_id():
    host = 'eihu.fa.us8.oraclecloud.com'
    url = f'https://{host}/hcmUI/CandidateExperience/en/sites/CX/job/5'
    responses.get(f'https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions',
                  json={'items': [{'TotalJobsCount': 2, 'requisitionList': [
                      {'Id': '5', 'Title': 'Cybersecurity Analyst Intern',
                       'PrimaryLocation': 'Reston, VA'},
                      {'Id': '6', 'Title': 'Security Operations Center Intern',
                       'PrimaryLocation': 'Reston, VA'}]}]})
    responses.get(f'https://{host}/hcmRestApi/resources/latest/'
                  'recruitingCEJobRequisitionDetails', json={'items': []})
    saved = (sj.LISTINGS_FILE, sj.SEEN_JOBS_FILE, sj.BOARD_BASELINE_FILE,
             sj.rebuild_readme.main, sys.argv, os.getcwd())
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'companies.yml').write_text(
            f'oracle:\n  - name: SAIC\n    host: {host}\n    site: CX\n')
        # Req 5 was judged under its old id and has left the board since; a
        # new id alone must not bring it back.
        (tmp / 'listings.json').write_text('[]')
        (tmp / 'seen_jobs.json').write_text(json.dumps({'oracle_CX_5': '2026-09-27'}))
        events_file = tmp / 'run_events.json'
        os.environ['RUN_EVENTS_FILE'] = str(events_file)
        try:
            os.chdir(tmp)
            sj.LISTINGS_FILE = tmp / 'listings.json'
            sj.SEEN_JOBS_FILE = tmp / 'seen_jobs.json'
            sj.BOARD_BASELINE_FILE = tmp / 'board_baseline.json'
            sj.rebuild_readme.main = lambda: None
            sys.argv = ['scrape_jobs.py', '--board', 'oracle']
            sj.main()
            events = json.loads(events_file.read_text())
            seen = json.loads((tmp / 'seen_jobs.json').read_text())
            rows = json.loads((tmp / 'listings.json').read_text())
        finally:
            del os.environ['RUN_EVENTS_FILE']
            (sj.LISTINGS_FILE, sj.SEEN_JOBS_FILE, sj.BOARD_BASELINE_FILE,
             sj.rebuild_readme.main, sys.argv, cwd) = saved
            os.chdir(cwd)
    check('a req seen under its old id is not judged or announced again',
          ([r['role'] for r in events['added']], [r['url'] for r in rows]),
          (['Security Operations Center Intern'], [url.replace('/job/5', '/job/6')]))
    check('seen_jobs.json carries the req under its new id',
          f'oracle_{host}_CX_5' in seen, True)


# --- persistence passes: probes, matching, re-adds and renames -----------------
@responses.activate
def test_workday_posting_survives_a_non_dict_body():
    api = 'https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Ext/job/Austin-TX/'
    public = 'https://acme.wd1.myworkdayjobs.com/Ext/job/Austin-TX/'
    responses.add(responses.GET, api + 'Null_R1', body='null',
                  content_type='application/json')
    responses.add(responses.GET, api + 'List_R2', status=403, json=[])
    responses.add(responses.GET, api + 'Live_R3', json={'jobPostingInfo': {
        'canApply': True, 'title': 'Security Specialist II', 'location': 'Cambridge, MA'}})
    check('a JSON null body says nothing', sj.workday_posting(public + 'Null_R1'),
          (None, None))
    check('a JSON list body says nothing', sj.workday_posting_state(public + 'List_R2'), None)
    state, info = sj.workday_posting(public + 'Live_R3')
    check('a live answer carries the posting info',
          (state, info.get('title')), ('live', 'Security Specialist II'))


def test_workday_fingerprint_survives_a_location_move():
    root = 'https://acme.wd1.myworkdayjobs.com/Ext'
    cases = [
        ('/job/Chantilly-VA/Cyber-Analyst-I_R123', 'R123'),
        ('/job/Reston-VA/Cyber-Analyst-I-Updated_R123', 'R123'),
        ('/job/Remote/Software-Engineer-2_JR102090', 'JR102090'),
        ('/job/Remote/Intern---Threat-Intelligence_JR-013988-1', 'JR-013988-1'),
        ('/job/Austin-TX/Security-Analyst_R-00123', 'R-00123'),
        # Arctic Wolf ids carry their own underscore.
        ('/job/Waterloo-ON-CAN/Professional-Services-Engineer-1_R26_1068', 'R26_1068'),
        ('/job/Cyber-Analyst_R77', 'R77'),
        # No '_<id>' suffix: the whole path is all there is to go on.
        ('/job/Austin-TX/Cyber-Analyst', '/job/Austin-TX/Cyber-Analyst'),
    ]
    for path, want in cases:
        check(f'workday fingerprint {path}', sj.job_fingerprint('Acme', 'Workday', root + path),
              ('Acme', 'Workday', want))
    moved = [_listing('Acme', 'Cyber Analyst I', root + '/job/Chantilly-VA/Cyber-Analyst-I_R123',
                      source='Workday', missing_since='2026-09-01')]
    raw = [{'company': 'Acme', 'board': 'Workday',
            'url': root + '/job/Reston-VA/Cyber-Analyst-I_R123'}]
    check('a req whose location slug moved is not vanished',
          (sj.retire_vanished_listings(moved, raw, '2026-09-10'),
           moved[0].get('missing_since')), ([], None))


for fn in (test_greenhouse, test_greenhouse_http_error_returns_none, test_lever,
           test_ashby, test_ashby_schema_drift_warns,
           test_smartrecruiters_pagination_short_page_stops,
           test_check_slugs_flags_unknown_smartrecruiters_id, test_oracle,
           test_fetch_json_retries_transient, test_fetch_json_gives_up_on_404,
           test_slug_validation_blocks_host_reparenting,
           test_workday_total_failure_returns_none,
           test_smartrecruiters_missing_total_keeps_paging,
           test_amazon_description_includes_qualifications,
           test_reevaluate_drops_rows_the_pipeline_now_rejects,
           test_reevaluate_refreshes_category_type_and_clearance,
           test_reevaluate_guardrails_keep_rows,
           test_reevaluate_matches_a_moved_req_by_dedup_key,
           test_job_fingerprint_reads_every_ats_url_shape,
           test_retire_vanished_listings,
           test_retire_vanished_needs_the_whole_board_not_one_posting,
           test_repair_broken_locations,
           test_scrape_boards_preserves_config_order,
           test_build_tasks_honors_board_and_limit, test_board_health_streaks,
           test_board_health_migrates_and_survives_a_corrupt_baseline,
           test_compare_runs_reports_flips_only,
           test_check_links_closes_after_two_dead_days,
           test_workday_total_only_on_first_page, test_workday_flags_a_cut_short_sweep,
           test_incomplete_sweep_retires_nothing,
           test_partial_workday_sweep_asks_the_detail_endpoint, test_workday_posting_state,
           test_long_silent_board_retires_its_rows,
           test_greenhouse_remote_keeps_a_remote_label,
           test_pinpoint_remote_is_us_only_for_usa_locations,
           test_oracle_fetches_descriptions_for_candidates,
           test_eightfold_paginates_backs_off_and_gates_levels,
           test_eightfold_none_vs_empty, test_eightfold_gives_up_after_sustained_429,
           test_eightfold_schema_drift_is_empty_not_crash,
           test_phenom_paginates_and_fetches_details,
           test_phenom_caps_pages_on_a_fuzzy_match, test_phenom_none_vs_empty,
           test_jibe_paginates_maps_fields_and_keeps_the_crawl_delay,
           test_jibe_none_vs_empty, test_jibe_retries_429_and_flags_a_cut_short_sweep,
           test_jibe_caps_pages, test_jibe_plumbs_through_config_and_retirement,
           test_new_boards_validate_config_and_plumb_through,
           test_main_writes_run_events_with_inserted_row,
           test_workable_reads_locations_and_description,
           test_lever_appends_lists_to_description,
           test_smartrecruiters_fetches_descriptions_for_candidates,
           test_workday_more_suffix_fetches_locations, test_amazon_restricts_to_us_reqs,
           test_retire_orphaned_listings, test_check_links_soft_404,
           test_board_health_forgets_a_removed_board,
           test_compare_runs_reports_retirements,
           test_oracle_ids_and_labels_carry_host_and_site,
           test_oracle_ids_differ_across_hosts_on_one_site,
           test_board_health_carries_an_oracle_board_across_the_label_change,
           test_main_carries_seen_oracle_reqs_to_the_new_id,
           test_workday_posting_survives_a_non_dict_body,
           test_workday_fingerprint_survives_a_location_move):
    fn()

if failures:
    print(f'\n{failures} scraper test(s) failed')
    sys.exit(1)
print('All scraper parser tests passed')
