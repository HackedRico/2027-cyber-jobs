#!/usr/bin/env python3
"""Parser tests for the ATS scrapers, run offline against mocked HTTP.

    python .github/scripts/test_scrapers.py

Exercises the response-shape handling that only broke in production before —
location extraction, pagination stops, intern hints, schema drift, and the
retry/backoff fetch layer — without touching the network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
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


for fn in (test_greenhouse, test_greenhouse_http_error_returns_none, test_lever,
           test_ashby, test_ashby_schema_drift_warns,
           test_smartrecruiters_pagination_short_page_stops, test_oracle,
           test_fetch_json_retries_transient, test_fetch_json_gives_up_on_404,
           test_slug_validation_blocks_host_reparenting,
           test_workday_total_failure_returns_none,
           test_smartrecruiters_missing_total_keeps_paging,
           test_amazon_description_includes_qualifications,
           test_drop_over_experienced):
    fn()

if failures:
    print(f'\n{failures} scraper test(s) failed')
    sys.exit(1)
print('All scraper parser tests passed')
