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


def test_greenhouse_location_reads_only_location_fields():
    """Dropbox 'Career Page Allocation' and Fastly 'Work Location Type' are not places."""
    def loc(label, *metadata):
        return sj.greenhouse_location({'location': {'name': label}, 'offices': [],
                                       'metadata': [{'name': n, 'value': v}
                                                    for n, v in metadata]})
    check('a non-location field holding "location" is ignored',
          loc('Hybrid', ('Career Page Allocation', 'Sales'), ('Location Cost Tier', 'Mid'),
              ('Work Location Type', 'Hybrid'), ('Location Type', 'On-Site')),
          'Hybrid')
    check('the real location fields are read and joined',
          loc('Hybrid', ('Location Type', 'Hybrid'), ('Primary Location', 'Austin, TX'),
              ('Additional Locations', ['Boston, MA']),
              ('Additional Job Post Location', ['Reston, VA'])),
          'Austin, TX; Boston, MA; Reston, VA')


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


@responses.activate
def test_lever_reads_all_locations():
    """Saviynt files a Vancouver + Milpitas req under country CA."""
    def posting(pid, country, primary, *others):
        return {'id': pid, 'text': 'Security Engineer', 'country': country,
                'categories': {'location': primary, 'allLocations': [primary, *others]},
                'hostedUrl': f'https://jobs.lever.co/acme/{pid}'}
    responses.get('https://api.lever.co/v0/postings/acme', json=[
        posting('a', 'CA', 'Vancouver', 'Milpitas, California'),
        posting('b', 'US', 'Wichita Metro Area', 'San Diego, California', 'Dallas, Texas'),
        posting('c', 'CA', 'Vancouver'),
        posting('d', 'DK', 'Remote Denmark', 'Remote Sweden'),
        {'id': 'e', 'text': 'Security Analyst', 'country': 'US',
         'categories': {'location': 'Austin, TX'}, 'hostedUrl': 'x'},
    ])
    jobs = sj.scrape_lever('Acme', 'acme')
    check('lever joins allLocations and skips only a req with no US site',
          [(j['id'], j['location']) for j in jobs],
          [('lever_acme_a', 'Vancouver; Milpitas, California'),
           ('lever_acme_b', 'Wichita Metro Area; San Diego, California; Dallas, Texas'),
           ('lever_acme_e', 'Austin, TX')])
    check('each kept req passes the US filter',
          [sj.is_us_location(j['location']) for j in jobs], [True, True, True])


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


def _ashby_address(city, region, country='United States'):
    return {'postalAddress': {'addressLocality': city, 'addressRegion': region,
                              'addressCountry': country}}


@responses.activate
def test_ashby_falls_back_to_the_postal_address():
    """Bare 'San Mateo' and 'North America' failed the US check beside a US address."""
    responses.get('https://api.ashbyhq.com/posting-api/job-board/acme', json={'jobs': [
        {'id': 'a', 'title': 'Security Engineer', 'location': 'San Mateo',
         'address': _ashby_address('San Mateo', 'California'),
         'secondaryLocations': [
             {'location': 'Ann Arbor', 'address': _ashby_address('Ann Arbor', 'Michigan')},
             {'location': 'London', 'address': _ashby_address('London', 'Greater London',
                                                              'United Kingdom')}]},
        {'id': 'b', 'title': 'Security Analyst', 'location': 'North America',
         'address': {'postalAddress': {'addressCountry': 'United States'}}},
        {'id': 'c', 'title': 'Security Engineer', 'location': 'San Francisco',
         'address': _ashby_address('San Francisco', 'California')},
        {'id': 'd', 'title': 'Security Engineer', 'location': 'Toronto',
         'address': _ashby_address('Toronto', 'Ontario', 'Canada')},
        {'id': 'e', 'title': 'Product Security Engineer', 'location': 'United States & Canada',
         'address': _ashby_address('San Francisco', 'California')},
        {'id': 'f', 'title': 'Security Engineer', 'location': '',
         'address': _ashby_address('Oakland', 'CA')},
    ]})
    jobs = sj.scrape_ashby('Acme', 'acme')
    check('ashby builds City, ST from a US address when the label fails',
          [j['location'] for j in jobs],
          ['San Mateo, CA; Ann Arbor, MI; London', 'North America; United States',
           'San Francisco', 'Toronto', 'United States & Canada; United States',
           'Oakland, CA'])
    check('only the US postings pass the US filter',
          [sj.is_us_location(j['location']) for j in jobs],
          [True, True, True, False, True, True])


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


@responses.activate
def test_fetch_json_retries_5xx():
    """One transient Workday 502 ended a whole search term."""
    responses.post('https://api.test/wd', status=502)
    responses.post('https://api.test/wd', json={'jobPostings': []})
    check('fetch_json retries a 502 then succeeds',
          sj.fetch_json('https://api.test/wd', method='POST', label='t'),
          {'jobPostings': []})
    responses.get('https://api.test/down', status=500)
    check('fetch_json gives up on a 500 that persists',
          sj.fetch_json('https://api.test/down', label='t'), None)
    check('fetch_json tries a persistent 500 MAX_RETRIES times',
          len([c for c in responses.calls if c.request.url.endswith('/down')]),
          sj.MAX_RETRIES)


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
                          'location': {'country': 'us', 'remote': True}}]}  # short page
    responses.get('https://api.smartrecruiters.com/v1/companies/Acme/postings', json=page1)
    responses.get('https://api.smartrecruiters.com/v1/companies/Acme/postings', json=page2)
    jobs = sj.scrape_smartrecruiters('Acme', 'Acme')
    check('smartrecruiters paged past a full first page with no total', len(jobs), 101)


