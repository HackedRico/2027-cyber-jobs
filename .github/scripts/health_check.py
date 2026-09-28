#!/usr/bin/env python3
"""Keep one "Scraper health" issue in step with the board's automation.

    python .github/scripts/health_check.py [--dry-run]

Flags a scrape that has not succeeded in 30 hours, a writer workflow whose
latest run in the last 3 days failed, was cancelled or timed out, boards that fail or fell to
zero postings, and a board with no new row for 7 days while employers post
student roles. The issue body stores the current problem keys; the maintainer
is mentioned only when that set changes, and the issue closes when it empties.
From Aug 2 to Sep 4 every scheduled scrape was cancelled and nothing said so.
Stdlib only.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

API = 'https://api.github.com'
WRITERS = {
    'scrape-jobs.yml': 'Scrape Job Boards',
    'link-check.yml': 'Check Links',
    'add-listing.yml': 'Add Approved Listings',
}
BAD_CONCLUSIONS = {'failure', 'cancelled', 'timed_out', 'startup_failure'}
SCRAPE_STALE_HOURS = 30
WRITER_WINDOW_HOURS = 72
QUIET_DAYS = 7
# Aug to Nov is when 2027 internships and new-grad roles open; a quiet week
# outside it is normal.
RECRUITING_MONTHS = range(8, 12)
# A board that failed once is usually a flaky host; two empty runs in a row
# is a broken slug.
FAILED_MIN_ZERO_RUNS = 2

ISSUE_TITLE = 'Scraper health'
ISSUE_LABEL = 'scraper-health'
LABEL_COLOR = 'b60205'
STATE_RE = re.compile(r'<!-- health-state: (\[.*?\]) -->')

HEALTH_FILE = Path('.github/data/health.json')
BASELINE_FILE = Path('.github/data/board_baseline.json')
LISTINGS_FILE = Path('listings.json')


class GitHub:
    """Minimal REST client; writes are refused in dry-run mode."""

    def __init__(self, token, repo, dry_run=False):
        self.token, self.repo, self.dry_run = token, repo, dry_run

    def call(self, method, path, body=None, ok=()):
        if method != 'GET' and self.dry_run:
            print(f'[dry-run] {method} {path} {json.dumps(body)[:300] if body else ""}')
            return None
        req = urllib.request.Request(
            f'{API}{path}', method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': f'Bearer {self.token}',
                     'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            # `ok` lists error codes that mean the write is already done.
            if e.code in ok:
                return None
            raise

    def repo_path(self, path):
        return f'/repos/{self.repo}{path}'


def _parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


def latest_writer_runs(gh):
    """{workflow file: latest completed run that did not skip, or None}."""
    runs = {}
    for workflow in WRITERS:
        data = gh.call('GET', gh.repo_path(
            f'/actions/workflows/{workflow}/runs?status=completed&per_page=30'))
        # add-listing fires on every label event and skips unless it is
        # 'approved', so the latest run is usually a skip that says nothing.
        runs[workflow] = next((r for r in (data or {}).get('workflow_runs', [])
                               if r.get('conclusion') != 'skipped'), None)
    return runs


def last_successful_scrape(gh):
    data = gh.call('GET', gh.repo_path(
        '/actions/workflows/scrape-jobs.yml/runs?status=success&per_page=1'))
    runs = (data or {}).get('workflow_runs', [])
    return _parse_time(runs[0].get('updated_at')) if runs else None


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def find_problems(now, writer_runs, last_success, health, baseline, listings,
                  previous=frozenset()):
    """Return {problem key: markdown line} for the current state.

    `previous` is the key set stored on the issue. A board flagged as
    regressed stays flagged while it still returns zero postings, because
    health.json names a regression only on the run it happens.
    """
    problems = {}
    if last_success is None or now - last_success > timedelta(hours=SCRAPE_STALE_HOURS):
        when = last_success.strftime('%b %-d %H:%M UTC') if last_success else 'never'
        problems['scrape-stale'] = (f'No successful scrape in {SCRAPE_STALE_HOURS} hours '
                                    f'(last success: {when})')

    for workflow, run in writer_runs.items():
        started = _parse_time((run or {}).get('created_at'))
        # add-listing runs only when a maintainer approves, so its latest run
        # can be weeks old; a bad run that old has been seen or superseded.
        if started and now - started > timedelta(hours=WRITER_WINDOW_HOURS):
            continue
        if run and run.get('conclusion') in BAD_CONCLUSIONS:
            note = ''
            if run['conclusion'] == 'cancelled':
                note = ', which is also how a job timeout shows'
            problems[f'writer:{workflow}'] = (
                f"{WRITERS[workflow]}: the latest run was {run['conclusion']}{note} "
                f"([run {run.get('id')}]({run.get('html_url')}))")

    baseline = baseline if isinstance(baseline, dict) else {}

    def zero_runs(label):
        entry = baseline.get(label)
        return entry.get('zero_runs', 0) if isinstance(entry, dict) else 0

    health = health if isinstance(health, dict) else {}
    for label in health.get('failed', []):
        if zero_runs(label) >= FAILED_MIN_ZERO_RUNS:
            problems[f'board-failed:{label}'] = (
                f'Board `{label}` failed and has returned nothing for '
                f'{zero_runs(label)} runs')
    regressed = {r.get('board') for r in health.get('regressed', []) if isinstance(r, dict)}
    regressed |= {k.split(':', 1)[1] for k in previous if k.startswith('board-regressed:')
                  and zero_runs(k.split(':', 1)[1]) > 0}
    for label in sorted(regressed - {None}):
        if f'board-failed:{label}' not in problems:
            problems[f'board-regressed:{label}'] = (
                f'Board `{label}` fell to 0 postings after returning some '
                f'({zero_runs(label)} empty runs so far)')

    if now.month in RECRUITING_MONTHS:
        dates = []
        for entry in listings if isinstance(listings, list) else []:
            try:
                dates.append(date.fromisoformat(entry.get('date_added', '')))
            except (AttributeError, TypeError, ValueError):
                continue
        newest = max(dates, default=None)
        if newest is None or (now.date() - newest).days > QUIET_DAYS:
            problems['no-new-rows'] = (
                f'No new row in {QUIET_DAYS} days during recruiting season '
                f'(newest date_added: {newest or "none"})')
    return problems


def render_body(problems, now):
    lines = [f'<!-- health-state: {json.dumps(sorted(problems))} -->',
             'The automation that keeps the board current has problems. This issue '
             'updates itself: it gets a comment when the set of problems changes and '
             'closes when they clear. The board repair procedures are in '
             '`.claude/skills/triage-board`.',
             '',
             f'Checked {now.strftime("%Y-%m-%d %H:%M UTC")}:',
             '']
    lines += [f'- {problems[k]}' for k in sorted(problems)]
    return '\n'.join(lines) + '\n'


def render_change(problems, previous, mention):
    new = sorted(set(problems) - previous)
    kept = sorted(set(problems) & previous)
    resolved = sorted(previous - set(problems))
    lines = [f'{mention} the scraper health changed.', '']
    if new:
        lines += ['**New**'] + [f'- {problems[k]}' for k in new] + ['']
    if resolved:
        lines += ['**Resolved**'] + [f'- `{k}`' for k in resolved] + ['']
    if kept:
        lines += ['**Still open**'] + [f'- {problems[k]}' for k in kept] + ['']
    return '\n'.join(lines)


def previous_state(issue):
    """Problem keys stored on the issue body, or an empty set."""
    if not issue:
        return set()
    m = STATE_RE.search(issue.get('body') or '')
    try:
        return set(json.loads(m.group(1))) if m else set()
    except ValueError:
        return set()


def plan(problems, issue, now, mention):
    """Return the (method, path suffix, body) calls that bring the issue in step."""
    previous = previous_state(issue)
    is_open = bool(issue) and issue.get('state') == 'open'
    if problems:
        body = render_body(problems, now)
        if not issue:
            return [('POST', '/issues', {'title': ISSUE_TITLE, 'labels': [ISSUE_LABEL],
                                         'body': body + f'\n{mention}\n'})]
        if set(problems) == previous and is_open:
            return []
        n = issue['number']
        return [('PATCH', f'/issues/{n}', {'body': body, 'state': 'open'}),
                ('POST', f'/issues/{n}/comments',
                 {'body': render_change(problems, previous if is_open else set(), mention)})]
    if is_open:
        n = issue['number']
        return [('POST', f'/issues/{n}/comments',
                 {'body': f'{mention} all clear as of {now.strftime("%Y-%m-%d %H:%M UTC")}, '
                          f'closing.'}),
                ('PATCH', f'/issues/{n}', {'body': render_body({}, now), 'state': 'closed'})]
    return []


def find_issue(gh):
    data = gh.call('GET', gh.repo_path(
        f'/issues?state=all&labels={ISSUE_LABEL}&per_page=100&sort=created&direction=desc'))
    return next((i for i in data or [] if i.get('title') == ISSUE_TITLE
                 and 'pull_request' not in i), None)


def _token(dry_run):
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if not token and dry_run:
        # Local dry runs borrow the gh CLI login.
        token = subprocess.run(['gh', 'auth', 'token'], capture_output=True,
                               text=True).stdout.strip()
    return token


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dry-run', action='store_true',
                        help='read GitHub state and print the plan without writing')
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY'))
    args = parser.parse_args()

    token = _token(args.dry_run)
    if not token or not args.repo:
        print('GITHUB_TOKEN and GITHUB_REPOSITORY (or --repo) are required')
        sys.exit(1)
    gh = GitHub(token, args.repo, args.dry_run)
    mention = os.environ.get('HEALTH_MENTION', '@HackedRico')
    now = datetime.now(UTC)

    issue = find_issue(gh)
    problems = find_problems(
        now, latest_writer_runs(gh), last_successful_scrape(gh),
        _read_json(HEALTH_FILE, {}), _read_json(BASELINE_FILE, {}),
        _read_json(LISTINGS_FILE, []), previous_state(issue))

    print(f'{len(problems)} problem(s):')
    for key in sorted(problems):
        print(f'  {key}: {problems[key]}')
    print(f'Issue: #{issue["number"]} ({issue["state"]})' if issue else 'Issue: none yet')

    calls = plan(problems, issue, now, mention)
    if not calls:
        print('No change to report')
        return
    if not issue:
        # 422 means the label exists already.
        gh.call('POST', gh.repo_path('/labels'),
                {'name': ISSUE_LABEL, 'color': LABEL_COLOR,
                 'description': 'Automation problems found by health_check.py'}, ok=(422,))
    for method, path, body in calls:
        gh.call(method, gh.repo_path(path), body)


if __name__ == '__main__':
    main()
