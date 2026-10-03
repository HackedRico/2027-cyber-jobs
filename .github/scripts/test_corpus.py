#!/usr/bin/env python3
"""Snapshot and invariant checks of the title rules over real posting titles.

    python .github/scripts/test_corpus.py                  # fails on any moved verdict
    python .github/scripts/test_corpus.py --update         # rewrites the verdict column
    python .github/scripts/test_corpus.py --base origin/main
    python .github/scripts/test_corpus.py --rebuild FILE   # new title list, see select()

fixtures/title_corpus.tsv holds the titles of a full scrape plus every stored
row as of its last refresh, each with the verdict the title rules give it. A
rule change that moves any verdict fails here until the PR rewrites the column
with --update, so that file's diff is the change's blast radius: every changed
line is a real title whose verdict moved, and a reviewer reads each one.

--base REF judges the corpus and the current listings.json rows with the
classify.py at REF and with the working tree's, and prints every title they
disagree on. It works when a PR refreshes the corpus too, and it exits 0.

Titles are judged with the location 'Remote (US)' and no description, so the
snapshot covers the title rules alone. The location rules have their own
tables in test_classification.py.
"""
import importlib.util
import json
import re
import subprocess
import sys
import zlib
from pathlib import Path

import testkit  # noqa: F401

SCRIPTS = Path(__file__).parent
ROOT = SCRIPTS.parent.parent
sys.path.insert(0, str(SCRIPTS))
import classify  # noqa: E402

CORPUS = SCRIPTS / 'fixtures' / 'title_corpus.tsv'
HEADER = 'title\tcompany\tsecurity_company\tverdict'
US = 'Remote (US)'

failures = 0


def fail(message):
    global failures
    failures += 1
    print(f'FAIL {message}')


def judge(title, security_company, location=US, rules=classify):
    return rules.judge_job(title, location, '', security_company)


def verdict(title, security_company, rules=classify):
    """The snapshot form of a verdict: 'level|category', or 'out|reason'."""
    accepted, reason = judge(title, security_company, rules=rules)
    return '|'.join(accepted) if accepted else f'out|{reason}'


def load():
    """Rows of (title, company, security_company, stored verdict)."""
    # split('\n'), not splitlines(): a title may hold U+2028 or a form feed.
    lines = CORPUS.read_text(encoding='utf-8').rstrip('\n').split('\n')
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

    One row per title and flag, named by the first company alphabetically, with
    whitespace collapsed so no separator can split a row. A title is kept when
    it is a stored row or carries a level word, since with no description an
    unleveled title can only move if a level rule changes. A fixed fifth of the
    unleveled titles that pass the cyber check stays too, so a level-word change
    still shows part of its blast radius.
    """
    rows = {}
    for p in sorted(postings, key=lambda p: (p['company'], p['title'])):
        title = ' '.join(str(p['title']).split())
        security = bool(p['security_company'])
        if not title or (title, security) in rows:
            continue
        sampled = (classify.is_cyber_title(title, security)
                   and zlib.crc32(title.encode()) % 5 == 0)
        if p.get('stored') or classify.classify_level(title) is not None or sampled:
            rows[(title, security)] = ' '.join(str(p['company']).split())
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


def _variants(title):
    yield title.replace(' ', '\xa0')
    yield title.replace(' ', '  ')
    yield title.upper()
    yield re.sub(r'\s+&\s+', ' and ', title)
    yield re.sub(r'\s+and\s+', ' & ', title, flags=re.I)


# Design rules no keyword change may break. Each names the titles it covers;
# change one only when the design it states changes.
def check_invariants(rows):
    titles = {(t, s) for t, _, s, _ in rows}
    accepted = {key: judge(*key)[0] for key in titles}
    for (title, security), base in accepted.items():
        # Spacing, case and '&' for 'and' never move a title on or off the board.
        for variant in _variants(title):
            if (judge(variant, security)[0] is None) != (base is None):
                fail(f'{variant!r} is {"out" if base else "in"}, but {title!r} is not')
                break
        if base is None:
            continue
        if judge(f'Senior {title}', security)[0] is not None:
            fail(f'a senior prefix leaves {title!r} on the board')
        if not security and judge(title, True)[0] is None:
            fail(f'the security_company flag takes {title!r} off the board')
        # The intern words win the level before every other signal.
        if re.search(r'\bintern(ship)?\b', title.lower()) and base[0] != 'intern':
            fail(f'{title!r} names an internship but levels as {base[0]}')
    return sum(1 for a in accepted.values() if a)


def base_rules(ref):
    """classify.py as it stands at a git ref, loaded as a separate module."""
    source = subprocess.run(['git', 'show', f'{ref}:.github/scripts/classify.py'],
                            cwd=ROOT, capture_output=True, text=True, check=True).stdout
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader('classify_base', loader=None))
    exec(compile(source, f'{ref}:classify.py', 'exec'), module.__dict__)
    return module


def compare_with(ref):
    import common
    import yaml
    rules = base_rules(ref)
    flags = common.security_company_flags(yaml.safe_load((ROOT / 'companies.yml').read_text()))
    listings = json.loads((ROOT / 'listings.json').read_text())
    pairs = {(t, s): c for t, c, s, _ in load()}
    for row in listings:
        key = (row.get('role', ''), flags.get(row.get('company', ''), False))
        pairs.setdefault(key, row.get('company', ''))
    moved = sorted((verdict(t, s, rules), verdict(t, s), c, t)
                   for (t, s), c in pairs.items() if verdict(t, s, rules) != verdict(t, s))
    for before, after, company, title in moved:
        print(f'  {before} -> {after}: {company}, {title!r}')
    print(f'{len(moved)} of {len(pairs)} titles judge differently at {ref} and in the '
          'working tree')


def main():
    args = sys.argv[1:]
    if args[:1] == ['--rebuild'] and len(args) == 2:
        rows = select(json.loads(Path(args[1]).read_text()))
        write(rows)
        print(f'Wrote {len(rows)} titles to {CORPUS.name}')
        return
    if args[:1] == ['--base'] and len(args) == 2:
        compare_with(args[1])
        return
    rows = load()
    check_snapshot(rows, '--update' in args)
    held = check_invariants(rows)
    if failures:
        print(f'\n{failures} corpus check(s) failed')
        sys.exit(1)
    print(f'Corpus OK: {len(rows)} titles, {held} of them accepted, all held to the '
          'invariants')


if __name__ == '__main__':
    main()