@responses.activate
def test_smartrecruiters_remote_is_us_only_for_us_postings():
    """Sectigo's remote 'Software Engineer (Java)' is in Iasi, Romania."""
    def posting(pid, name, **loc):
        return {'id': pid, 'name': name, 'location': loc}
    responses.get('https://api.smartrecruiters.com/v1/companies/Sectigo/postings', json={
        'totalFound': 4, 'content': [
            posting('1', 'Software Engineer (Java)', city='Iași', region='IS', country='ro',
                    remote=True, fullLocation='Iași, IS, Romania'),
            posting('2', 'Network Engineer', city='Manchester', region='England',
                    country='gb', remote=True),
            posting('3', 'Channel Sales Engineer', city='Austin', region='TX', country='us',
                    remote=True),
            posting('4', 'Security Analyst', city='Roseland', region='NJ', country='us',
                    remote=False)]})
    jobs = sj.scrape_smartrecruiters('Sectigo', 'Sectigo')
    check('smartrecruiters skips remote postings outside the US',
          [(j['title'], j['location']) for j in jobs],
          [('Channel Sales Engineer', 'Remote (US)'), ('Security Analyst', 'Roseland, NJ')])


RECRUITEE_API = 'https://aikidosecurity.recruitee.com/api/offers/'


def _recruitee_offer(oid, title, sites, remote=False, **extra):
    """An offer in the live shape: flat fields from the first site plus `locations`."""
    code, state, city, country = sites[0]
    offer = {'id': oid, 'slug': f'offer-{oid}', 'title': title, 'remote': remote,
             'city': city, 'country': country, 'country_code': code, 'state_code': state,
             'state_name': state, 'careers_url': f'https://aikidosecurity.recruitee.com/o/{oid}',
             'locations': [{'country_code': c, 'state_code': s, 'city': ci, 'country': co}
                           for c, s, ci, co in sites],
             'description': '<p>Join the team.</p>', 'requirements': ''}
    offer.update(extra)
    return offer


@responses.activate
def test_recruitee_reads_state_sites_and_description():
    """Aikido's remote Customer Success Engineers in Romania and Dubai read as US."""
    ro = ('RO', 'B', 'Bucharest', 'Romania')
    responses.get(RECRUITEE_API, json={'offers': [
        _recruitee_offer(1, 'Customer Success Engineer Romania', [ro], remote=True),
        _recruitee_offer(2, 'Customer Success Engineer Dubai',
                         [('AE', 'DU', 'Dubai', 'United Arab Emirates')], remote=True),
        _recruitee_offer(3, 'Security Engineer I', [('US', 'VA', 'Herndon', 'United States')],
                         requirements='<p>0 to 2 years of experience.</p>'),
        _recruitee_offer(4, 'Channel Business Manager Austin',
                         [('US', 'TX', 'Austin', 'United States')], remote=True),
        _recruitee_offer(5, 'Analyst Relations Lead',
                         [('BE', 'VOV', 'Ghent', 'Belgium'),
                          ('US', 'IL', 'Chicago', 'United States')]),
        _recruitee_offer(6, 'Site Reliability Engineer', [('BE', 'VOV', 'Ghent', 'Belgium')]),
    ]})
    jobs = sj.scrape_recruitee('Aikido Security', 'aikidosecurity')
    check('recruitee keeps only offers with a US site, remote or not',
          [(j['title'], j['location']) for j in jobs],
          [('Security Engineer I', 'Herndon, VA'),
           ('Channel Business Manager Austin', 'Remote (US)'),
           ('Analyst Relations Lead', 'Ghent, Belgium; Chicago, IL')])
    check('a US city with its state code passes the US filter',
          [sj.is_us_location(j['location']) for j in jobs], [True, True, True])
    check('recruitee joins description and requirements', jobs[0]['description'],
          '<p>Join the team.</p>\n<p>0 to 2 years of experience.</p>')
    check('an offer with no requirements keeps its description alone',
          jobs[1]['description'], '<p>Join the team.</p>')
    check('a flat-field offer with no locations[] still reads its state',
          sj.recruitee_location({'city': 'Herndon', 'country': 'United States',
                                 'state_code': 'VA'}), 'Herndon, VA')


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
    # Only a row with no fingerprint may be matched by key: a fingerprinted
    # row missing from the feed is not judged by a sibling req.
    listings = [_stored('Acme', 'Security Engineer II', 1,
                        url='https://acme.example/careers/security-engineer-ii')]
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
          {'count': 7, 'zero_runs': 0, 'empty_runs': 0, 'last_nonzero': '2026-09-20'})
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


def _partial_flags(jobs):
    return sorted({bool(j.get('partial_sweep')) for j in jobs or []})


@responses.activate
def test_eightfold_prefers_a_city_over_a_state_only_standardized_location():
    """Lockheed 'Software Cyber Engineer - SWE 0' read 'MD,US' for Hanover, MD."""
    def pos(pid, std, raw):
        return {'id': pid, 'name': 'Software Cyber Engineer - SWE 0',
                'standardizedLocations': std, 'locations': raw}
    _ef_search('cyber', 0, json=_ef_page([
        pos(1, ['MD,US'], ['Hanover, MD']),
        pos(2, ['MD,US', 'Orlando, FL, US'], ['Annapolis Junction, MD', 'Orlando, FL']),
        pos(3, ['Redmond, WA, US'], ['United States, Washington, Redmond']),
        pos(4, ['US', 'Redmond, WA, US'],
            ['United States, Multiple Locations, Multiple Locations',
             'United States, Washington, Redmond']),
        pos(5, [], ['Hanover, MD'])], 5))
    for term in ('intern', 'early career'):
        _ef_search(term, 0, json=_ef_page([], 0))
    responses.get(EF_DETAIL, json={'data': {}})
    jobs = sj.scrape_eightfold('Lockheed Martin', 'acme', 'acme.com')
    check('eightfold takes the raw twin of a state-only standardized entry',
          [j['location'] for j in jobs],
          ['Hanover, MD', 'Annapolis Junction, MD; Orlando, FL, US', 'Redmond, WA, US',
           'US; Redmond, WA, US', 'Hanover, MD'])


