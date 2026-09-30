#!/usr/bin/env python3
"""Tests for the writer-output check and the scraper health alarm, run offline.

    python .github/scripts/test_health.py
"""
import contextlib
import io
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_outputs as co  # noqa: E402
import health_check as hc  # noqa: E402
import rebuild_readme  # noqa: E402
import scrape_jobs as sj  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    if got != want:
        failures += 1
        print(f'FAIL {name}: got {got!r}, want {want!r}')


def row(**overrides):
    base = {'company': 'Acme', 'role': 'SOC Analyst I', 'location': 'Austin, TX',
            'type': 'earlycareer', 'category': 'SOC & Detection', 'clearance': False,
            'url': 'https://boards.greenhouse.io/acme/jobs/1', 'source': 'Greenhouse',
            'date_added': '2026-09-20'}
    base.update(overrides)
    return base


# --- check_outputs.check_row ---------------------------------------------------
ROWS = [
    ('a good row', row(), []),
    ('a closed row with no url', row(url='', closed=True, closed_date='2026-09-21'), []),
    ('an open row with no url', row(url=''), ['url is empty but the row is not closed']),
    ('a bad type', row(type='senior'), ['type `senior` is not one of']),
    ('a bad category', row(category='Sales'), ['category `Sales` is not an allowed']),
    ('a bad date', row(date_added='Sep 20'), ["`date_added` 'Sep 20' is not YYYY-MM-DD"]),
    ('a bad closed_date', row(closed=True, closed_date='2026/09/21'),
     ["`closed_date` '2026/09/21' is not YYYY-MM-DD"]),
    ('a non-http url', row(url='javascript:alert(1)'), ['is not a single-line http(s) link']),
    ('a url with a newline', row(url='https://x.example/a\n| fake |'),
     ['is not a single-line http(s) link']),
    ('a missing field', {k: v for k, v in row().items() if k != 'source'},
     ['missing field `source`']),
    ('a string clearance', row(clearance='yes'), ['`clearance` is str, want bool']),
    ('an empty role', row(role=' '), ['`role` is empty']),
    ('a string closed flag', row(closed='true', url=''), ['`closed` is not a boolean']),
    ('a non-object row', 'oops', ['row is not an object']),
]
for name, entry, wants in ROWS:
    got = co.check_row(entry)
    check(f'check_row {name}: count', len(got), len(wants))
    for want in wants:
        check(f'check_row {name}: {want}', any(want in p for p in got), True)


# --- check_outputs.check_listings ----------------------------------------------
dupes = co.check_listings([
    row(),
    row(role='Other', url='https://job-boards.greenhouse.io/acme/jobs/1?utm_source=x'),
    row(url='https://boards.greenhouse.io/acme/jobs/2'),
    # Closed rows may repeat an open row: a revived posting leaves one behind.
    row(url='', closed=True, closed_date='2026-09-21'),
])
check('check_listings finds a duplicate open url across hosts',
      any('same url as open row' in p for p in dupes), True)
check('check_listings finds a duplicate open dedup key',
      any('open duplicate of company, role and location' in p for p in dupes), True)
check('check_listings ignores closed repeats', len(dupes), 2)
check('check_listings rejects a non-array', co.check_listings({}), ['listings.json is not a JSON array'])


# --- check_outputs.main with a baseline ----------------------------------------
def run_check_outputs(listings, baseline=None, readme=None):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'listings.json').write_text(json.dumps(listings))
        argv = ['check_outputs.py']
        if baseline is not None:
            (tmp / 'base.json').write_text(json.dumps(baseline))
            argv += ['--baseline', str(tmp / 'base.json')]
        (tmp / 'README.md').write_text(readme or '')
        saved = (co.LISTINGS_FILE, co.README_FILE, sys.argv)
        co.LISTINGS_FILE, co.README_FILE, sys.argv = (tmp / 'listings.json',
                                                      tmp / 'README.md', argv)
        # readme_drift uses its own defaults, bound at import.
        drift = co.readme_drift
        co.readme_drift = lambda: drift(tmp / 'listings.json', tmp / 'README.md')
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                co.main()
            code = 0
        except SystemExit as e:
            code = e.code
        finally:
            co.LISTINGS_FILE, co.README_FILE, sys.argv = saved
            co.readme_drift = drift
    return code, out.getvalue()


