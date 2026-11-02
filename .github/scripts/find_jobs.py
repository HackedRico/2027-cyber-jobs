#!/usr/bin/env python3
"""Search the open roles in listings.json, for a student or their AI assistant.

    python3 .github/scripts/find_jobs.py [filters] [--json] [--limit N]
    python3 .github/scripts/find_jobs.py --categories

Filters of different kinds must all match. Several values of one kind match any
of them, so `--type intern --type newgrad` finds both. `--state` and `--remote`
together match a role in one of those states or a remote one. A keyword matches
the start of a word in the title or the category, so `analy` finds Analyst.
Stdlib only, so it runs on a bare python3 in a fresh clone.
"""

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_site import last_scrape_date, page_data  # noqa: E402
from classify import EMBEDDED_STATE_CODES, state_names  # noqa: E402
from rebuild_readme import CATEGORY_BLURBS, TABLE_TYPES  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LISTINGS_FILE = ROOT / 'listings.json'
HEALTH_FILE = ROOT / '.github/data/health.json'
DEFAULT_LIMIT = 50


def resolve_categories(names, known):
    """The `known` categories each of `names` names, matched case-insensitively.

    A part of a name counts, so 'soc' is SOC & Detection and 'cloud' is both
    cloud categories. Raises ValueError for a name that matches none.
    """
    picked = []
    for name in names:
        hits = [c for c in known if name.strip().lower() in c.lower()]
        if not hits:
            raise ValueError(f'no category matches {name!r}; run --categories for the list')
        picked += [c for c in hits if c not in picked]
    return picked


def resolve_states(names):
    """Postal codes for `names`, each a code such as 'va' or a name such as 'Virginia'."""
    codes = []
    for name in names:
        found = [name.strip().upper()] if len(name.strip()) == 2 else state_names(name)
        if not found or not set(found) <= EMBEDDED_STATE_CODES:
            raise ValueError(f'{name!r} is not a US state or postal code')
        codes += [c for c in found if c not in codes]
    return codes


def search(rows, types=(), categories=(), states=(), remote=False, no_clearance=False,
           companies=(), keywords=(), since_days=None, today=None):
    """The open `rows` (page rows, as build_site.page_data makes them) that match.

    `categories` and `states` must already be resolved to exact names and codes.
    """
    today = today or date.today()
    cutoff = (today - timedelta(days=since_days)).isoformat() if since_days is not None else ''
    words = [re.compile(r'\b' + re.escape(k.strip()), re.IGNORECASE) for k in keywords]
    picked = []
    for r in rows:
        if r.get('closed'):
            continue
        if types and r['type'] not in types:
            continue
        if categories and r['category'] not in categories:
            continue
        if (states or remote) and not (
                (remote and r['remote']) or set(states) & set(r['states'])):
            continue
        if no_clearance and r['clearance']:
            continue
        if companies and not any(c.lower() in r['company'].lower() for c in companies):
            continue
        if words and not any(w.search(r['role']) or w.search(r['category']) for w in words):
            continue
        if r['date_added'] < cutoff:
            continue
        picked.append(r)
    return picked


def format_row(r):
    # One field per line part, not a delimited line: titles carry '|' and ','
    # ("2027 | Americas | New York City Area | Internal Audit ...").
    flag = ' · 🇺🇸 clearance or citizenship' if r['clearance'] else ''
    return (f"{r['company']}: {r['role']}\n"
            f"  {r['type']} · {r['category']} · {r['location']} · added {r['date_added']}{flag}\n"
            f"  {r['url']}")


def main(argv=None, today=None):
    parser = argparse.ArgumentParser(
        description=__doc__.split('\n\n')[0],
        epilog='Example: find_jobs.py --type intern --category soc --state VA --remote '
               '--no-clearance')
    parser.add_argument('--type', action='append', default=[], choices=TABLE_TYPES,
                        help='intern, newgrad (new-grad programs) or earlycareer (0 to 2 years)')
    parser.add_argument('--category', action='append', default=[],
                        help='a category or part of its name; see --categories')
    parser.add_argument('--state', action='append', default=[],
                        help='a postal code or state name, such as VA or Virginia; with '
                             '--remote, a role in the state or a remote one')
    parser.add_argument('--remote', action='store_true',
                        help='roles with a remote place in the US; with --state, either')
    parser.add_argument('--no-clearance', action='store_true',
                        help='skip roles that ask for a clearance or U.S. citizenship')
    parser.add_argument('--company', action='append', default=[],
                        help='part of an employer name')
    parser.add_argument('--keyword', action='append', default=[],
                        help='the start of a word in the title or category, such as '
                             'detect, cloud or summer')
    parser.add_argument('--since', type=int, metavar='DAYS',
                        help='only roles added in the last DAYS days')
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT,
                        help=f'most roles to print, 0 for all (default {DEFAULT_LIMIT})')
    parser.add_argument('--json', action='store_true', help='print a JSON array of rows')
    parser.add_argument('--categories', action='store_true',
                        help='list each category, what it covers and its open roles')
    args = parser.parse_args(argv)

    today = today or date.today()
    listings = json.loads(LISTINGS_FILE.read_text(encoding='utf-8'))
    scraped = last_scrape_date(HEALTH_FILE)
    data = page_data(listings, today, scraped)
    rows = data['rows']
    open_rows = [r for r in rows if not r.get('closed')]

    if args.categories:
        for name in data['categories']:
            count = sum(1 for r in open_rows if r['category'] == name)
            print(f'{name} ({count} open): {CATEGORY_BLURBS.get(name, "")}')
        return 0

    try:
        categories = resolve_categories(args.category, data['categories'])
        states = resolve_states(args.state)
    except ValueError as err:
        parser.error(str(err))
    found = search(rows, args.type, categories, states, args.remote, args.no_clearance,
                   args.company, args.keyword, args.since, today)
    shown = found[:args.limit] if args.limit > 0 else found

    if scraped is None or (today - scraped).days > 1:
        print(f'Note: this clone holds data from {scraped or "an unknown date"}. Run git pull '
              'for the latest roles.', file=sys.stderr)
    if args.json:
        print(json.dumps(shown, ensure_ascii=False, indent=1))
        return 0
    print(f'{len(found)} of {len(open_rows)} open roles match. Data from the '
          f'{data["generated"]} scrape.')
    for r in shown:
        print('\n' + format_row(r))
    if len(shown) < len(found):
        print(f'\n{len(found) - len(shown)} more. Narrow the filters or pass --limit 0.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