@responses.activate
def test_eightfold_flags_a_cut_short_sweep():
    """Microsoft 'security' matches 970 postings against a 500 cap."""
    def run(max_pages):
        original = sj.EIGHTFOLD_MAX_PAGES
        try:
            sj.EIGHTFOLD_MAX_PAGES = max_pages
            return sj.scrape_eightfold('Acme', 'acme', 'acme.com')
        finally:
            sj.EIGHTFOLD_MAX_PAGES = original

    def search(second_page, **kwargs):
        responses.reset()
        _ef_search('cyber', 0, json=_ef_page(
            [_ef_pos(i, f'Mechanical Engineer {i}') for i in range(10)], 20))
        _ef_search('cyber', 10, **(kwargs or {'json': _ef_page(second_page, 20)}))
        for term in ('intern', 'early career'):
            _ef_search(term, 0, json=_ef_page([], 0))

    search([_ef_pos(i, f'Mechanical Engineer {i}') for i in range(10, 20)])
    check('eightfold: a whole sweep is not partial', _partial_flags(run(2)), [False])
    check('eightfold: a term that hits the page cap flags every posting',
          _partial_flags(run(1)), [True])
    search(None, status=500)
    jobs = run(2)
    check('eightfold: a failed page mid-term keeps what it read, flagged',
          (len(jobs), _partial_flags(jobs)), (10, [True]))
    responses.reset()
    _ef_search('cyber', 0, status=500)
    for term in ('intern', 'early career'):
        _ef_search(term, 0, json=_ef_page([], 0))
    check('eightfold: a sweep that lost pages and found nothing -> None', run(2), None)


@responses.activate
def test_phenom_flags_a_cut_short_sweep():
    """BAE 'cyber' matches 1,837 postings against a 200 cap."""
    size = sj.PHENOM_PAGE_SIZE
    _ph_search('cyber', 0, [_ph_job(n) for n in range(size)], 5000)
    _ph_search('intern', 0, [], 0)
    responses.post(PH_API, status=500, match=[responses.matchers.json_params_matcher(
        {'ddoKey': 'refineSearch', 'keywords': 'cyber', 'from': size}, strict_match=False)])
    jobs = sj.scrape_phenom('Acme', 'careers.acme.org', 'en_us', 'us')
    check('phenom: a failed page mid-term keeps what it read, flagged',
          (len(jobs), _partial_flags(jobs)), (size, [True]))
    original = sj.PHENOM_MAX_PAGES
    try:
        sj.PHENOM_MAX_PAGES = 1
        jobs = sj.scrape_phenom('Acme', 'careers.acme.org', 'en_us', 'us')
    finally:
        sj.PHENOM_MAX_PAGES = original
    check('phenom: a term that hits the page cap flags every posting',
          _partial_flags(jobs), [True])
    responses.reset()
    _ph_search('cyber', 0, [_ph_job(1)], 1)
    _ph_search('intern', 0, [], 0)
    check('phenom: a whole sweep is not partial',
          _partial_flags(sj.scrape_phenom('Acme', 'careers.acme.org', 'en_us', 'us')),
          [False])


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
        jobs = sj.scrape_smartrecruiters('Kudelski Security', 'KudelskiSecurityInc', True)
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


@responses.activate
def test_oracle_and_smartrecruiters_use_the_security_flag():
    """Fortinet (Oracle) never fetched 'Software Engineer I'; LLNL spent its cap on interns."""
    host = 'fortinet.fa.us2.oraclecloud.com'
    base = f'https://{host}/hcmRestApi/resources/latest/'
    responses.get(base + 'recruitingCEJobRequisitions', json={'items': [{
        'TotalJobsCount': 1, 'requisitionList': [
            {'Id': '1', 'Title': 'Software Engineer I', 'PrimaryLocation': 'Sunnyvale, CA'}]}]})
    responses.get(base + 'recruitingCEJobRequisitionDetails', json={'items': [{
        'ExternalDescriptionStr': '<p>3+ years of experience.</p>'}]})
    responses.get(SR_POSTINGS, json={'totalFound': 1, 'content': [
        _sr_posting('1', 'Software Engineering Intern')]})
    responses.get(f'{SR_POSTINGS}/1', json={'jobAd': {'sections': {
        'qualifications': {'title': 'Qualifications', 'text': '<p>Enrolled in a BS.</p>'}}}})

    def details(fn, *args):
        responses.calls.reset()
        jobs = fn(*args)
        return len([c for c in responses.calls
                    if 'Details' in c.request.url or '/postings/' in c.request.url]), jobs

    check('oracle at a general employer skips a generic leveled title',
          details(sj.scrape_oracle, 'Acme', host, 'CX_1')[0], 0)
    count, jobs = details(sj.scrape_oracle, 'Fortinet', host, 'CX_1', True)
    check('oracle at a security company fetches it', count, 1)
    check('...so the experience gate sees the years',
          sj.evaluate_job(jobs[0]['title'], jobs[0]['location'], jobs[0]['description'],
                          True), None)
    check('smartrecruiters at a general employer skips a generic intern title',
          details(sj.scrape_smartrecruiters, 'LLNL', 'KudelskiSecurityInc')[0], 0)
    check('smartrecruiters at a security company fetches it',
          details(sj.scrape_smartrecruiters, 'Acme', 'KudelskiSecurityInc', True)[0], 1)

    config = {'smartrecruiters': [{'name': 'S', 'slug': 's', 'security_company': True}],
              'oracle': [{'name': 'O', 'host': 'o.fa.us2.oraclecloud.com', 'site': 'CX_1',
                          'security_company': True}],
              'greenhouse': [{'name': 'G', 'slug': 'g', 'security_company': True}]}
    check('build_tasks passes the flag to smartrecruiters and oracle only',
          [t.args for t in sj.build_tasks(config)][:3],
          [('G', 'g'), ('S', 's', True), ('O', 'o.fa.us2.oraclecloud.com', 'CX_1', True)])


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


