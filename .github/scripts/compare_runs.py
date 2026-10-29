#!/usr/bin/env python3
"""Diff two `scrape_jobs.py --dry-run` logs to show what a change flips.

    git switch main && python .github/scripts/scrape_jobs.py --dry-run > before.log
    git switch -   && python .github/scripts/scrape_jobs.py --dry-run > after.log
    python .github/scripts/compare_runs.py before.log after.log

Reports rows accepted in only one run, rows whose level changed, existing rows
one run drops, retires, reclassifies, refreshes, revives, renames or has its
location repaired and the other does not, postings one run skips at insert or
defers on a failed detail fetch, a changed renormalized-location or
control-character count or closing summary line, and boards whose status
changed. Event lines are counted, so a run that
drops one of two same-title rows differs from one that drops both. Exits 1
when anything differs, so a clean exit is the equivalence proof for a scraper
or classifier change, with one blind spot: an event line names a stored row by
company and role only, so two runs that act on different rows of a same-title
group, as many times each, compare equal. Run the two scrapes minutes apart:
postings arrive continuously, so a row that appears in only the later log
under a title the change does not target is noise, not a flip.
"""

import re
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

NEW_RE = re.compile(r'^\s*NEW \[(\w+)\] (.+)$')
DROP_RE = re.compile(r'^\s*DROP \[([\w-]+)\] (.+)$')
RETIRED_RE = re.compile(r'^\s*RETIRED \[([\w-]+)\] (.+)$')
RECLASSIFY_RE = re.compile(r'^\s*RECLASSIFY \[(\w+) -> (\w+)\] (.+)$')
# Both locations are printed with !r, so anchoring on the repr quotes keeps a
# title that itself contains ": " ("Summer 2027 Intern: Cybersecurity") intact.
REPAIR_RE = re.compile(
    r'''^\s*REPAIRED \[location\] (.+): ('[^']*'|"[^"]*") -> ('[^']*'|"[^"]*")$''')
# A refreshed category is a quoted string and a clearance flag a bare bool.
_VALUE = r'''('[^']*'|"[^"]*"|True|False|None)'''
REFRESH_RE = re.compile(rf'^\s*REFRESHED \[(\w+)\] (.+): {_VALUE} -> {_VALUE}$')
BOARD_RE = re.compile(r'^Checking (.+?)\.\.\. (ok|zero|FAILED|CRASHED) \((\d+) postings')
REVIVED_RE = re.compile(r'^\s*REVIVED (.+)$')
RENAMED_RE = re.compile(r'^\s*RENAMED \[company\] (.+)$')
SKIPPED_RE = re.compile(r'^\s*SKIPPED \[([\w-]+)\] (.+)$')
# A 429 or 5xx on a detail fetch defers the posting, so without these lines a
# title deferred in one run read as accepted only in the other, a false flip.
DEFERRED_RE = re.compile(r'^\s*DEFERRED \[([\w-]+)\] (.+)$')
RENORMALIZED_RE = re.compile(r'^Renormalized (\d+) location\(s\)$')
STRIPPED_RE = re.compile(r'^Stripped control characters from (\d+) row\(s\)$')
SUMMARY_RE = re.compile(r'^Added \d+ new listing\(s\), .+$')


class Run(NamedTuple):
    rows: dict        # "Company — Title @ Location" -> level
    dropped: Counter  # ("Company — Title", reason)
    reclassified: Counter  # ("Company — Title", old, new)
    repaired: Counter  # ("Company — Title", old location, new location)
    boards: dict      # label -> (status, count)
    refreshed: Counter  # ("Company — Title", field, old, new)
    retired: Counter  # ("Company — Title", reason)
    revived: Counter  # "Company — Title"
    renamed: Counter  # "Old Company -> Company — Title"
    skipped: Counter  # ("Company — Title @ 'location'", reason)
    deferred: Counter  # ("Company — Title", reason)
    renormalized: int
    stripped: int     # rows stripped of control characters
    summary: str | None  # the closing "Added N new listing(s), ..." line


