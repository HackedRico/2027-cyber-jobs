#!/usr/bin/env python3
"""Check listings.json and the README before a writer workflow commits them.

    python .github/scripts/check_outputs.py [--baseline FILE]

Exits 1 on a broken row. With --baseline (the listings.json already on main),
a problem the baseline has too is printed as a warning instead, so a bad row
that predates this check cannot block every scrape until someone fixes it by
hand. A README table that differs from a fresh rebuild is only ever a warning.
Stdlib only, so it runs before any dependency is installed.
"""

import argparse
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import rebuild_readme  # noqa: E402
from classify import CATEGORY_ALLOWLIST, listing_dedup_key  # noqa: E402
from common import normalize_url  # noqa: E402

LISTINGS_FILE = Path('listings.json')
README_FILE = Path('README.md')

REQUIRED_FIELDS = {
    'company': str, 'role': str, 'location': str, 'type': str, 'category': str,
    'clearance': bool, 'url': str, 'source': str, 'date_added': str,
}
NONEMPTY_FIELDS = ('company', 'role', 'location', 'type', 'category', 'source', 'date_added')
TYPES = {'intern', 'newgrad', 'earlycareer'}
DATE_FIELDS = ('date_added', 'closed_date', 'missing_since')
URL_RE = re.compile(r'^https?://\S+$')
STATS_RE = re.compile(r'<!-- STATS -->.*?<!-- /STATS -->', re.DOTALL)


def _label(entry):
    return ' | '.join(str(entry.get(k, '')) for k in ('company', 'role', 'location'))


def _is_open(entry):
    return not entry.get('closed') and bool(entry.get('url'))


def check_row(entry):
    """Problems with one listings.json row, as strings."""
    if not isinstance(entry, dict):
        return [f'row is not an object: {entry!r}'[:200]]
    label = _label(entry)
    problems = []
    for field, kind in REQUIRED_FIELDS.items():
        if field not in entry:
            problems.append(f'{label}: missing field `{field}`')
        elif not isinstance(entry[field], kind):
            problems.append(f'{label}: `{field}` is {type(entry[field]).__name__}, '
                            f'want {kind.__name__}')
    for field in NONEMPTY_FIELDS:
        if isinstance(entry.get(field), str) and not entry[field].strip():
            problems.append(f'{label}: `{field}` is empty')
    if isinstance(entry.get('type'), str) and entry['type'] and entry['type'] not in TYPES:
        problems.append(f"{label}: type `{entry['type']}` is not one of {sorted(TYPES)}")
    category = entry.get('category')
    if isinstance(category, str) and category and category not in CATEGORY_ALLOWLIST:
        problems.append(f'{label}: category `{category}` is not an allowed category')
    for field in DATE_FIELDS:
        value = entry.get(field)
        if value is None or (field == 'date_added' and not value):
            continue
        try:
            datetime.strptime(value, '%Y-%m-%d')
        except (TypeError, ValueError):
            problems.append(f'{label}: `{field}` {value!r} is not YYYY-MM-DD')
    if 'closed' in entry and not isinstance(entry['closed'], bool):
        problems.append(f'{label}: `closed` is not a boolean')
    url = entry.get('url')
    if isinstance(url, str):
        if url and not URL_RE.match(url):
            problems.append(f'{label}: url {url!r} is not a single-line http(s) link')
        elif not url and not entry.get('closed'):
            problems.append(f'{label}: url is empty but the row is not closed')
    return problems


def check_listings(listings):
    """Problems across listings.json: bad rows and duplicate open rows."""
    if not isinstance(listings, list):
        return ['listings.json is not a JSON array']
    problems = []
    for entry in listings:
        problems.extend(check_row(entry))
    urls, keys = {}, {}
    for entry in listings:
        if not isinstance(entry, dict) or not _is_open(entry):
            continue
        url = normalize_url(entry['url'])
        if url in urls:
            problems.append(f'{_label(entry)}: same url as open row {urls[url]}')
        else:
            urls[url] = _label(entry)
        key = listing_dedup_key(entry.get('company', ''), entry.get('role', ''),
                                entry.get('location', ''))
        if key in keys:
            problems.append(f'{_label(entry)}: open duplicate of company, role and location')
        else:
            keys[key] = _label(entry)
    return problems


def readme_drift(listings_path=LISTINGS_FILE, readme_path=README_FILE):
    """Warnings when README.md differs from what rebuild_readme would write now."""
    if not readme_path.exists():
        return [f'{readme_path} not found']
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copy(listings_path, tmp / 'listings.json')
        shutil.copy(readme_path, tmp / 'README.md')
        saved = (rebuild_readme.LISTINGS_FILE, rebuild_readme.README_FILE,
                 rebuild_readme.COMPANIES_MD)
        rebuild_readme.LISTINGS_FILE = tmp / 'listings.json'
        rebuild_readme.README_FILE = tmp / 'README.md'
        rebuild_readme.COMPANIES_MD = tmp / 'companies.md'
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rebuild_readme.main()
        except SystemExit:
            return ['rebuild_readme could not rebuild the README (missing table markers?)']
        finally:
            (rebuild_readme.LISTINGS_FILE, rebuild_readme.README_FILE,
             rebuild_readme.COMPANIES_MD) = saved
        rebuilt = (tmp / 'README.md').read_text()
    current = readme_path.read_text()
    # The stats line carries today's date, which a rebuild always changes.
    rebuilt, current = STATS_RE.sub('', rebuilt), STATS_RE.sub('', current)
    if rebuilt == current:
        return []
    now_lines, want_lines = current.splitlines(), rebuilt.splitlines()
    first = next((i for i, (a, b) in enumerate(zip(now_lines, want_lines, strict=False))
                  if a != b), min(len(now_lines), len(want_lines)))
    return [f'README.md differs from a rebuild of listings.json, first at line {first + 1} '
            f'({len(now_lines)} lines now, {len(want_lines)} after a rebuild)']


def _load(path):
    try:
        return json.loads(Path(path).read_text()), None
    except (OSError, ValueError) as e:
        return None, f'{path} could not be read as JSON: {e}'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--baseline', help='listings.json from main; its problems only warn')
    args = parser.parse_args()

    listings, err = _load(LISTINGS_FILE)
    errors = [err] if err else check_listings(listings)
    known = set()
    if args.baseline:
        base, base_err = _load(args.baseline)
        if base_err:
            print(f'::warning::{base_err}; checking without a baseline')
        else:
            known = set(check_listings(base))

    fatal = [p for p in errors if p not in known]
    for p in errors:
        level = 'error' if p in fatal else 'warning'
        print(f'::{level}::listings.json: {p}')
    if not err:
        for w in readme_drift():
            print(f'::warning::{w}')

    rows = len(listings) if isinstance(listings, list) else 0
    print(f'Checked {rows} rows: {len(fatal)} new problem(s), '
          f'{len(errors) - len(fatal)} already on main')
    sys.exit(1 if fatal else 0)


if __name__ == '__main__':
    main()