@responses.activate
def test_smartrecruiters_and_amazon_flag_a_cut_short_sweep():
    """A failed page mid-sweep used to come back as a plain, unflagged list."""
    sr = 'https://api.smartrecruiters.com/v1/companies/Acme/postings'
    full = {'totalFound': 300, 'content': [
        {'id': str(i), 'name': 'Accountant',
         'location': {'country': 'us', 'city': 'Austin', 'region': 'TX'}} for i in range(100)]}
    responses.get(sr, json=full)
    responses.get(sr, status=404)
    jobs = sj.scrape_smartrecruiters('Acme', 'Acme')
    check('smartrecruiters: a failed second page keeps the first, flagged',
          (len(jobs), _partial_flags(jobs)), (100, [True]))

    amazon = 'https://www.amazon.jobs/en/search.json'
    page = {'hits': 300, 'jobs': [{'id_icims': str(i), 'title': 'Accountant',
                                   'location': 'US, WA, Seattle'} for i in range(100)]}
    responses.get(amazon, json=page)
    responses.get(amazon, status=404)
    jobs = sj.scrape_amazon()
    check('amazon: a failed second page keeps the first, flagged',
          (len(jobs), _partial_flags(jobs)), (100, [True]))

    responses.reset()
    responses.get(sr, json=full)
    responses.get(amazon, json=page)
    original = sj.MAX_PAGES
    try:
        sj.MAX_PAGES = 1
        capped = (sj.scrape_smartrecruiters('Acme', 'Acme'), sj.scrape_amazon())
    finally:
        sj.MAX_PAGES = original
    check('smartrecruiters and amazon: the page cap flags every posting',
          [_partial_flags(jobs) for jobs in capped], [[True], [True]])

    responses.reset()
    responses.get(sr, status=404)
    responses.get(amazon, status=404)
    check('a first page that fails is still None, not []',
          (sj.scrape_smartrecruiters('Acme', 'Acme'), sj.scrape_amazon()), (None, None))


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
    retired, renamed = sj.retire_orphaned_listings(listings, config, '2026-09-28')
    check('nothing is renamed without a live posting', renamed, [])
    check('rows whose company has no board for their source retire',
          [e['role'] for e in retired], ['Site Reliability Engineer II', 'Moved Off Workday'])
    check('an orphaned row is blanked the way the revive path expects',
          (listings[0]['url'], listings[0]['closed'], listings[0]['closed_date']),
          ('', True, '2026-09-28'))
    check('configured, Amazon, Community, unknown-source and closed rows are untouched',
          [bool(e['url']) for e in listings[1:3] + listings[4:7]], [True] * 5)
    check('an empty config retires nothing',
          sj.retire_orphaned_listings([_listing('Todyl', 'X', ashby, source='Ashby')], {},
                                      '2026-09-28'), ([], []))


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
    soft = check_links.SOFT_404
    check('a 200 with a not-found title or an empty title is a soft 404',
          [check_links.fetch_status(r.url, soft_404=True) for r in responses.registered()],
          [soft, soft, 200, soft, 200, 200])
    check('without soft_404 the raw status stands',
          check_links.fetch_status(bofa), 200)

    row = {'company': 'Bank of America', 'role': 'Analyst', 'source': 'Community', 'url': bofa}
    check('a soft 404 starts the streak like a real one',
          check_links.record_result(row, check_links.fetch_status(bofa, soft_404=True),
                                    '2026-09-27'), False)
    check('a second soft day is not enough, since an app can load with no title',
          check_links.record_result(row, soft, '2026-09-28'), False)
    check('the third distinct day closes it',
          (check_links.record_result(row, soft, '2026-09-29'), row['url']), (True, ''))


# --- check_links: a redirect that drops the req id -----------------------------
@responses.activate
def test_check_links_redirect_that_drops_the_req():
    gone = 'https://boards.greenhouse.io/acme/jobs/4412345'
    responses.get(gone, status=302, headers={'Location': 'https://boards.greenhouse.io/acme?error=true'})
    responses.get('https://boards.greenhouse.io/acme', status=200, body='<title>Jobs at Acme</title>',
                  content_type='text/html')
    search = 'https://careers.acme.com/job/SOC-Analyst/882211'
    responses.get(search, status=301, headers={'Location': 'https://careers.acme.com/search'})
    responses.get('https://careers.acme.com/search', status=200,
                  body='<title>Search jobs</title>', content_type='text/html')
    moved = 'https://careers.acme.com/job/882299'
    responses.get(moved, status=301,
                  headers={'Location': 'https://careers.acme.com/en/job/882299/soc-analyst'})
    responses.get('https://careers.acme.com/en/job/882299/soc-analyst', status=200,
                  body='<title>SOC Analyst</title>', content_type='text/html')
    check('a redirect to an error page or a page without the req id is a soft 404',
          [check_links.fetch_status(u, soft_404=True) for u in (gone, search, moved)],
          [check_links.SOFT_404, check_links.SOFT_404, 200])
    check('a scraped-row check does not read redirects',
          check_links.fetch_status(search), 200)