BAD = row(url='')
code, out = run_check_outputs([row(), BAD])
check('check_outputs fails on a new problem', code, 1)
check('check_outputs prints an error annotation', '::error::listings.json: Acme' in out, True)
code, out = run_check_outputs([row(), BAD], baseline=[BAD])
check('check_outputs only warns on a problem main already has', code, 0)
check('check_outputs prints the known problem as a warning',
      '::warning::listings.json: Acme | SOC Analyst I | Austin, TX: url is empty' in out, True)
code, out = run_check_outputs([row(), BAD, row(type='x')], baseline=[BAD])
check('check_outputs still fails on a new problem beside a known one', code, 1)


# --- check_outputs.check_against_baseline --------------------------------------
def board(n):
    return [row(role=f'SOC Analyst I {i}', url=f'https://boards.greenhouse.io/acme/jobs/{i}')
            for i in range(n)]


BASE = board(100)
check('a clean run passes', co.check_against_baseline(BASE + [row()], BASE), [])
senior = row(role='Senior Security Engineer', url='https://boards.greenhouse.io/acme/jobs/900')
london = row(location='London, UK', url='https://boards.greenhouse.io/acme/jobs/901')
check('a new scraped row the charter rejects is a problem',
      len(co.check_against_baseline(BASE + [senior, london], BASE)), 2)
check('a new Community row is not re-judged',
      co.check_against_baseline(BASE + [dict(london, source='Community')], BASE), [])
check('a row already open on main is not re-judged',
      co.check_against_baseline(BASE + [senior], BASE + [senior]), [])
closed = [dict(e, url='', closed=True) if i < 30 else e for i, e in enumerate(BASE)]
check('closing 30 of 100 open rows in one run is a problem',
      len(co.check_against_baseline(closed, BASE)), 1)
check('unless the run allows a mass close',
      co.check_against_baseline(closed, BASE, allow_mass_close=True), [])
check('dropping 20 of 100 is ordinary churn', co.check_against_baseline(BASE[:80], BASE), [])
check('a small board needs MASS_CLOSE_MIN closures before it trips',
      co.check_against_baseline(board(40)[:20], board(40)), [])
code, out = run_check_outputs(closed, baseline=BASE)
check('check_outputs fails a mass close', (code, 'open rows closed or dropped' in out), (1, True))

README = """# Board
<!-- STATS -->
**1** open roles tracked · updated January 1, 2026
<!-- /STATS -->
<!-- LEGEND -->
<!-- /LEGEND -->
<!-- TABLE_START intern -->
| Company | Role | Apply | Location | Added |
| ------- | ---- | ----- | -------- | ----- |
<!-- TABLE_END intern -->
<!-- CLOSED_START intern -->
<!-- CLOSED_END intern -->
<!-- TABLE_START newgrad -->
| Company | Role | Apply | Location | Added |
| ------- | ---- | ----- | -------- | ----- |
<!-- TABLE_END newgrad -->
<!-- CLOSED_START newgrad -->
<!-- CLOSED_END newgrad -->
<!-- TABLE_START earlycareer -->
| Company | Role | Apply | Location | Added |
| ------- | ---- | ----- | -------- | ----- |
<!-- TABLE_END earlycareer -->
<!-- CLOSED_START earlycareer -->
<!-- CLOSED_END earlycareer -->
"""
code, out = run_check_outputs([row()], readme=README)
check('check_outputs warns when the README is behind listings.json',
      '::warning::README.md differs from a rebuild' in out, True)
check('check_outputs does not fail on README drift', code, 0)
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'listings.json').write_text(json.dumps([row()]))
    (tmp / 'README.md').write_text(README)
    saved = (rebuild_readme.LISTINGS_FILE, rebuild_readme.README_FILE, rebuild_readme.COMPANIES_MD)
    rebuild_readme.LISTINGS_FILE = tmp / 'listings.json'
    rebuild_readme.README_FILE = tmp / 'README.md'
    rebuild_readme.COMPANIES_MD = tmp / 'companies.md'
    with contextlib.redirect_stdout(io.StringIO()):
        rebuild_readme.main()
    (rebuild_readme.LISTINGS_FILE, rebuild_readme.README_FILE,
     rebuild_readme.COMPANIES_MD) = saved
    check('readme_drift is quiet on a freshly rebuilt README, whatever the stats date',
          co.readme_drift(tmp / 'listings.json', tmp / 'README.md'), [])
    check('readme_drift left the README it checked alone',
          '| Acme |' in (tmp / 'README.md').read_text(), True)


