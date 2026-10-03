#!/usr/bin/env python3
"""Snapshot and invariant checks of the title rules over real posting titles.

    python .github/scripts/test_corpus.py                  # fails on any moved verdict
    python .github/scripts/test_corpus.py --update         # rewrites the verdict column
    python .github/scripts/test_corpus.py --rebuild FILE   # new title list, see select()

fixtures/title_corpus.tsv holds the titles of a full scrape plus every stored
row, each with the verdict the title rules give it. A rule change that moves
any verdict fails here until the PR rewrites the column with --update, so that
file's diff is the change's blast radius: every changed line is a real title
whose verdict moved, and a reviewer reads each one.

Titles are judged with the location 'Remote (US)' and no description, so the
snapshot covers the title rules alone. The location rules have their own
tables in test_classification.py.

The invariants then hold every accepted title to rules that no keyword change
may break, such as "a senior title is out" and "a London posting is out".
"""
import json
import re
import sys
import zlib
from pathlib import Path

import testkit  # noqa: F401

sys.path.insert(0, str(Path(__file__).parent))
import classify  # noqa: E402

CORPUS = Path(__file__).parent / 'fixtures' / 'title_corpus.tsv'
HEADER = 'title\tcompany\tsecurity_company\tverdict'
US = 'Remote (US)'

failures = 0


def fail(message):
    global failures
    failures += 1
    print(f'FAIL {message}')


def judge(title, security_company, location=US):
    return classify.judge_job(title, location, '', security_company)


def verdict(title, security_company):
    """The snapshot form of a verdict: 'level|category', or 'out|reason'."""
    accepted, reason = judge(title, security_company)
    return '|'.join(accepted) if accepted else f'out|{reason}'


def load():
    """Rows of (title, company, security_company, stored verdict)."""
    lines = CORPUS.read_text(encoding='utf-8').splitlines()
    if not lines or lines[0] != HEADER:
        sys.exit(f'{CORPUS.name}: the first line must be the header {HEADER!r}')
    rows = []
    for number, line in enumerate(lines[1:], start=2):
        fields = line.split('\t')
        if len(fields) != 4 or fields[2] not in ('0', '1'):
            sys.exit(f'{CORPUS.name}:{number}: expected 4 tab-separated fields')
        rows.append((fields[0], fields[1], fields[2] == '1', fields[3]))
    return rows


def write(rows):
    """Write rows sorted, so a refresh or an update diffs line by line."""
    rows = sorted(rows, key=lambda r: (r[0].casefold(), r[0], r[1], r[2]))
    body = [f'{t}\t{c}\t{int(s)}\t{v}' for t, c, s, v in rows]
    CORPUS.write_text('\n'.join([HEADER, *body]) + '\n', encoding='utf-8')


def select(postings):
    """Corpus rows from postings: [{"company", "title", "security_company", "stored"}].

    One row per title and flag, named by the first company alphabetically. A
    title is kept when it is a stored row or carries a level word, since with
    no description an unleveled title can only move if a level rule changes.
    A fixed fifth of the unleveled titles that pass the cyber check stays too,
    so a level-word change still shows part of its blast radius.
    """
    rows = {}
    for p in sorted(postings, key=lambda p: (p['company'], p['title'])):
        title = p['title'].replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').strip()
        security = bool(p['security_company'])
        if not title or (title, security) in rows:
            continue
        sampled = (classify.is_cyber_title(title, security)
                   and zlib.crc32(title.encode()) % 5 == 0)
        if p.get('stored') or classify.classify_level(title) is not None or sampled:
            rows[(title, security)] = p['company'].replace('\t', ' ').strip()
    return [(t, c, s, verdict(t, s)) for (t, s), c in rows.items()]


def check_snapshot(rows, update):
    moved = []
    current = []
    for title, company, security, stored in rows:
        now = verdict(title, security)
        current.append((title, company, security, now))
        if now != stored:
            moved.append((stored, now, title, company))
    if update:
        write(current)
        for stored, now, title, company in moved:
            print(f'  {stored} -> {now}: {company}, {title!r}')
        print(f'Rewrote {len(moved)} verdict(s) in {CORPUS.name}')
        return
    for stored, now, title, company in moved:
        fail(f'{company}, {title!r}: {stored} -> {now}')
    if moved:
        print(f'\n{len(moved)} verdict(s) moved. If the change means it, rerun with '
              '--update and read every changed line of the corpus diff.')


# Each invariant: (name, broken(title, security_company, accepted) -> bool).
# They are held only over titles the rules accept today.
INVARIANTS = [
    ('a senior prefix rejects',
     lambda t, s, a: judge(f'Senior {t}', s)[0] is not None),
    ('a manager suffix rejects',
     lambda t, s, a: judge(f'{t} Manager', s)[0] is not None),
    ('a foreign location rejects',
     lambda t, s, a: judge(t, s, 'London, United Kingdom')[0] is not None),
    ('the security_company flag only adds titles',
     lambda t, s, a: not s and judge(t, True)[0] is None),
    ('non-breaking and doubled spaces change nothing',
     lambda t, s, a: (judge(t.replace(' ', '\xa0'), s)[0] != a
                      or judge(t.replace(' ', '  '), s)[0] != a)),
    ('case changes nothing',
     lambda t, s, a: judge(t.upper(), s)[0] != a),
    ('a title naming an internship is an internship',
     lambda t, s, a: bool(re.search(r'\bintern(ship)?\b', t.lower())) and a[0] != 'intern'),
    ('the category is one the issue form offers',
     lambda t, s, a: a[1] not in classify.CATEGORY_ALLOWLIST),
]


def check_invariants(rows):
    accepted = {(t, s): judge(t, s)[0] for t, _, s, _ in rows}
    accepted = {key: a for key, a in accepted.items() if a}
    for name, broken in INVARIANTS:
        hits = [t for (t, s), a in accepted.items() if broken(t, s, a)]
        for title in hits[:10]:
            fail(f'{name}: {title!r}')
        if len(hits) > 10:
            print(f'     ... and {len(hits) - 10} more breaking {name!r}')
    return len(accepted)


def main():
    args = sys.argv[1:]
    if args[:1] == ['--rebuild'] and len(args) == 2:
        rows = select(json.loads(Path(args[1]).read_text()))
        write(rows)
        print(f'Wrote {len(rows)} titles to {CORPUS.name}')
        return
    update = '--update' in args
    rows = load()
    check_snapshot(rows, update)
    held = check_invariants(rows)
    if failures:
        print(f'\n{failures} corpus check(s) failed')
        sys.exit(1)
    print(f'Corpus OK: {len(rows)} titles, {held} accepted titles held to '
          f'{len(INVARIANTS)} invariants')


if __name__ == '__main__':
    main()