def test_check_links_ages_out_old_community_rows():
    old = {'company': 'Acme', 'role': 'SOC Intern', 'source': 'Community',
           'url': 'https://x/1', 'date_added': '2026-05-01'}
    young = dict(old, date_added='2026-08-01')
    scraped = dict(old, source='Greenhouse')
    check('only a Community row past the age limit ages out',
          [check_links.is_aged_out(e, '2026-09-30') for e in (old, young, scraped)],
          [True, False, False])


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
def test_main_does_not_re_announce_an_oracle_req_under_its_new_id():
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
        # Req 5 was judged under its pre-host id and its row is on the board.
        # A new id alone must not announce it a second time: the row's URL
        # already holds it, with no carry-over of the old seen stamp needed.
        # That carry-over let a new Honeywell req on site CX_1 inherit an Amex
        # stamp for the same number.
        (tmp / 'listings.json').write_text(json.dumps([{
            'company': 'SAIC', 'role': 'Cybersecurity Analyst Intern', 'location': 'Reston, VA',
            'type': 'intern', 'category': 'Security Engineering', 'clearance': False,
            'url': url, 'source': 'Oracle', 'date_added': '2026-09-27'}]))
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
    check('a req on the board under its old id is not announced again',
          ([r['role'] for r in events['added']], [r['url'] for r in rows]),
          (['Security Operations Center Intern'], [url, url.replace('/job/5', '/job/6')]))
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
    seen = {}
    added, _ = sj.insert_new_listings(
        moved, [dict(raw[0], id='wd-moved', title='Cyber Analyst I', location='Reston, VA')],
        seen, {}, '2026-09-10')
    check('...and is not inserted again under its new location', added, [])


RTX = 'https://globalhr.wd5.myworkdayjobs.com/REC_RTX_Ext_Gateway/job/'


def _wd_row(role, req, kind='earlycareer', location='Cambridge, MA', **extra):
    row = {'company': 'RTX', 'role': role, 'location': location, 'type': kind,
           'category': 'Security Engineering', 'clearance': False,
           'url': f'{RTX}US-MA-CAMBRIDGE/{role.replace(" ", "-")}_{req}', 'source': 'Workday'}
    row.update(extra)
    return row


def _wd_live(title, req, description='', location='Cambridge, MA', **extra):
    job = {'id': f'workday_globalhr_{req}', 'company': 'RTX', 'title': title,
           'location': location, 'url': f'{RTX}US-MA-CAMBRIDGE/{title.replace(" ", "-")}_{req}',
           'board': 'Workday', 'description': description}
    job.update(extra)
    return job


def test_reevaluate_keeps_a_row_behind_a_multi_location_placeholder():
    """A failed Workday detail fetch leaves the list view's '2 Locations'."""
    listings = [_wd_row('Cyber Analyst I', 'R1'), _wd_row('SOC Analyst I', 'R2')]
    raw = [_wd_live('Cyber Analyst I', 'R1', 'Entry level.', location='2 Locations'),
           _wd_live('SOC Analyst I', 'R2', 'Entry level.', location='Austin, TX, More...')]
    kept, dropped, _ = _reevaluate(listings, raw)
    check('a multi-location placeholder drops nothing', (dropped, len(kept)), ([], 2))


def test_reevaluate_does_not_judge_a_fingerprinted_row_by_a_sibling():
    """The capped RTX sweep missed R100 but reached sibling R200 (5+ years)."""
    listings = [_wd_row('Systems Security Engineer I', 'R100')]
    raw = [_wd_live('Systems Security Engineer I', 'R200',
                    'Requires 5+ years of experience.', partial_sweep=True)]
    kept, dropped, refreshed = _reevaluate(listings, raw)
    check('a fingerprinted row missing from the feed is not judged by its sibling',
          (dropped, refreshed, len(kept)), ([], [], 1))


def _probe_from(answers, asked):
    def probe(url):
        asked.append(url)
        return answers.get(url, (None, None))
    return probe


def test_reevaluate_rejudges_a_row_past_the_workday_cap():
    """RTX 'Security Specialist II' sat open with NISPOM duties in its description.

    The title rules now reject that title outright, so a title they still
    accept stands in for it here.
    """
    facility = _wd_row('Security Analyst II', '01877097')
    silent = _wd_row('Security Engineer', 'R3')
    intern = _wd_row('Security Engineer', 'R4', kind='intern')
    matched = _wd_row('Cyber Analyst I', 'R5')
    listings = [facility, silent, intern, matched]
    answers = {
        facility['url']: ('live', {
            'title': 'Security Analyst II', 'location': 'US-MA-CAMBRIDGE-BBN04',
            'additionalLocations': [],
            'jobDescription': '<p>Administer the NISPOM program and classified document '
                              'control for the site.</p>'}),
        # An empty body is not evidence against a flat title.
        silent['url']: ('live', {'title': 'Security Engineer', 'location': 'Cambridge, MA',
                                 'jobDescription': ''}),
        # The experience gate exempts interns here as it does at insert.
        intern['url']: ('live', {'title': 'Security Engineer', 'location': 'Cambridge, MA',
                                 'jobDescription': 'Requires 6+ years of experience.'}),
    }
    raw = [_wd_live('Cyber Analyst I', 'R5', 'Entry level.', partial_sweep=True)]
    asked = []
    kept, dropped, refreshed = _reevaluate(listings, raw)
    check('without a probe nothing past the cap is judged', dropped, [])
    kept, dropped, refreshed = sj.reevaluate_stored_listings(
        listings, raw, {}, probe=_probe_from(answers, asked))
    check('the probed posting drops a facility-security row',
          [(e['role'], reason) for e, reason in dropped],
          [('Security Analyst II', 'facility-security')])
    check('an empty probed body and an intern row are kept',
          [(e['role'], e['type']) for e in kept],
          [('Security Engineer', 'earlycareer'), ('Security Engineer', 'intern'),
           ('Cyber Analyst I', 'earlycareer')])
    check('only the unmatched rows are probed', sorted(asked),
          sorted([facility['url'], silent['url'], intern['url']]))

    asked.clear()
    complete = [dict(raw[0], partial_sweep=False)]
    sj.reevaluate_stored_listings(listings, complete, {}, probe=_probe_from(answers, asked))
    check('a complete sweep probes nothing', asked, [])
    sj.reevaluate_stored_listings(listings, complete, {}, probe=_probe_from(answers, asked),
                                  failed_companies={'RTX'})
    check('a company with a failed board is probed like a partial sweep', len(asked), 3)