# --- report_board_health writes health.json ------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    saved = (sj.BOARD_BASELINE_FILE, sj.HEALTH_FILE)
    sj.BOARD_BASELINE_FILE, sj.HEALTH_FILE = tmp / 'baseline.json', tmp / 'health.json'
    sj.BOARD_BASELINE_FILE.write_text(json.dumps({
        'A (greenhouse/a)': {'count': 5, 'zero_runs': 0, 'last_nonzero': '2026-09-26'}}))
    stats = [{'label': 'A (greenhouse/a)', 'status': 'zero', 'count': 0},
             {'label': 'B (lever/b)', 'status': 'FAILED', 'count': 0},
             {'label': 'C (ashby/c)', 'status': 'ok', 'count': 3}]
    with contextlib.redirect_stdout(io.StringIO()):
        sj.report_board_health(stats, '2026-09-27', persist=False)
    check('report_board_health writes no health.json on a partial run',
          sj.HEALTH_FILE.exists(), False)
    with contextlib.redirect_stdout(io.StringIO()):
        sj.report_board_health(stats, '2026-09-27', persist=True)
    check('report_board_health writes health.json',
          json.loads(sj.HEALTH_FILE.read_text()),
          {'date': '2026-09-27', 'boards': 3, 'ok': 1, 'empty': 1,
           'failed': ['B (lever/b)'], 'regressed': [{'board': 'A (greenhouse/a)', 'had': 5}],
           'silent': 0})
    sj.BOARD_BASELINE_FILE, sj.HEALTH_FILE = saved


# --- health_check.find_problems ------------------------------------------------
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def run(conclusion, hours_ago=1, run_id=7):
    return {'id': run_id, 'conclusion': conclusion, 'html_url': f'https://x/runs/{run_id}',
            'created_at': (NOW - timedelta(hours=hours_ago)).isoformat()}


HEALTHY_RUNS = {w: run('success') for w in hc.WRITERS}
FRESH = NOW - timedelta(hours=3)
LISTINGS = [{'date_added': '2026-09-25'}]


def problems(**kw):
    args = {'now': NOW, 'writer_runs': HEALTHY_RUNS, 'last_success': FRESH, 'health': {},
            'baseline': {}, 'listings': LISTINGS, 'previous': frozenset()}
    args.update(kw)
    return hc.find_problems(**args)


check('find_problems is empty when all is well', problems(), {})
check('find_problems flags a stale scrape',
      list(problems(last_success=NOW - timedelta(hours=31))), ['scrape-stale'])
check('find_problems flags a scrape that never succeeded',
      'never' in problems(last_success=None)['scrape-stale'], True)
for conclusion in ('failure', 'cancelled', 'timed_out'):
    got = problems(writer_runs=dict(HEALTHY_RUNS, **{'scrape-jobs.yml': run(conclusion)}))
    check(f'find_problems flags a {conclusion} writer run', list(got), ['writer:scrape-jobs.yml'])
check('find_problems ignores a bad writer run older than the window',
      problems(writer_runs=dict(HEALTHY_RUNS, **{'add-listing.yml': run('cancelled', 100)})), {})
check('find_problems handles a workflow with no runs',
      problems(writer_runs=dict(HEALTHY_RUNS, **{'add-listing.yml': None})), {})

BASE = {'B': {'count': 0, 'zero_runs': 5}, 'F': {'count': 0, 'zero_runs': 1},
        'R': {'count': 0, 'zero_runs': 1}, 'Back': {'count': 4, 'zero_runs': 0}}
got = problems(health={'failed': ['B', 'F'], 'regressed': [{'board': 'R', 'had': 3}]},
               baseline=BASE)
