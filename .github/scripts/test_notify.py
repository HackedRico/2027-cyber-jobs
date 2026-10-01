#!/usr/bin/env python3
"""Tests for notify.py, the release and alert-issue announcer, run offline.

    python .github/scripts/test_notify.py

Covers the release title and body, opener detection, which alert stream a row
lands in, markdown escaping of scraped fields, and the unlock, comment, relock
sequence against mocked GitHub HTTP.
"""
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import notify  # noqa: E402
import responses  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    if got != want:
        failures += 1
        print(f'FAIL {name}: got {got!r}, want {want!r}')


# --- release body and format_row ------------------------------------------------
RUN_AT = '2026-09-23T13:37:00Z'
GH = 'https://api.github.com/repos/o/r'


def _row(company, role, kind='intern', **extra):
    row = {'company': company, 'role': role, 'location': 'Austin, TX', 'type': kind,
           'category': 'Security Engineering', 'clearance': False,
           'url': f'https://x/{company}/{role}'.replace(' ', '-'), 'source': 'Greenhouse',
           'date_added': '2026-09-23'}
    row.update(extra)
    return row


def _events(added):
    return {'schema_version': 1, 'run_at': RUN_AT, 'added': added,
            'revived': [], 'retired': []}


check('format_row neutralises mentions, links and HTML in scraped fields',
      notify.format_row(_row('Acme', 'Intern @octocat [x](https://evil) <b>',
                             url='https://x/a_(b)'), set()),
      '- **Acme**: Intern @&#8203;octocat \\[x\\](https&#8203;://evil) &lt;b&gt; · Austin, TX · '
      'Security Engineering · [Apply](https://x/a_%28b%29)')
check('format_row caps a long site list',
      notify.format_row(_row('Acme', 'SOC Intern', location='A, TX; B, TX; C, TX; D, TX'),
                        set()).split(' · ')[1], 'A, TX; B, TX; 2 more')


def _release_payload():
    posts = [c for c in responses.calls if c.request.url.endswith('/releases')]
    return json.loads(posts[0].request.body) if posts else None


def _mock_github(issues=()):
    responses.post(f'{GH}/releases', status=201, json={'html_url': 'https://rel'})
    responses.get(f'{GH}/issues', json=list(issues))
    responses.post(f'{GH}/issues', status=201, json={'number': 99, 'locked': False})
    responses.post(re.compile(rf'{GH}/issues/\d+/comments'), status=201, json={})
    responses.put(re.compile(rf'{GH}/issues/\d+/lock'), status=204)
    responses.delete(re.compile(rf'{GH}/issues/\d+/lock'), status=204)


@responses.activate
def test_notify_skips_release_when_nothing_added():
    posted = notify.announce(_events([]), [], 'tok', 'o/r')
    check('notify posts nothing when no row was added', posted, [])
    check('notify makes no HTTP call when no row was added', len(responses.calls), 0)


@responses.activate
def test_notify_burst_cap_lists_ten_student_rows():
    _mock_github()
    added = ([_row('Intern Co', f'Security Intern {i}') for i in range(8)]
             + [_row('Grad Co', f'Security Analyst New Grad {i}', 'newgrad') for i in range(6)]
             + [_row('Early Co', f'Security Analyst I {i}', 'earlycareer') for i in range(9)])
    notify.announce(_events(added), [], 'tok', 'o/r')
    body = _release_payload()['body']
    check('burst release states the counts per type',
          '**23 new roles** this run: 8 intern, 6 new grad, 9 early career.' in body, True)
    check('burst release lists ten rows', body.count('\n- **'), 10)
    check('burst release leaves early-career rows to the board', 'Early Co' in body, False)
    check('burst release links the board',
          '[board](https://github.com/o/r#readme)' in body, True)


@responses.activate
def test_notify_puts_security_company_rows_last():
    _mock_github()
    added = [_row('CrowdStrike', 'Software Engineer Intern'),
             _row('Acme', 'SOC Analyst Intern', clearance=True)]
    earlier = [_row('CrowdStrike', 'Old', date_added='2026-09-01'),
               _row('Acme', 'Old', date_added='2026-09-01')]
    notify.announce(_events(added), earlier, 'tok', 'o/r')
    body = _release_payload()['body']
    check('cyber rows stay under their type heading',
          body.startswith('## 🎒 Internships (1)\n- **Acme** 🇺🇸: SOC Analyst Intern'), True)
    check('non-cyber rows go under the security-company heading',
          '## 🛡️ Also hiring at security companies (1)\n- **CrowdStrike**: '
          'Software Engineer Intern · Austin, TX · intern' in body, True)