@responses.activate
def test_main_probes_each_missed_workday_row_once():
    base = 'https://t.wd5.myworkdayjobs.com'
    facility = f'{base}/B/job/US-MA-CAMBRIDGE/Security-Analyst-II_01877097'
    analyst = f'{base}/B/job/US-MA-CAMBRIDGE/Cyber-Analyst-I_JR555'
    responses.add_callback(responses.POST, WD_API, callback=_workday_pages({
        'cyber': [_wd_page(100, 0, 20)]}))
    responses.get(facility.replace(f'{base}/B', f'{base}/wday/cxs/t/B'), json={
        'jobPostingInfo': {'canApply': True, 'title': 'Security Analyst II',
                           'location': 'Cambridge, MA',
                           'jobDescription': 'Maintain NISPOM compliance.'}})
    responses.get(analyst.replace(f'{base}/B', f'{base}/wday/cxs/t/B'), json={
        'jobPostingInfo': {'canApply': True, 'title': 'Cyber Analyst I',
                           'location': 'Cambridge, MA', 'jobDescription': 'Entry level.'}})
    rows = [dict(_wd_row('Security Analyst II', '01877097'), company='X', url=facility),
            dict(_wd_row('Cyber Analyst I', 'JR555'), company='X', url=analyst)]
    original = sj.WORKDAY_MAX_PAGES
    try:
        sj.WORKDAY_MAX_PAGES = 1
        out, after = _run_main(
            'workday:\n  - name: X\n    tenant: t\n    instance: wd5\n    board: B\n',
            rows, ['--dry-run', '--board', 'workday'])
    finally:
        sj.WORKDAY_MAX_PAGES = original
    detail = [c.request.url for c in responses.calls if '/wday/cxs/t/B/job/' in c.request.url]
    check('each missed row costs one detail request across both passes',
          sorted(detail), sorted([facility.replace(f'{base}/B', f'{base}/wday/cxs/t/B'),
                                  analyst.replace(f'{base}/B', f'{base}/wday/cxs/t/B')]))
    check('the re-judged row logs a DROP line compare_runs reads',
          compare_runs.parse_log(out).dropped,
          {'X — Security Analyst II': 'facility-security'})
    check('a dry run writes nothing', after, rows)


def _run_main(companies, listings, argv, seen=None):
    import contextlib
    import io
    saved = (sj.LISTINGS_FILE, sj.SEEN_JOBS_FILE, sj.BOARD_BASELINE_FILE,
             sj.rebuild_readme.main, sys.argv, os.getcwd())
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'companies.yml').write_text(companies)
        (tmp / 'listings.json').write_text(json.dumps(listings))
        (tmp / 'seen_jobs.json').write_text(json.dumps(seen or {}))
        out = io.StringIO()
        try:
            os.chdir(tmp)
            sj.LISTINGS_FILE = tmp / 'listings.json'
            sj.SEEN_JOBS_FILE = tmp / 'seen_jobs.json'
            sj.BOARD_BASELINE_FILE = tmp / 'board_baseline.json'
            sj.rebuild_readme.main = lambda: None
            sys.argv = ['scrape_jobs.py', *argv]
            with contextlib.redirect_stdout(out):
                sj.main()
            after = json.loads((tmp / 'listings.json').read_text())
        finally:
            (sj.LISTINGS_FILE, sj.SEEN_JOBS_FILE, sj.BOARD_BASELINE_FILE,
             sj.rebuild_readme.main, sys.argv, cwd) = saved
            os.chdir(cwd)
    return out.getvalue(), after


def test_insert_readds_a_dropped_row_while_its_posting_is_live():
    listings = [
        _stored('Acme', 'SOC Analyst I', 1),
        # Folded by the repair pass: its key is held by the row above.
        _stored('Acme', 'Cyber Analyst I', 3, location='Austin, TX'),
    ]
    raw = [
        _live('Acme', 'SOC Analyst I', 1, 'Monitor alerts.'),
        # Dropped or orphan-retired on an earlier run, still live and passing.
        _live('Acme', 'Cyber Analyst I', 2, 'Entry level.', location='Reston, VA'),
        # Dropped by the re-eval pass this run: the same gates refuse it here.
        _live('Acme', 'Security Engineer II', 4, 'Requires 6+ years of experience.'),
        # A second copy of the folded row's req under its repaired location.
        _live('Acme', 'Cyber Analyst I', 5, 'Entry level.'),
    ]
    seen = {job['id']: '2026-09-01' for job in raw}
    added, revived = sj.insert_new_listings(listings, raw, seen, {}, '2026-09-10')
    check('a seen posting whose row left the board comes back, nothing else does',
          [(r['role'], r['location']) for r in added], [('Cyber Analyst I', 'Reston, VA')])
    check('the re-added posting is stamped seen today', seen['gh-Acme-2'], '2026-09-10')
    added, _ = sj.insert_new_listings(listings, raw, seen, {}, '2026-09-10')
    check('a second pass adds nothing', added, [])


def test_revive_takes_the_new_source():
    """A company that moved ATS was RETIRED [orphaned] then REVIVED every run."""
    listings = [_stored('Acme', 'SOC Analyst I', 1, url='', closed=True,
                        closed_date='2026-09-09'),
                _stored('Acme', 'Cyber Analyst I', 2, url='', closed=True, source='Community')]
    ashby = 'https://jobs.ashbyhq.com/acme/b9dee2a0-9bb3-447e-9bce-2b1bed784e5b'
    raw = [dict(_live('Acme', 'SOC Analyst I', 1, 'Monitor alerts.'), url=ashby, board='Ashby'),
           _live('Acme', 'Cyber Analyst I', 2, 'Entry level.')]
    _, revived = sj.insert_new_listings(listings, raw, {}, {}, '2026-09-10')
    check('a revived row takes its new board as its source, a Community row keeps its own',
          [(r['role'], r['url'], r['source']) for r in revived],
          [('SOC Analyst I', ashby, 'Ashby'),
           ('Cyber Analyst I', GH_JOBS.format('acme', 2), 'Community')])
    check('the revived row is no orphan under the new config',
          sj.retire_orphaned_listings(listings, {'ashby': [{'name': 'Acme', 'slug': 'acme'}]},
                                      '2026-09-11', raw)[0], [])


