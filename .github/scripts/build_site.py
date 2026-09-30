#!/usr/bin/env python3
"""Build the GitHub Pages board into _site/ from site/ and listings.json.

    python .github/scripts/build_site.py [--out _site] [--today YYYY-MM-DD]

Copies the static page, writes a trimmed _site/listings.json for it to fetch,
and writes Atom feeds: feed.xml for every type and one per type.
"""

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from classify import (  # noqa: E402
    CATEGORY_NAMES,
    EMBEDDED_STATE_CODES,
    FALLBACK_CATEGORIES,
    STATE_NAME_RE,
    US_STATE_ABBRS,
)
from rebuild_readme import NEW_DAYS, TABLE_TYPES, is_open  # noqa: E402

LISTINGS_FILE = Path('listings.json')
SITE_SRC = Path('site')
DEFAULT_OUT = Path('_site')

SITE_URL = 'https://hackedrico.github.io/2027-cyber-jobs/'
REPO_URL = 'https://github.com/HackedRico/2027-cyber-jobs'
# A tag: URI keeps an entry's id fixed if the Pages URL ever moves.
TAG_AUTHORITY = 'tag:hackedrico.github.io,2026:2027-cyber-jobs'

# Long enough that a student who saw a row last week can still see it went,
# short enough that closed rows do not bury the open ones.
CLOSED_WINDOW_DAYS = 14
FEED_SIZE = 50
FEED_TITLES = {
    'intern': 'Cybersecurity internships',
    'newgrad': 'Cybersecurity new-grad roles',
    'earlycareer': 'Cybersecurity early-career roles',
}
TYPE_WORDS = {'intern': 'Internship', 'newgrad': 'New grad', 'earlycareer': 'Early career'}

ATOM_NS = 'http://www.w3.org/2005/Atom'
ET.register_namespace('', ATOM_NS)

PAGE_FIELDS = ('company', 'role', 'location', 'type', 'category', 'clearance', 'url',
               'date_added')

STATE_CODE_RE = re.compile(r'\b([A-Z]{2})\b')
REMOTE_RE = re.compile(r'\bremote\b', re.IGNORECASE)
# Characters XML 1.0 forbids: C0 controls but tab and newlines, lone
# surrogates, U+FFFE and U+FFFF, plus DEL to match common.CONTROL_RE.
# ElementTree writes them unchecked, so one scraped title carrying one made
# every feed unparseable, and a lone surrogate made the UTF-8 write raise and
# stopped the whole build.
XML_INVALID_RE = re.compile(
    '[^\t\n\r\x20-\x7e\x80-퟿-�\U00010000-\U0010ffff]')
HTTP_URL_RE = re.compile(r'^https?://\S+$', re.IGNORECASE)


def clean_text(value):
    """`value` without the characters XML_INVALID_RE matches."""
    return XML_INVALID_RE.sub('', value)


def row_id(entry, ordinal=0):
    """Short hash of company, role, date_added and the row's ordinal among twins.

    The page uses it as a DOM key and the feeds as the entry id, so it must not
    change when the scraper rewrites a row's url, category or location. The
    renormalize and repair passes rewrite locations, and a location in the hash
    showed feed readers the same job again. `ordinal` tells apart the rows one
    posting spawns per site on the same day (Recorded Future 'Fraud Analyst' in
    DC, NY and Boston); the first such row takes 0 and hashes without it.
    """
    fields = [entry.get(f, '').strip() for f in ('company', 'role', 'date_added')]
    if ordinal:
        fields.append(str(ordinal))
    # surrogatepass: a lone surrogate in a scraped title stopped the build,
    # and it leaves every other row's id as it was.
    joined = '\x1f'.join(fields).encode('utf-8', 'surrogatepass')
    return hashlib.sha256(joined).hexdigest()[:12]


def row_ids(listings):
    """row_id for each row of `listings`, in order.

    Twins are numbered in listings.json order, which only appends and purges,
    so a location rewrite never moves a row's number.
    """
    seen = {}
    ids = []
    for entry in listings:
        twin = tuple(entry.get(f, '').strip() for f in ('company', 'role', 'date_added'))
        ids.append(row_id(entry, seen.get(twin, 0)))
        seen[twin] = seen.get(twin, 0) + 1
    return ids


def derive_places(location):
    """(states, remote) for a stored location string.

    States are postal codes in first-seen order. "Remote, GA" gives both GA and
    remote, since a student in Georgia filtering by state wants it.
    """
    states, remote = [], False
    for part in location.split(';'):
        part = part.strip()
        if not part:
            continue
        if REMOTE_RE.search(part):
            remote = True
        codes = [c for c in STATE_CODE_RE.findall(part) if c in EMBEDDED_STATE_CODES]
        if not codes:
            # "Hybrid - Massachusetts - Boston, US - ... - Maryland - Columbia"
            codes = [US_STATE_ABBRS[n.lower()] for n in STATE_NAME_RE.findall(part)]
        for code in codes:
            if code not in states:
                states.append(code)
    return states, remote


def _parse_date(value):
    try:
        return datetime.strptime(value or '', '%Y-%m-%d').date()
    except ValueError:
        return None


def select_rows(listings, today):
    """Open rows, plus rows closed in the last CLOSED_WINDOW_DAYS days."""
    cutoff = today - timedelta(days=CLOSED_WINDOW_DAYS)
    picked = []
    for entry in listings:
        if is_open(entry):
            picked.append(entry)
            continue
        closed_on = _parse_date(entry.get('closed_date'))
        if closed_on is not None and closed_on >= cutoff:
            picked.append(entry)
    return picked