@responses.activate
def test_notify_opener_leads_the_title():
    _mock_github()
    added = [_row('Amazon', 'Security Engineer Internship 2027 (US)'),
             _row('Northrop Grumman', '2027 Intern, Cybersecurity Engineer'),
             _row('Anduril', 'Security Engineer', 'earlycareer')]
    listings = added + [
        _row('Northrop Grumman', 'Cyber Intern', closed=True, closed_date='2026-09-01'),
        # Closed more than 60 days ago, so it no longer counts against Amazon.
        _row('Amazon', 'Old Intern', closed=True, closed_date='2026-07-01')]
    notify.announce(_events(added), listings, 'tok', 'o/r')
    payload = _release_payload()
    check('opener leads the release title', payload['name'],
          '🚨 Amazon opened intern hiring · 3 new roles')
    check('opener row carries the siren',
          '- **Amazon** 🚨: Security Engineer Internship 2027 (US)' in payload['body'], True)
    check('a company with a recent intern row is not an opener',
          '**Northrop Grumman** 🚨' in payload['body'], False)
    check('release tag is the run second in UTC', payload['tag_name'], 'roles-20260923-133700')
    check('release becomes latest on main',
          (payload['make_latest'], payload['target_commitish']), ('true', 'main'))


@responses.activate
def test_notify_unlocks_comments_and_relocks():
    issue = {'number': 5, 'locked': True, 'author_association': 'OWNER',
             'body': 'Subscribe\n<!-- alert-stream: earlycareer -->'}
    stranger = {'number': 6, 'locked': False, 'author_association': 'NONE',
                'body': '<!-- alert-stream: earlycareer -->'}
    _mock_github([issue, stranger])
    notify.announce(_events([_row('Acme', 'Security Analyst I', 'earlycareer')]),
                    [], 'tok', 'o/r')
    calls = [(c.request.method, c.request.url.removeprefix(GH))
             for c in responses.calls if '/issues/5' in c.request.url]
    check('an owner issue is unlocked, commented on and relocked', calls,
          [('DELETE', '/issues/5/lock'), ('POST', '/issues/5/comments'),
           ('PUT', '/issues/5/lock')])
    check("a stranger's issue with the marker is ignored",
          any('/issues/6' in c.request.url for c in responses.calls), False)


@responses.activate
def test_notify_fails_when_relock_fails():
    issue = {'number': 5, 'locked': True, 'author_association': 'OWNER',
             'body': '<!-- alert-stream: earlycareer -->'}
    responses.post(f'{GH}/releases', status=201, json={})
    responses.get(f'{GH}/issues', json=[issue])
    responses.delete(f'{GH}/issues/5/lock', status=204)
    responses.post(f'{GH}/issues/5/comments', status=201, json={})
    responses.put(f'{GH}/issues/5/lock', status=403)
    try:
        notify.announce(_events([_row('Acme', 'Security Analyst I', 'earlycareer')]),
                        [], 'tok', 'o/r')
        raised = False
    except RuntimeError:
        raised = True
    check('a failed relock fails the step', raised, True)


@responses.activate
def test_notify_creates_missing_stream_issue():
    _mock_github()
    notify.announce(_events([_row('Acme', 'Security Analyst I', 'earlycareer')]),
                    [], 'tok', 'o/r')
    created = [json.loads(c.request.body) for c in responses.calls
               if c.request.method == 'POST' and c.request.url == f'{GH}/issues']
    check('a missing stream issue is created once', len(created), 1)
    check('the new issue is titled and labeled for its stream',
          (created[0]['title'], created[0]['labels']), ('🌱 Early-career alerts', ['alerts']))
    check('the new issue carries the stream marker',
          '<!-- alert-stream: earlycareer -->' in created[0]['body'], True)
    sequence = [(c.request.method, c.request.url.removeprefix(GH))
                for c in responses.calls if '/issues/99' in c.request.url]
    check('the new issue gets the comment and is then locked', sequence,
          [('POST', '/issues/99/comments'), ('PUT', '/issues/99/lock')])