def test_orphan_pass_renames_a_renamed_company():
    config = {'greenhouse': [{'name': 'Acme Corp', 'slug': 'acme'}]}
    listings = [_stored('Acme', 'SOC Analyst I', 1, missing_since='2026-09-09'),
                _stored('Acme', 'Cyber Analyst I', 2),
                # Same req number on another tenant's board is not this req.
                _stored('Acme', 'Security Analyst I', 7,
                        url='https://acme.wd1.myworkdayjobs.com/Ext/job/Austin-TX/X_R1',
                        source='Workday')]
    raw = [_live('Acme Corp', 'SOC Analyst I', 1, 'Monitor alerts.'),
           dict(_live('Other', 'Security Analyst I', 8),
                url='https://other.wd1.myworkdayjobs.com/Ext/job/Austin-TX/X_R1', board='Workday')]
    retired, renamed = sj.retire_orphaned_listings(
        listings, dict(config, workday=[{'name': 'Other'}]), '2026-09-10', raw)
    check('a live req takes the new company name instead of retiring',
          [(e['role'], old, e['company']) for e, old in renamed],
          [('SOC Analyst I', 'Acme', 'Acme Corp')])
    check('the renamed row keeps its url and drops its absence streak',
          (listings[0]['url'], 'missing_since' in listings[0]),
          (GH_JOBS.format('acme', 1), False))
    check('rows with no live req, or a req on another tenant, still retire',
          [e['role'] for e in retired], ['Cyber Analyst I', 'Security Analyst I'])
    added, _ = sj.insert_new_listings(listings, raw[:1], {'gh-Acme Corp-1': '2026-09-01'},
                                      {}, '2026-09-10')
    check('the insert pass does not add the renamed req again', added, [])


def test_failed_board_holds_its_companys_rows():
    """Idaho National Laboratory has two Oracle sites; one failed, one answered."""
    host = 'https://inl.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/'
    stats = [{'label': 'Idaho National Laboratory (oracle/inl.fa.us2.oraclecloud.com/CX_1001)',
              'status': 'ok', 'count': 1},
             {'label': 'Idaho National Laboratory (oracle/inl.fa.us2.oraclecloud.com/CX_1002)',
              'status': 'FAILED', 'count': 0},
             {'label': 'Acme (greenhouse/acme)', 'status': 'CRASHED', 'count': 0},
             {'label': 'Fine (lever/fine)', 'status': 'zero', 'count': 0}]
    failed = sj.failed_board_companies(stats)
    check('companies with a failed or crashed board', sorted(failed),
          ['Acme', 'Idaho National Laboratory'])
    inl = 'Idaho National Laboratory'
    listings = [_listing(inl, 'Cyber Intern', host + 'CX_1002/job/9', source='Oracle',
                         missing_since='2026-09-01'),
                _listing(inl, 'Fresh', host + 'CX_1002/job/10', source='Oracle')]
    raw = [{'company': inl, 'board': 'Oracle', 'url': host + 'CX_1001/job/1'}]
    check('a failed site retires nothing on absence',
          sj.retire_vanished_listings(listings, raw, '2026-09-10', failed_companies=failed), [])
    check('...and stamps no streak', [e.get('missing_since') for e in listings],
          ['2026-09-01', None])
    check('without the failed set the old behavior retired the row',
          [e['role'] for e in sj.retire_vanished_listings(listings, raw, '2026-09-10')],
          ['Cyber Intern'])


def test_failed_boards_do_not_grow_the_silent_streak():
    """A six-day IP ban retired every row of a board that still held them."""
    label = 'Acme (greenhouse/acme)'
    history = {label: {'count': 4, 'zero_runs': 0, 'last_nonzero': '2026-09-01'}}
    for status in ['FAILED'] * sj.SILENT_BOARD_RUNS + ['CRASHED']:
        history, _, _ = sj.board_health([{'label': label, 'status': status, 'count': 0}],
                                        history, '2026-09-10')
    check('failed runs still count toward the dead-board alert',
          history[label]['zero_runs'], sj.SILENT_BOARD_RUNS + 1)
    check('...but not toward the silent streak', history[label]['empty_runs'], 0)
    failed = [{'label': label, 'status': 'FAILED', 'count': 0}]
    check('a board that only failed is not silent', sj.long_silent_boards(failed, history),
          set())
    for _run in range(sj.SILENT_BOARD_RUNS - 1):
        history, _, _ = sj.board_health([{'label': label, 'status': 'zero', 'count': 0}],
                                        history, '2026-09-10')
    history, _, _ = sj.board_health(failed, history, '2026-09-10')
    check('a failure holds an empty streak without growing it',
          history[label]['empty_runs'], sj.SILENT_BOARD_RUNS - 1)
    history, _, _ = sj.board_health([{'label': label, 'status': 'zero', 'count': 0}],
                                    history, '2026-09-10')
    check('real empty runs make a board silent',
          sj.long_silent_boards([{'label': label, 'status': 'zero', 'count': 0}], history),
          {('Acme', 'greenhouse')})


def test_orphan_pass_covers_jibe_rows():
    row = _listing('Gone', 'Cyber Analyst', 'https://careers.gone.org/jobs/59772', source='Jibe')
    retired, _ = sj.retire_orphaned_listings([row], {'jibe': [{'name': 'Other'}]}, '2026-09-10')
    check('a Jibe row whose board left the config retires', [e['role'] for e in retired],
          ['Cyber Analyst'])