def page_row(entry, rid=None):
    """The fields the page reads, plus id, states and remote."""
    out = {'id': rid or row_id(entry)}
    for field in PAGE_FIELDS:
        value = entry.get(field, '')
        out[field] = clean_text(value).strip() if isinstance(value, str) else value
    out['clearance'] = bool(entry.get('clearance'))
    out['category'] = out['category'] or 'Security Engineering'
    out['states'], out['remote'] = derive_places(out['location'])
    if not is_open(entry):
        out['closed'] = True
        out['url'] = ''
        out['closed_date'] = entry.get('closed_date', '')
    return out


def _newest_first(rows):
    return sorted(rows, key=lambda r: (r['date_added'], r['company'].lower(), r['role']),
                  reverse=True)


def page_data(listings, today):
    """The object written to _site/listings.json."""
    ids = {id(e): rid for e, rid in zip(listings, row_ids(listings), strict=True)}
    rows = _newest_first(page_row(e, ids[id(e)]) for e in select_rows(listings, today))
    return {
        'generated': today.isoformat(),
        'new_days': NEW_DAYS,
        'types': list(TABLE_TYPES),
        'categories': CATEGORY_NAMES + [c for c in FALLBACK_CATEGORIES
                                        if c not in CATEGORY_NAMES],
        'rows': rows,
    }


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, f'{{{ATOM_NS}}}{tag}',
                       {k: clean_text(str(v)) for k, v in attrs.items()})
    if text is not None:
        el.text = clean_text(text)
    return el


def _stamp(day):
    return f'{day}T00:00:00Z'


def _entry_html(row):
    flag = ('🇺🇸 Asks for a security clearance or U.S. citizenship' if row['clearance']
            else 'No clearance or citizenship flag')
    lines = [f'<b>{html.escape(row["company"])}</b>: {html.escape(row["role"])}',
             *(html.escape(text) for text in (
                 f'{TYPE_WORDS[row["type"]]} · {row["category"]}',
                 row['location'], flag, f'Added {row["date_added"]}'))]
    return '<p>' + '<br>'.join(lines) + '</p>'


def build_feed(rows, kind=None):
    """An Atom feed of the FEED_SIZE newest open rows, all types or one."""
    picked = [r for r in rows if not r.get('closed') and (kind is None or r['type'] == kind)]
    picked = _newest_first(picked)[:FEED_SIZE]
    name = f'feed-{kind}.xml' if kind else 'feed.xml'
    title = FEED_TITLES[kind] if kind else 'Cybersecurity internships, new grad and early career'
    feed = ET.Element(f'{{{ATOM_NS}}}feed')
    _sub(feed, 'id', f'{TAG_AUTHORITY}:{name}')
    _sub(feed, 'title', f'2027 Cyber Jobs: {title}')
    _sub(feed, 'subtitle', 'US cybersecurity roles for students, scraped twice a day.')
    _sub(feed, 'updated', _stamp(picked[0]['date_added'] if picked else '2026-01-01'))
    _sub(feed, 'link', rel='self', type='application/atom+xml', href=SITE_URL + name)
    _sub(feed, 'link', rel='alternate', type='text/html',
         href=SITE_URL + (f'?type={kind}' if kind else ''))
    author = _sub(feed, 'author')
    _sub(author, 'name', '2027 Cyber Jobs')
    _sub(author, 'uri', REPO_URL)
    for row in picked:
        entry = _sub(feed, 'entry')
        _sub(entry, 'id', f'{TAG_AUTHORITY}:job:{row["id"]}')
        flag = ' 🇺🇸' if row['clearance'] else ''
        _sub(entry, 'title', f'{row["company"]}: {row["role"]}{flag}')
        _sub(entry, 'updated', _stamp(row['date_added']))
        _sub(entry, 'published', _stamp(row['date_added']))
        # A feed reader opens any scheme it is handed, javascript: included,
        # so a row without an http(s) link points at the board instead.
        href = row['url'] if HTTP_URL_RE.match(row['url'] or '') else SITE_URL
        _sub(entry, 'link', rel='alternate', type='text/html', href=href)
        _sub(entry, 'category', term=row['category'])
        _sub(entry, 'content', _entry_html(row), type='html')
    ET.indent(feed)
    return name, ('<?xml version="1.0" encoding="utf-8"?>\n'
                  + ET.tostring(feed, encoding='unicode') + '\n')


def build(out=DEFAULT_OUT, listings_file=LISTINGS_FILE, src=SITE_SRC, today=None):
    """Write the whole site to `out`, replacing whatever was there."""
    today = today or date.today()
    listings = json.loads(Path(listings_file).read_text(encoding='utf-8'))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    # Empty the directory rather than delete it, so a local http.server
    # started inside it keeps serving across rebuilds.
    for child in out.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(src, out, dirs_exist_ok=True)
    data = page_data(listings, today)
    (out / 'listings.json').write_text(
        json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    for kind in (None, *TABLE_TYPES):
        name, xml = build_feed(data['rows'], kind)
        (out / name).write_text(xml, encoding='utf-8')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--today', type=date.fromisoformat, default=None)
    args = parser.parse_args()
    data = build(args.out, today=args.today)
    closed = sum(1 for r in data['rows'] if r.get('closed'))
    print(f'Built {args.out}: {len(data["rows"]) - closed} open rows, '
          f'{closed} closed in the last {CLOSED_WINDOW_DAYS} days')


if __name__ == '__main__':
    main()