# --- release_title -------------------------------------------------------------
def test_release_title():
    rows = [_row('Acme', 'SOC Intern'), _row('Beta', 'Security Analyst New Grad', 'newgrad'),
            _row('Gamma', 'Security Analyst I', 'earlycareer')]
    check('with no opener the title counts rows per type',
          notify.release_title(rows, set()),
          '3 new roles: 1 intern, 1 new grad, 1 early career')
    check('one row reads singular', notify.release_title(rows[:1], set()),
          '1 new role: 1 intern')
    openers = {notify._row_key(r) for r in rows[:2]}
    check('openers lead the title with their hiring types',
          notify.release_title(rows, openers),
          '🚨 Acme, Beta opened intern + new grad hiring · 3 new roles')
    many = [_row(f'Co{i}', 'SOC Intern') for i in range(4)]
    check('more than two opener companies collapse to a count',
          notify.release_title(many, {notify._row_key(r) for r in many}),
          '🚨 Co0, Co1 + 2 more opened intern hiring · 4 new roles')
    odd = [_row('Acme\nInc\x00', 'SOC Intern'), _row('Beta\r\x1b[2J  Corp', 'SOC Intern')]
    check('a release title flattens newlines and control characters in company names',
          notify.release_title(odd, {notify._row_key(r) for r in odd}),
          '🚨 Acme Inc, Beta [2J Corp opened intern hiring · 2 new roles')


# --- find_openers --------------------------------------------------------------
def test_find_openers():
    run_at = datetime(2026, 9, 23, 13, 37, tzinfo=UTC)
    added = [
        _row('Fresh', 'SOC Intern'),
        _row('Open Co', 'SOC Intern'),
        _row('Recent Co', 'SOC Intern'),
        _row('Stale Co', 'SOC Intern'),
        _row('Other Type Co', 'SOC Intern'),
        _row('Case Co', 'SOC Intern'),
        _row('Early Co', 'Security Analyst I', 'earlycareer'),
        _row('Security Co', 'Software Engineer Intern'),
    ]
    listings = added + [
        _row('Open Co', 'Old Intern', date_added='2026-01-02'),
        _row('Recent Co', 'Old Intern', closed=True, closed_date='2026-08-01'),
        _row('Stale Co', 'Old Intern', closed=True, closed_date='2026-07-01'),
        _row('Other Type Co', 'Old Grad', 'newgrad'),
        _row('CASE CO', 'Old Intern'),
    ]
    check('an opener is a cyber student row at a company with none of that type in 60 days',
          sorted(role[0] for role in notify.find_openers(added, listings, run_at)),
          ['Fresh', 'Other Type Co', 'Stale Co'])
    check("a run's own rows do not count against each other",
          notify.find_openers([_row('Twin', 'SOC Intern'), _row('Twin', 'Cyber Intern')],
                              [_row('Twin', 'SOC Intern'), _row('Twin', 'Cyber Intern')],
                              run_at),
          {notify._row_key(_row('Twin', 'SOC Intern')),
           notify._row_key(_row('Twin', 'Cyber Intern'))})


# --- STREAMS -------------------------------------------------------------------
def test_stream_selection():
    def streams(row):
        return [s.key for s in notify.STREAMS if s.matches(row)]

    check('an intern row with no flag reaches the student streams',
          streams(_row('Acme', 'SOC Intern')), ['intern', 'student-no-flag'])
    check('a flagged new-grad row skips the no-flag stream',
          streams(_row('Acme', 'Cyber Analyst New Grad', 'newgrad', clearance=True)),
          ['newgrad'])
    check('an early-career row is never a student row, and remote is read from the location',
          streams(_row('Acme', 'Security Analyst I', 'earlycareer',
                       location='Austin, TX; Remote (US)')), ['earlycareer', 'remote'])
    check('a row with no location matches no remote stream',
          streams(_row('Acme', 'SOC Intern', location=None)), ['intern', 'student-no-flag'])


