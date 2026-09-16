#!/usr/bin/env python3
"""Diff two `scrape_jobs.py --dry-run` logs to show what a change flips.

    git switch main && python .github/scripts/scrape_jobs.py --dry-run > before.log
    git switch -   && python .github/scripts/scrape_jobs.py --dry-run > after.log
    python .github/scripts/compare_runs.py before.log after.log

Reports rows accepted in only one run, rows whose level changed, existing rows
one run drops, reclassifies or has its location repaired and the other does
not, and boards whose status changed. Exits 1 when anything differs, so a clean
exit is the equivalence proof for a scraper or classifier change. Run the two
scrapes minutes apart: postings arrive continuously, so a row that appears in
only the later log under a title the change does not target is noise, not a
flip.
"""

import re
import sys
from pathlib import Path
from typing import NamedTuple

NEW_RE = re.compile(r'^\s*NEW \[(\w+)\] (.+)$')
DROP_RE = re.compile(r'^\s*DROP \[([\w-]+)\] (.+)$')
RECLASSIFY_RE = re.compile(r'^\s*RECLASSIFY \[(\w+) -> (\w+)\] (.+)$')
# Both locations are printed with !r, so anchoring on the repr quotes keeps a
# title that itself contains ": " ("Summer 2027 Intern: Cybersecurity") intact.
REPAIR_RE = re.compile(
    r'''^\s*REPAIRED \[location\] (.+): ('[^']*'|"[^"]*") -> ('[^']*'|"[^"]*")$''')
BOARD_RE = re.compile(r'^Checking (.+?)\.\.\. (ok|zero|FAILED|CRASHED) \((\d+) postings')


class Run(NamedTuple):
    rows: dict        # "Company — Title @ Location" -> level
    dropped: dict     # "Company — Title" -> reason
    reclassified: dict  # "Company — Title" -> (old, new)
    repaired: dict    # ("Company — Title", old location) -> new location
    boards: dict      # label -> (status, count)


def parse_log(text):
    rows, dropped, reclassified, repaired, boards = {}, {}, {}, {}, {}
    for line in text.splitlines():
        if m := NEW_RE.match(line):
            rows[m.group(2).strip()] = m.group(1)
        elif m := DROP_RE.match(line):
            dropped[m.group(2).strip()] = m.group(1)
        elif m := RECLASSIFY_RE.match(line):
            reclassified[m.group(3).strip()] = (m.group(1), m.group(2))
        elif m := REPAIR_RE.match(line):
            # Keyed with the old location too: one title is posted per site,
            # so same-title repairs must not overwrite each other.
            repaired[(m.group(1).strip(), m.group(2))] = m.group(3)
        elif m := BOARD_RE.match(line):
            boards[m.group(1)] = (m.group(2), int(m.group(3)))
    return Run(rows, dropped, reclassified, repaired, boards)


def _section(title, items):
    if not items:
        return []
    return [f'{title} ({len(items)}):'] + [f'  - {item}' for item in items] + ['']


def diff(before, after):
    """Return report lines; an empty list means the runs are equivalent."""
    only_before = sorted(k for k in before.rows if k not in after.rows)
    only_after = sorted(k for k in after.rows if k not in before.rows)
    relevelled = sorted(f'{k}: {before.rows[k]} -> {after.rows[k]}'
                        for k in before.rows if k in after.rows
                        and before.rows[k] != after.rows[k])
    drops = sorted(f'{k} [{r}]' for k, r in after.dropped.items()
                   if before.dropped.get(k) != r)
    undrops = sorted(f'{k} [{r}]' for k, r in before.dropped.items()
                     if after.dropped.get(k) != r)
    reclass = sorted(f'{k}: {o} -> {n}' for k, (o, n) in after.reclassified.items()
                     if before.reclassified.get(k) != (o, n))
    repairs = sorted(f'{k}: {o} -> {n}' for (k, o), n in after.repaired.items()
                     if before.repaired.get((k, o)) != n)
    unrepairs = sorted(f'{k}: {o} -> {n}' for (k, o), n in before.repaired.items()
                       if after.repaired.get((k, o)) != n)
    boards = sorted(f'{label}: {before.boards[label][0]} ({before.boards[label][1]}) '
                    f'-> {after.boards[label][0]} ({after.boards[label][1]})'
                    for label in before.boards if label in after.boards
                    and before.boards[label][0] != after.boards[label][0])
    return (_section('Rows accepted only before', [f'[{before.rows[k]}] {k}' for k in only_before])
            + _section('Rows accepted only after', [f'[{after.rows[k]}] {k}' for k in only_after])
            + _section('Rows whose level changed', relevelled)
            + _section('Existing rows dropped only after', drops)
            + _section('Existing rows dropped only before', undrops)
            + _section('Existing rows reclassified only after', reclass)
            + _section('Existing rows whose location was repaired only after', repairs)
            + _section('Existing rows whose location was repaired only before', unrepairs)
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