def parse_log(text):
    rows, boards = {}, {}
    # Counted, not keyed by title: 57 stored rows share a company and role
    # with another row (JPMorgan Chase — Security Engineer II, one per site),
    # and a dict kept one DROP line of several, so a run that dropped one
    # more of them still compared equal. Repairs too: listings.json holds two
    # CrowdStrike "Analyst I, Falcon Complete (Remote)" rows at one location,
    # so even a (title, old location) key repeats.
    dropped, reclassified, refreshed, retired = Counter(), Counter(), Counter(), Counter()
    revived, renamed, skipped, repaired = Counter(), Counter(), Counter(), Counter()
    deferred = Counter()
    renormalized, stripped, summary = 0, 0, None
    for line in text.splitlines():
        if m := NEW_RE.match(line):
            rows[m.group(2).strip()] = m.group(1)
        elif m := DROP_RE.match(line):
            dropped[(m.group(2).strip(), m.group(1))] += 1
        elif m := RETIRED_RE.match(line):
            retired[(m.group(2).strip(), m.group(1))] += 1
        elif m := RECLASSIFY_RE.match(line):
            reclassified[(m.group(3).strip(), m.group(1), m.group(2))] += 1
        elif m := REVIVED_RE.match(line):
            revived[m.group(1).strip()] += 1
        elif m := RENAMED_RE.match(line):
            renamed[m.group(1).strip()] += 1
        elif m := SKIPPED_RE.match(line):
            skipped[(m.group(2).strip(), m.group(1))] += 1
        elif m := DEFERRED_RE.match(line):
            deferred[(m.group(2).strip(), m.group(1))] += 1
        elif m := RENORMALIZED_RE.match(line):
            renormalized = int(m.group(1))
        elif m := STRIPPED_RE.match(line):
            stripped = int(m.group(1))
        elif m := SUMMARY_RE.match(line):
            summary = line
        elif m := REPAIR_RE.match(line):
            repaired[(m.group(1).strip(), m.group(2), m.group(3))] += 1
        elif m := REFRESH_RE.match(line):
            refreshed[(m.group(2).strip(), m.group(1), m.group(3), m.group(4))] += 1
        elif m := BOARD_RE.match(line):
            boards[m.group(1)] = (m.group(2), int(m.group(3)))
    return Run(rows, dropped, reclassified, repaired, boards, refreshed, retired,
               revived, renamed, skipped, deferred, renormalized, stripped, summary)


def _section(title, items):
    if not items:
        return []
    return [f'{title} ({len(items)}):'] + [f'  - {item}' for item in items] + ['']


def _changed(title, before, after):
    if before == after:
        return []
    return [f'{title}:', f'  - before: {before}', f'  - after:  {after}', '']


def _extra(counts, other, fmt):
    return sorted(fmt(key) for key in (counts - other).elements())


def _tagged(key):
    return f'{key[0]} [{key[1]}]'


def _change(key):
    return f'{key[0]}: {key[1]} -> {key[2]}'


def _refresh(key):
    return f'{key[0]} [{key[1]}]: {key[2]} -> {key[3]}'


def _both_ways(title, before, after, fmt):
    return (_section(f'{title} only after', _extra(after, before, fmt))
            + _section(f'{title} only before', _extra(before, after, fmt)))


def diff(before, after):
    """Return report lines; an empty list means the runs are equivalent."""
    only_before = sorted(k for k in before.rows if k not in after.rows)
    only_after = sorted(k for k in after.rows if k not in before.rows)
    relevelled = sorted(f'{k}: {before.rows[k]} -> {after.rows[k]}'
                        for k in before.rows if k in after.rows
                        and before.rows[k] != after.rows[k])
    boards = sorted(f'{label}: {before.boards[label][0]} ({before.boards[label][1]}) '
                    f'-> {after.boards[label][0]} ({after.boards[label][1]})'
                    for label in before.boards if label in after.boards
                    and before.boards[label][0] != after.boards[label][0])
    return (_section('Rows accepted only before', [f'[{before.rows[k]}] {k}' for k in only_before])
            + _section('Rows accepted only after', [f'[{after.rows[k]}] {k}' for k in only_after])
            + _section('Rows whose level changed', relevelled)
            + _both_ways('Existing rows dropped', before.dropped, after.dropped, _tagged)
            + _both_ways('Existing rows retired', before.retired, after.retired, _tagged)
            + _both_ways('Existing rows reclassified', before.reclassified,
                         after.reclassified, _change)
            + _both_ways('Existing rows whose location was repaired', before.repaired,
                         after.repaired, _change)
            + _both_ways('Existing rows refreshed', before.refreshed, after.refreshed, _refresh)
            + _both_ways('Closed rows revived', before.revived, after.revived, str)
            + _both_ways('Existing rows whose company was renamed', before.renamed,
                         after.renamed, str)
            + _both_ways('Postings skipped at insert', before.skipped, after.skipped, _tagged)
            + _both_ways('Postings deferred on a failed detail fetch', before.deferred,
                         after.deferred, _tagged)
            + _changed('Locations renormalized', before.renormalized, after.renormalized)
            + _changed('Rows stripped of control characters', before.stripped,
                       after.stripped)
            + _changed('Closing summary', before.summary or '(none)',
                       after.summary or '(none)')
            + _section('Boards whose status changed', boards))


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print(__doc__)
        return 2
    before = parse_log(Path(args[0]).read_text())
    after = parse_log(Path(args[1]).read_text())
    if not before.boards or not after.boards:
        print('ERROR: a log has no "Checking <board>..." lines; is it a scrape log?')
        return 2
    report = diff(before, after)
    if not report:
        print(f'No differences across {len(after.boards)} boards '
              f'and {len(after.rows)} accepted rows')
        return 0
    print('\n'.join(report).rstrip())
    return 1


if __name__ == '__main__':
    sys.exit(main())