# --- escaping ------------------------------------------------------------------
def test_markdown_escaping():
    check('md_escape collapses whitespace and escapes markdown and table syntax',
          notify.md_escape('  a|b\n`c` *d* _e_ ~f~ \\g  '),
          'a\\|b \\`c\\` \\*d\\* \\_e\\_ \\~f\\~ \\\\g')
    check('md_escape leaves an ampersand for the plain-text email',
          notify.md_escape('Cloud & Infra'), 'Cloud & Infra')
    check('md_escape breaks every @ that could ping a user',
          notify.md_escape('@team mail a@b'), '@&#8203;team mail a@&#8203;b')
    # "&#64;octocat" rendered as a live @octocat in a release.
    ENTITIES = [
        ('&#64;octocat', '&amp;#&#8203;64;octocat'),
        ('&#x40;octocat', '&amp;#x40;octocat'),
        ('&commat;octocat', '&amp;commat;octocat'),
        ('&lt;script&gt;', '&amp;lt;script&amp;gt;'),
        ('R&D Intern', 'R&D Intern'),
        # Reads as a named entity, so it is escaped; it renders as typed.
        ('AT&T; Security', 'AT&amp;T; Security'),
        ('Cloud & Infra', 'Cloud & Infra'),
    ]
    for text, want in ENTITIES:
        check(f'md_escape escapes an & only where it starts an entity: {text!r}',
              notify.md_escape(text), want)
    check('md_escape breaks issue references',
          notify.md_escape('see #12 and HackedRico/2027-cyber-jobs#3, not C# or #tag'),
          'see #&#8203;12 and HackedRico/2027-cyber-jobs#&#8203;3, not C# or #tag')
    check('_apply_link drops a non-http url', notify._apply_link('javascript:alert(1)'), '')
    check('_apply_link drops a url with whitespace', notify._apply_link('https://x/a b'), '')
    check('_apply_link keeps an https url', notify._apply_link('https://x/a'),
          ' · [Apply](https://x/a)')


# --- burst cap -----------------------------------------------------------------
@responses.activate
def test_notify_lists_every_row_up_to_the_burst_cap():
    _mock_github()
    added = [_row('Intern Co', f'Security Intern {i}') for i in range(notify.BURST_ROWS)]
    notify.announce(_events(added), [], 'tok', 'o/r')
    body = _release_payload()['body']
    check('a run of exactly BURST_ROWS rows lists them all under their heading',
          (body.startswith(f'## 🎒 Internships ({notify.BURST_ROWS})'),
           body.count('\n- **')), (True, notify.BURST_ROWS))


# --- unlock, comment, relock ---------------------------------------------------
@responses.activate
def test_notify_unlocked_issue_is_locked_after_the_comment():
    issue = {'number': 7, 'locked': False, 'author_association': 'MEMBER',
             'body': '<!-- alert-stream: earlycareer -->'}
    _mock_github([issue])
    notify.announce(_events([_row('Acme', 'Security Analyst I', 'earlycareer')]),
                    [], 'tok', 'o/r')
    calls = [(c.request.method, c.request.url.removeprefix(GH))
             for c in responses.calls if '/issues/7' in c.request.url]
    check('an unlocked issue is commented on and then locked, with no unlock call', calls,
          [('POST', '/issues/7/comments'), ('PUT', '/issues/7/lock')])


@responses.activate
def test_notify_relocks_when_the_comment_fails():
    issue = {'number': 5, 'locked': True, 'author_association': 'OWNER',
             'body': '<!-- alert-stream: earlycareer -->'}
    responses.post(f'{GH}/releases', status=201, json={})
    responses.get(f'{GH}/issues', json=[issue])
    responses.delete(f'{GH}/issues/5/lock', status=204)
    responses.post(f'{GH}/issues/5/comments', status=500, json={})
    responses.put(f'{GH}/issues/5/lock', status=204)
    try:
        notify.announce(_events([_row('Acme', 'Security Analyst I', 'earlycareer')]),
                        [], 'tok', 'o/r')
        raised = False
    except RuntimeError:
        raised = True
    calls = [(c.request.method, c.request.url.removeprefix(GH))
             for c in responses.calls if '/issues/5' in c.request.url]
    check('a failed comment fails the step', raised, True)
    check('...and the thread is locked again first', calls[-1], ('PUT', '/issues/5/lock'))


@responses.activate
def test_notify_dry_run_posts_nothing():
    posted = notify.announce(
        _events([_row('Acme', 'SOC Intern', location='Remote (US)')]), [], None, 'o/r',
        dry_run=True)
    check('a dry run reports the release and each matching stream',
          posted, [('release', 'roles-20260923-133700'), ('comment', 'intern'),
                   ('comment', 'student-no-flag'), ('comment', 'remote')])
    check('a dry run makes no HTTP call', len(responses.calls), 0)