@responses.activate
def test_fetches_refuse_a_redirect_to_another_host():
    """A board API that answers from a host companies.yml does not name is not that board."""
    responses.get('https://api.test/moved', status=302,
                  headers={'Location': 'https://evil.test/jobs'})
    responses.get('https://evil.test/jobs', json={'jobs': [{'id': 1}]})
    responses.get('https://api.test/old', status=301,
                  headers={'Location': 'https://api.test/new'})
    responses.get('https://api.test/new', json={'ok': True})
    check('fetch_json refuses a cross-host redirect',
          sj.fetch_json('https://api.test/moved', label='t'), None)
    check('...and does not retry it',
          len([c for c in responses.calls if c.request.url == 'https://api.test/moved']), 1)
    check('fetch_json follows a redirect on the same host',
          sj.fetch_json('https://api.test/old', label='t'), {'ok': True})
    check('_get_json_patiently refuses a cross-host redirect',
          sj._get_json_patiently('https://api.test/moved', label='t'), None)
    check('_get_json_patiently follows a redirect on the same host',
          sj._get_json_patiently('https://api.test/old', label='t'), {'ok': True})
    page = 'https://t.wd5.myworkdayjobs.com/B/job/Reston-VA/Analyst_R1'
    responses.get('https://t.wd5.myworkdayjobs.com/wday/cxs/t/B/job/Reston-VA/Analyst_R1',
                  status=302, headers={'Location': 'https://evil.test/jobs'})
    check('workday_posting_state says nothing about a cross-host redirect',
          sj.workday_posting_state(page), None)


@responses.activate
def test_fetches_cap_the_response_size():
    body = json.dumps({'jobs': ['x' * 200]})
    responses.get('https://api.test/big', body=body, content_type='application/json')
    responses.get('https://api.test/small', json={'jobs': []})
    original = sj.MAX_RESPONSE_BYTES
    try:
        sj.MAX_RESPONSE_BYTES = 100
        check('fetch_json treats a body over the cap as a failed fetch',
              sj.fetch_json('https://api.test/big', label='t'), None)
        check('_get_json_patiently does too',
              sj._get_json_patiently('https://api.test/big', label='t'), None)
        check('a body under the cap still parses',
              sj.fetch_json('https://api.test/small', label='t'), {'jobs': []})
    finally:
        sj.MAX_RESPONSE_BYTES = original
    check('the real cap fits the largest board, Anduril at 41.6 MB',
          sj.MAX_RESPONSE_BYTES > 42 * 1024 * 1024, True)


for fn in (test_greenhouse_location_reads_only_location_fields,
           test_greenhouse, test_greenhouse_http_error_returns_none, test_lever,
           test_lever_reads_all_locations,
           test_ashby, test_ashby_schema_drift_warns,
           test_ashby_falls_back_to_the_postal_address,
           test_smartrecruiters_pagination_short_page_stops,
           test_check_slugs_flags_unknown_smartrecruiters_id, test_oracle,
           test_fetch_json_retries_transient, test_fetch_json_gives_up_on_404,
           test_fetch_json_retries_5xx,
           test_slug_validation_blocks_host_reparenting,
           test_workday_total_failure_returns_none,
           test_smartrecruiters_missing_total_keeps_paging,
           test_smartrecruiters_remote_is_us_only_for_us_postings,
           test_recruitee_reads_state_sites_and_description,
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
           test_eightfold_prefers_a_city_over_a_state_only_standardized_location,
           test_eightfold_flags_a_cut_short_sweep, test_phenom_flags_a_cut_short_sweep,
           test_jibe_paginates_maps_fields_and_keeps_the_crawl_delay,
           test_jibe_none_vs_empty, test_jibe_retries_429_and_flags_a_cut_short_sweep,
           test_jibe_caps_pages, test_jibe_plumbs_through_config_and_retirement,
           test_new_boards_validate_config_and_plumb_through,
           test_main_writes_run_events_with_inserted_row,
           test_workable_reads_locations_and_description,
           test_lever_appends_lists_to_description,
           test_smartrecruiters_fetches_descriptions_for_candidates,
           test_oracle_and_smartrecruiters_use_the_security_flag,
           test_workday_more_suffix_fetches_locations, test_amazon_restricts_to_us_reqs,
           test_smartrecruiters_and_amazon_flag_a_cut_short_sweep,
           test_retire_orphaned_listings, test_check_links_soft_404,
           test_check_links_redirect_that_drops_the_req,
           test_check_links_ages_out_old_community_rows,
           test_board_health_forgets_a_removed_board,
           test_compare_runs_reports_retirements,
           test_oracle_ids_and_labels_carry_host_and_site,
           test_oracle_ids_differ_across_hosts_on_one_site,
           test_board_health_carries_an_oracle_board_across_the_label_change,
           test_main_does_not_re_announce_an_oracle_req_under_its_new_id,
           test_workday_posting_survives_a_non_dict_body,
           test_workday_fingerprint_survives_a_location_move,
           test_reevaluate_keeps_a_row_behind_a_multi_location_placeholder,
           test_reevaluate_does_not_judge_a_fingerprinted_row_by_a_sibling,
           test_reevaluate_rejudges_a_row_past_the_workday_cap,
           test_main_probes_each_missed_workday_row_once,
           test_insert_readds_a_dropped_row_while_its_posting_is_live,
           test_revive_takes_the_new_source, test_orphan_pass_renames_a_renamed_company,
           test_failed_board_holds_its_companys_rows,
           test_failed_boards_do_not_grow_the_silent_streak,
           test_orphan_pass_covers_jibe_rows,
           test_fetches_refuse_a_redirect_to_another_host,
           test_fetches_cap_the_response_size):
    fn()

if failures:
    print(f'\n{failures} scraper test(s) failed')
    sys.exit(1)
print('All scraper parser tests passed')
