#!/usr/bin/env python3
"""Announce a run's new rows as a GitHub Release and alert-issue comments.

    python .github/scripts/notify.py [--dry-run] [--events FILE]

Reads the events file that scrape_jobs.main() or process_approved.py writes to
$RUN_EVENTS_FILE. Students subscribe through GitHub itself (Watch > Custom >
Releases, or Subscribe on one alert issue), so this stores nothing about them.
"""

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import requests

sys.path.insert(0, str(Path(__file__).parent))
from classify import is_cyber_title  # noqa: E402
from common import gh_headers, md_escape  # noqa: E402

LISTINGS_FILE = Path('listings.json')
API = 'https://api.github.com'
DEFAULT_REPO = 'HackedRico/2027-cyber-jobs'
ALERTS_LABEL = 'alerts'
# Only issues a maintainer or this workflow opened can carry a stream, so a
# stranger's issue that copies the marker cannot hijack the alert thread.
TRUSTED_AUTHORS = {'OWNER', 'MEMBER', 'COLLABORATOR'}
BOT_LOGIN = 'github-actions[bot]'

OPENER_WINDOW_DAYS = 60
BURST_ROWS = 20
BURST_LISTED = 10
COMMENT_ROWS = 20
MAX_SITES = 3

TYPE_ORDER = ('intern', 'newgrad', 'earlycareer')
TYPE_HEADINGS = {
    'intern': '🎒 Internships',
    'newgrad': '🎓 New grad',
    'earlycareer': '🌱 Early career',
}
TYPE_WORDS = {'intern': 'intern', 'newgrad': 'new grad', 'earlycareer': 'early career'}
STUDENT_TYPES = ('intern', 'newgrad')
SECURITY_CO_HEADING = '🛡️ Also hiring at security companies'


class Stream(NamedTuple):
    """One alert issue: which rows it carries and how its thread reads."""
    key: str
    title: str
    nouns: tuple[str, str]
    matches: Callable[[dict], bool]
    blurb: str


STREAMS = (
    Stream('intern', '🎒 Internship alerts', ('new internship', 'new internships'),
           lambda r: r.get('type') == 'intern',
           'every new internship or co-op'),
    Stream('newgrad', '🎓 New-grad alerts', ('new role for new grads', 'new roles for new grads'),
           lambda r: r.get('type') == 'newgrad',
           'every new role for new grads'),
    Stream('earlycareer', '🌱 Early-career alerts',
           ('new early-career role', 'new early-career roles'),
           lambda r: r.get('type') == 'earlycareer',
           'every new early-career role (0 to 2 years)'),
    Stream('student-no-flag', '🎯 Intern and new-grad alerts without a 🇺🇸 flag',
           ('new student role with no 🇺🇸 flag', 'new student roles with no 🇺🇸 flag'),
           lambda r: r.get('type') in STUDENT_TYPES and not r.get('clearance'),
           'every new internship and new-grad role that does not ask for '
           'US citizenship or a clearance'),
    Stream('remote', '🏠 Remote (US) alerts', ('new remote role', 'new remote roles'),
           lambda r: 'remote' in (r.get('location') or '').lower(),
           'every new role that lists Remote (US)'),
)


def _marker(key):
    return f'<!-- alert-stream: {key} -->'


def _row_key(row):
    return (row.get('company', ''), row.get('role', ''), row.get('location', ''),
            row.get('type', ''))


def _day(stamp):
    try:
        return date.fromisoformat((stamp or '')[:10])
    except ValueError:
        return None