check('find_problems flags a board that keeps failing and a regressed board, not a one-off failure',
      sorted(got), ['board-failed:B', 'board-regressed:R'])
got = problems(health={'failed': [], 'regressed': []},
               baseline=dict(BASE, R={'count': 0, 'zero_runs': 2}),
               previous={'board-regressed:R', 'board-regressed:Back'})
check('find_problems keeps a regressed board flagged until it returns postings',
      sorted(got), ['board-regressed:R'])
check('find_problems flags a quiet week in recruiting season',
      list(problems(listings=[{'date_added': '2026-09-19'}])), ['no-new-rows'])
check('find_problems accepts a quiet week outside recruiting season',
      problems(now=datetime(2026, 12, 27, tzinfo=UTC), last_success=datetime(2026, 12, 27, tzinfo=UTC),
               writer_runs={}, listings=[{'date_added': '2026-12-01'}]), {})
check('find_problems survives junk dates',
      list(problems(listings=[{'date_added': 'soon'}, {}])), ['no-new-rows'])


# --- health_check.plan ---------------------------------------------------------
P = {'scrape-stale': 'No successful scrape', 'writer:link-check.yml': 'Check Links failed'}
M = '@maintainer'
calls = hc.plan(P, None, NOW, M)
check('plan opens the issue when there is none', [c[:2] for c in calls], [('POST', '/issues')])
check('plan mentions the maintainer in a new issue', M in calls[0][2]['body'], True)
check('plan stores the problem keys on the issue', hc.previous_state(calls[0][2]), set(P))

opened = {'number': 3, 'state': 'open', 'body': hc.render_body(P, NOW)}
check('plan stays quiet while the problem set is unchanged', hc.plan(P, opened, NOW, M), [])
calls = hc.plan({'scrape-stale': 'x'}, opened, NOW, M)
check('plan edits and comments when the set changes',
      [c[:2] for c in calls], [('PATCH', '/issues/3'), ('POST', '/issues/3/comments')])
check('plan names what resolved', '`writer:link-check.yml`' in calls[1][2]['body'], True)
check('plan mentions the maintainer on a change', calls[1][2]['body'].startswith(M), True)
calls = hc.plan({}, opened, NOW, M)
check('plan comments and closes when all clear',
      [(c[0], c[1], c[2].get('state')) for c in calls],
      [('POST', '/issues/3/comments', None), ('PATCH', '/issues/3', 'closed')])
closed = dict(opened, state='closed')
check('plan does nothing when clear and closed', hc.plan({}, closed, NOW, M), [])
calls = hc.plan(P, closed, NOW, M)
check('plan reopens a closed issue when problems return',
      (calls[0][2]['state'], 'New' in calls[1][2]['body']), ('open', True))
check('previous_state tolerates a hand-edited body',
      hc.previous_state({'body': '<!-- health-state: [broken -->'}), set())


# --- run lookups read the unfiltered list (#44) --------------------------------
class FakeRuns:
    def __init__(self, runs):
        self.runs, self.paths = runs, []

    def repo_path(self, path):
        return path

    def call(self, method, path, body=None, ok=()):
        self.paths.append(path)
        return {'workflow_runs': self.runs}


gh = FakeRuns([
    {'status': 'in_progress', 'conclusion': None, 'updated_at': '2026-09-30T14:00:00Z'},
    {'status': 'completed', 'conclusion': 'skipped', 'updated_at': '2026-09-30T13:50:00Z'},
    {'status': 'completed', 'conclusion': 'success', 'updated_at': '2026-09-30T13:46:59Z'},
    {'status': 'completed', 'conclusion': 'failure', 'updated_at': '2026-09-29T02:00:00Z'},
])
check('the last success skips runs still going', hc.last_successful_scrape(gh),
      datetime(2026, 9, 30, 13, 46, 59, tzinfo=UTC))
check('no request uses the lagging status filter', any('status=' in p for p in gh.paths), False)
check('the latest writer run skips in-progress and skipped runs',
      {r['conclusion'] for r in hc.latest_writer_runs(gh).values()}, {'success'})


if failures:
    print(f'\n{failures} health test(s) failed')
    sys.exit(1)
print('All health tests passed')