@responses.activate
def test_notify_retries_a_taken_release_tag():
    # An add-listing run that finishes in the same second as a scrape.
    responses.post(f'{GH}/releases', status=422, json={'message': 'already_exists'})
    responses.post(f'{GH}/releases', status=201, json={'html_url': 'https://rel'})
    responses.get(f'{GH}/issues', json=[])
    responses.post(f'{GH}/issues', status=201, json={'number': 99, 'locked': False})
    responses.post(re.compile(rf'{GH}/issues/\d+/comments'), status=201, json={})
    responses.put(re.compile(rf'{GH}/issues/\d+/lock'), status=204)
    os.environ['GITHUB_RUN_ID'] = '4242'
    try:
        posted = notify.announce(_events([_row('Acme', 'Security Analyst I', 'earlycareer')]),
                                 [], 'tok', 'o/r')
    finally:
        del os.environ['GITHUB_RUN_ID']
    tags = [json.loads(c.request.body)['tag_name'] for c in responses.calls
            if c.request.url.endswith('/releases')]
    check('a taken tag is retried with the run id',
          tags, ['roles-20260923-133700', 'roles-20260923-133700-4242'])
    check('the retried release is reported', posted[0], ('release', 'roles-20260923-133700-4242'))


@responses.activate
def test_notify_one_failed_stream_does_not_stop_the_others():
    broken = {'number': 5, 'locked': False, 'author_association': 'OWNER',
              'body': '<!-- alert-stream: intern -->'}
    working = {'number': 6, 'locked': False, 'author_association': 'OWNER',
               'body': '<!-- alert-stream: student-no-flag -->'}
    responses.post(f'{GH}/releases', status=500, json={})
    responses.get(f'{GH}/issues', json=[broken, working])
    responses.post(f'{GH}/issues/5/comments', status=500, json={})
    responses.post(f'{GH}/issues/6/comments', status=201, json={})
    responses.put(re.compile(rf'{GH}/issues/\d+/lock'), status=204)
    try:
        notify.announce(_events([_row('Acme', 'SOC Intern')]), [], 'tok', 'o/r')
        error = None
    except notify.AnnounceError as e:
        error = str(e)
    check('a failed release and stream still fail the step at the end',
          error, 'failed: release roles-20260923-133700, comment intern')
    check('the stream after a failed one still gets its comment',
          any(c.request.url == f'{GH}/issues/6/comments' and c.response.status_code == 201
              for c in responses.calls), True)


def test_notify_main_returns_one_on_a_failed_announcement():
    saved = notify.announce

    def failing(*args, **kwargs):
        raise notify.AnnounceError('failed: release x')
    with tempfile.TemporaryDirectory() as tmp:
        events = Path(tmp) / 'events.json'
        events.write_text(json.dumps(_events([_row('Acme', 'SOC Intern')])))
        os.environ['GITHUB_TOKEN'] = 't'
        notify.announce = failing
        try:
            code = notify.main(['--events', str(events), '--listings', str(events)])
        finally:
            notify.announce = saved
            del os.environ['GITHUB_TOKEN']
    check('main exits 1 after a failed announcement', code, 1)


for fn in (test_notify_skips_release_when_nothing_added,
           test_notify_burst_cap_lists_ten_student_rows,
           test_notify_puts_security_company_rows_last,
           test_notify_opener_leads_the_title, test_notify_unlocks_comments_and_relocks,
           test_notify_fails_when_relock_fails, test_notify_creates_missing_stream_issue,
           test_release_title, test_find_openers, test_stream_selection,
           test_markdown_escaping, test_notify_lists_every_row_up_to_the_burst_cap,
           test_notify_unlocked_issue_is_locked_after_the_comment,
           test_notify_relocks_when_the_comment_fails, test_notify_dry_run_posts_nothing,
           test_notify_retries_a_taken_release_tag,
           test_notify_one_failed_stream_does_not_stop_the_others,
           test_notify_main_returns_one_on_a_failed_announcement):
    fn()

if failures:
    print(f'\n{failures} notify test(s) failed')
    sys.exit(1)
print('All notify tests passed')