def _run_time(events):
    try:
        return datetime.strptime(events['run_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
    except (KeyError, TypeError, ValueError):
        return datetime.now(UTC)


def find_openers(added, listings, run_at):
    """Return the `_row_key`s of added rows that mark a company opening hiring.

    An opener is an intern or new-grad row with a cyber title at a company that
    had no row of that type on the board in the prior 60 days. A row counts as
    on the board while it is open, or until 60 days after it closed.
    """
    this_run = {_row_key(r) for r in added}
    cutoff = run_at.date() - timedelta(days=OPENER_WINDOW_DAYS)
    recent = set()
    for row in listings:
        if _row_key(row) in this_run:
            continue
        last_seen = _day(row.get('closed_date') or row.get('date_added'))
        if row.get('closed') and (last_seen is None or last_seen < cutoff):
            continue
        recent.add((row.get('company', '').casefold(), row.get('type')))
    return {_row_key(r) for r in added
            if r.get('type') in STUDENT_TYPES
            and is_cyber_title(r.get('role', ''))
            and (r.get('company', '').casefold(), r.get('type')) not in recent}


def _apply_link(url):
    if not url or not re.match(r'^https?://', url) or re.search(r'\s', url):
        return ''
    safe = (url.replace('(', '%28').replace(')', '%29')
               .replace('<', '%3C').replace('>', '%3E'))
    return f' · [Apply]({safe})'


def format_row(row, openers, with_type=False):
    """Render one row as a markdown bullet shared by the release and comments."""
    flags = ''
    if _row_key(row) in openers:
        flags += ' 🚨'
    if row.get('clearance'):
        flags += ' 🇺🇸'
    parts = [md_escape(row.get('role', ''))]
    sites = [p.strip() for p in (row.get('location') or '').split(';') if p.strip()]
    if len(sites) > MAX_SITES:
        sites = sites[:MAX_SITES - 1] + [f'{len(sites) - MAX_SITES + 1} more']
    if sites:
        parts.append(md_escape('; '.join(sites)))
    if with_type:
        parts.append(TYPE_WORDS.get(row.get('type'), row.get('type', '')))
    if row.get('category'):
        parts.append(md_escape(row['category']))
    return (f'- **{md_escape(row.get("company", ""))}**{flags}: ' + ' · '.join(parts)
            + _apply_link(row.get('url', '')))


def _openers_first(rows, openers):
    return sorted(rows, key=lambda r: _row_key(r) not in openers)


def _plural(n, word):
    return f'{n} {word}' if n == 1 else f'{n} {word}s'


def _type_counts(rows):
    counts = [(t, sum(1 for r in rows if r.get('type') == t)) for t in TYPE_ORDER]
    return ', '.join(f'{n} {TYPE_WORDS[t]}' for t, n in counts if n)


def release_title(added, openers):
    """Lead with the companies that opened hiring, else summarise the counts."""
    total = _plural(len(added), 'new role')
    opened = [r for r in added if _row_key(r) in openers]
    if not opened:
        return f'{total}: {_type_counts(added)}'
    companies = list(dict.fromkeys(r['company'] for r in opened))
    names = ', '.join(companies[:2])
    if len(companies) > 2:
        names += f' + {len(companies) - 2} more'
    kinds = ' + '.join(TYPE_WORDS[t] for t in STUDENT_TYPES
                       if any(r.get('type') == t for r in opened))
    return f'🚨 {names} opened {kinds} hiring · {total}'


def release_body(added, openers, board_url):
    """Group rows by type, interns first, with security-company rows last."""
    footer = [
        '',
        f'[Full board]({board_url})',
        '',
        f'<sub>🚨 first intern or new-grad cyber role from this company in '
        f'{OPENER_WINDOW_DAYS} days · 🇺🇸 US citizenship or clearance required</sub>',
    ]
    if len(added) > BURST_ROWS:
        student = [r for r in added if r.get('type') in STUDENT_TYPES]
        student.sort(key=lambda r: (_row_key(r) not in openers,
                                    not is_cyber_title(r.get('role', ''))))
        lines = [f'**{_plural(len(added), "new role")}** this run: {_type_counts(added)}.',
                 '']
        if student:
            shown = student[:BURST_LISTED]
            lines.append(f'### First {len(shown)} of {len(student)} internships '
                         f'and new-grad roles')
            lines += [format_row(r, openers, with_type=True) for r in shown]
            lines.append('')
        lines.append(f'The rest are on the [board]({board_url}).')
        return '\n'.join(lines + footer[2:])

    cyber = [r for r in added if is_cyber_title(r.get('role', ''))]
    security_co = [r for r in added if not is_cyber_title(r.get('role', ''))]
    lines = []
    for kind in TYPE_ORDER:
        rows = [r for r in cyber if r.get('type') == kind]
        if rows:
            lines += [f'## {TYPE_HEADINGS[kind]} ({len(rows)})']
            lines += [format_row(r, openers) for r in _openers_first(rows, openers)]
            lines.append('')
    if security_co:
        security_co.sort(key=lambda r: TYPE_ORDER.index(r['type'])
                         if r.get('type') in TYPE_ORDER else len(TYPE_ORDER))
        lines += [f'## {SECURITY_CO_HEADING} ({len(security_co)})']
        lines += [format_row(r, openers, with_type=True) for r in security_co]
        lines.append('')
    return '\n'.join(lines + footer[1:])


def stream_comment(stream, rows, openers, run_at, board_url):
    """Render one alert-issue comment for the rows a stream matched."""
    stamp = f'{run_at:%b} {run_at.day}, {run_at:%H:%M} UTC'
    rows = _openers_first(rows, openers)
    noun = stream.nouns[len(rows) != 1]
    lines = [f'**{len(rows)} {noun}** · {stamp}']
    lines += [format_row(r, openers) for r in rows[:COMMENT_ROWS]]
    if len(rows) > COMMENT_ROWS:
        lines.append(f'- and {len(rows) - COMMENT_ROWS} more on the [board]({board_url})')
    return '\n'.join(lines)


def stream_issue_body(stream, board_url):
    """Body for a stream's alert issue, including the marker notify.py looks for."""
    return '\n'.join([
        f'This issue gets one comment per scrape run that finds {stream.blurb}.',
        '',
        '**To get alerts:** click **Subscribe** in the sidebar. GitHub then sends '
        'each comment by email, GitHub Mobile push or your web inbox, depending on '
        'your notification settings. Click **Unsubscribe** to stop.',
        '',
        'For every new role of every type, use Watch > Custom > Releases on the '
        'repo instead. Pick one channel, or you get each role twice.',
        '',
        f'The thread is locked so only alerts land here. [Full board]({board_url})',
        '',
        _marker(stream.key),
    ])


class GitHub:
    """The few REST calls notify.py needs, failing loudly on any error."""

    def __init__(self, token, repo):
        self.repo = repo
        self.session = requests.Session()
        self.session.headers.update(gh_headers(token))

    def call(self, method, path, want, **kwargs):
        resp = self.session.request(method, f'{API}/repos/{self.repo}{path}',
                                    timeout=20, **kwargs)
        if resp.status_code != want:
            raise RuntimeError(f'{method} {path}: HTTP {resp.status_code} {resp.text[:200]}')
        return resp.json() if resp.content else None

    def create_release(self, tag, title, body):
        return self.call('POST', '/releases', 201, json={
            'tag_name': tag, 'target_commitish': 'main', 'name': title, 'body': body,
            'make_latest': 'true'})

    def stream_issues(self):
        found, page = {}, 1
        while True:
            batch = self.call('GET', '/issues', 200, params={
                'labels': ALERTS_LABEL, 'state': 'open', 'per_page': 100, 'page': page})
            for issue in batch:
                if 'pull_request' in issue:
                    continue
                trusted = (issue.get('author_association') in TRUSTED_AUTHORS
                           or (issue.get('user') or {}).get('login') == BOT_LOGIN)
                if not trusted:
                    continue
                for stream in STREAMS:
                    # Newest wins, so a fresh issue each recruiting season takes over.
                    if (_marker(stream.key) in (issue.get('body') or '')
                            and issue['number'] > found.get(stream.key, {}).get('number', 0)):
                        found[stream.key] = issue
            if len(batch) < 100:
                return found
            page += 1

    def create_stream_issue(self, stream, board_url):
        return self.call('POST', '/issues', 201, json={
            'title': stream.title, 'body': stream_issue_body(stream, board_url),
            'labels': [ALERTS_LABEL]})

    def comment_locked(self, issue, body):
        """Unlock, comment, relock: GITHUB_TOKEN cannot comment on a locked issue."""
        number = issue['number']
        if issue.get('locked'):
            self.call('DELETE', f'/issues/{number}/lock', 204)
        try:
            self.call('POST', f'/issues/{number}/comments', 201, json={'body': body})
        finally:
            # Runs on a failed comment too, so the thread never stays open to
            # replies that would email every subscriber.
            self.call('PUT', f'/issues/{number}/lock', 204, json={'lock_reason': 'resolved'})


def announce(events, listings, token, repo, dry_run=False):
    """Post the release and one comment per matching stream; return what was posted."""
    added = events.get('added') or []
    if not added:
        print('No new roles this run; nothing to announce')
        return []
    run_at = _run_time(events)
    board_url = f'https://github.com/{repo}#readme'
    openers = find_openers(added, listings, run_at)
    tag = f'roles-{run_at:%Y%m%d-%H%M}'
    title = release_title(added, openers)
    body = release_body(added, openers, board_url)
    comments = [(s, stream_comment(s, rows, openers, run_at, board_url))
                for s in STREAMS if (rows := [r for r in added if s.matches(r)])]

    if dry_run:
        print(f'[dry-run] release {tag}: {title}\n\n{body}\n')
        for stream, text in comments:
            print(f'[dry-run] comment on "{stream.title}":\n\n{text}\n')
        return [('release', tag)] + [('comment', s.key) for s, _ in comments]

    gh = GitHub(token, repo)
    release = gh.create_release(tag, title, body)
    print(f'Published release {tag}: {release.get("html_url", "")}')
    posted = [('release', tag)]
    issues = gh.stream_issues()
    for stream, text in comments:
        issue = issues.get(stream.key)
        if issue is None:
            issue = gh.create_stream_issue(stream, board_url)
            print(f'Created alert issue #{issue["number"]}: {stream.title}')
        gh.comment_locked(issue, text)
        print(f'Commented on #{issue["number"]} ({stream.key})')
        posted.append(('comment', stream.key))
    return posted


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dry-run', action='store_true',
                        help='print the release and comments instead of posting them')
    parser.add_argument('--events', default=os.environ.get('RUN_EVENTS_FILE'),
                        help='events file (default: $RUN_EVENTS_FILE)')
    parser.add_argument('--listings', type=Path, default=LISTINGS_FILE,
                        help='board state after the run, for the opener check')
    args = parser.parse_args(argv)

    if not args.events or not Path(args.events).exists():
        # The scrape writes this file on every non-dry run, so a missing one
        # means the handoff broke and the run's roles would go unannounced.
        print(f'ERROR: events file not found: {args.events!r}')
        return 1
    events = json.loads(Path(args.events).read_text())
    listings = json.loads(args.listings.read_text()) if args.listings.exists() else []
    repo = os.environ.get('GITHUB_REPOSITORY') or DEFAULT_REPO
    token = os.environ.get('GITHUB_TOKEN')
    if events.get('added') and not args.dry_run and not token:
        print('ERROR: GITHUB_TOKEN not set')
        return 1
    announce(events, listings, token, repo, dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
