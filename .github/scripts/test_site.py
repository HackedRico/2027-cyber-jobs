#!/usr/bin/env python3
"""Tests for the GitHub Pages build, run offline.

    python .github/scripts/test_site.py
"""
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_site as bs  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 9, 28)
A = '{http://www.w3.org/2005/Atom}'

failures = 0


def check(name, got, want):
    global failures
    if got != want:
        failures += 1
        print(f'FAIL {name}: got {got!r}, want {want!r}')


def row(**overrides):
    base = {'company': 'Acme', 'role': 'SOC Analyst I', 'location': 'Austin, TX',
            'type': 'earlycareer', 'category': 'SOC & Detection', 'clearance': False,
            'url': 'https://boards.greenhouse.io/acme/jobs/1', 'source': 'Greenhouse',
            'date_added': '2026-09-20'}
    base.update(overrides)
    return base


# --- derive_places -------------------------------------------------------------
PLACES = [
    ('Arlington, VA', (['VA'], False)),
    ('Remote (US)', ([], True)),
    ('Remote, GA', (['GA'], True)),
    ('Remote (US); San Francisco, CA; New York City, NY; Washington, DC',
     (['CA', 'NY', 'DC'], True)),
    ('Baltimore, MD; Waldorf, MD; Washington, DC; Woodbridge, VA', (['MD', 'DC', 'VA'], False)),
    ('CA (US); AZ (US)', (['CA', 'AZ'], False)),
    ('VA, McLean', (['VA'], False)),
    ('Chicago, IL, More...', (['IL'], False)),
    ('Hybrid - Massachusetts - Boston, US - Headquarters - Maryland - Columbia',
     (['MA', 'MD'], False)),
    ('Aguadilla, PR', (['PR'], False)),
    ('United States', ([], False)),
    ('', ([], False)),
]
for location, want in PLACES:
    check(f'derive_places({location!r})', bs.derive_places(location), want)


# --- select_rows -----------------------------------------------------------------
OPEN = row(role='Open role')
RECENT = row(role='Closed last week', url='', closed=True, closed_date='2026-09-20')
EDGE = row(role='Closed 14 days ago', url='', closed=True, closed_date='2026-09-14')
OLD = row(role='Closed 15 days ago', url='', closed=True, closed_date='2026-09-13')
DEAD = row(role='No url, not flagged closed', url='')
picked = [r['role'] for r in bs.select_rows([OPEN, RECENT, EDGE, OLD, DEAD], TODAY)]
check('select_rows keeps open rows and rows closed within 14 days', picked,
      ['Open role', 'Closed last week', 'Closed 14 days ago'])

page = bs.page_row(RECENT)
check('page_row flags a closed row', (page['closed'], page['url'], page['closed_date']),
      (True, '', '2026-09-20'))
check('page_row keeps only the page fields',
      sorted(bs.page_row(OPEN)),
      sorted(['id', 'company', 'role', 'location', 'type', 'category', 'clearance', 'url',
              'date_added', 'states', 'remote']))
check('page_row drops source and missing_since',
      {'source', 'missing_since'} & set(bs.page_row(row(missing_since='2026-09-01'))), set())


# --- row_id ----------------------------------------------------------------------
check('row_id is stable across calls', bs.row_id(OPEN), bs.row_id(dict(OPEN)))
check('row_id ignores url and category',
      bs.row_id(OPEN), bs.row_id(row(role='Open role', url='https://x.example/2',
                                     category='GRC & Risk')))
# The scraper's renormalize and repair passes rewrite stored locations; a feed
# entry id that moved with them showed readers the same job twice.
check('row_id ignores the location',
      bs.row_id(OPEN), bs.row_id(row(role='Open role', location='Austin, TX; Remote (US)')))
check('row_id changes with date_added',
      bs.row_id(OPEN) == bs.row_id(row(role='Open role', date_added='2026-09-21')), False)
check('row_id is a short hex string', (len(bs.row_id(OPEN)), set(bs.row_id(OPEN)) <= set(
    '0123456789abcdef')), (12, True))

TWINS = [row(role='Fraud Analyst', location='Washington, DC'),
         row(role='Fraud Analyst', location='New York, NY'),
         row(role='Fraud Analyst', location='Boston, MA')]
twin_ids = bs.row_ids(TWINS)
check('row_ids tells apart one role posted per site on the same day',
      len(set(twin_ids)), 3)
check('row_ids gives the first twin the plain row_id', twin_ids[0], bs.row_id(TWINS[0]))
moved = [dict(t) for t in TWINS]
moved[1]['location'] = 'New York City, NY'
check('row_ids survives a twin location rewrite', bs.row_ids(moved), twin_ids)
data = bs.page_data(TWINS, TODAY)
check('page_data uses row_ids', sorted(r['id'] for r in data['rows']), sorted(twin_ids))

real = json.loads((ROOT / 'listings.json').read_text(encoding='utf-8'))
ids = bs.row_ids(real)
check('row_ids is unique across listings.json', len(ids), len(set(ids)))


# --- build and feeds -------------------------------------------------------------
LISTINGS = [
    row(company='Acme', role='Security Intern', type='intern', date_added='2026-09-25'),
    row(company='Beta & Co', role='Cyber <Analyst>', type='newgrad', clearance=True,
        location='Arlington, VA; Remote (US)', date_added='2026-09-24'),
    row(company='Gamma', role='SOC Analyst', date_added='2026-09-23'),
    RECENT,
    OLD,
]


def build_twice():
    outs = []
    for _ in range(2):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'listings.json'
            src.write_text(json.dumps(LISTINGS), encoding='utf-8')
            out = Path(tmp) / '_site'
            bs.build(out, listings_file=src, src=ROOT / 'site', today=TODAY)
            outs.append({p.name: p.read_bytes() if p.suffix == '.png'
                         else p.read_text(encoding='utf-8') for p in out.iterdir()})
    return outs


first, second = build_twice()
check('build is deterministic', first == second, True)
check('build copies the page and writes data and feeds',
      {'index.html', 'app.js', 'styles.css', 'listings.json', 'feed.xml', 'feed-intern.xml',
       'feed-newgrad.xml', 'feed-earlycareer.xml'} <= set(first), True)

# A link unfurler shows a blank card when og:image points at a file the build did not ship.
og_images = re.findall(r'<meta property="og:image" content="https://hackedrico\.github\.io/'
                       r'2027-cyber-jobs/([^"]+)"', first['index.html'])
check('index.html names one og:image on the Pages site', len(og_images), 1)
check('the og:image file ships with the build', og_images[0] in first, True)
check('the og:image is a 1280x640 png', first[og_images[0]][:8] == b'\x89PNG\r\n\x1a\n'
      and int.from_bytes(first[og_images[0]][16:20], 'big') == 1280
      and int.from_bytes(first[og_images[0]][20:24], 'big') == 640, True)

data = json.loads(first['listings.json'])
check('site data holds open rows and recent closed rows',
      [r['role'] for r in data['rows']],
      ['Security Intern', 'Cyber <Analyst>', 'SOC Analyst', 'Closed last week'])
check('site data lists every category', 'Engineering @ Security Co' in data['categories'], True)
beta = data['rows'][1]
check('site data carries states and remote', (beta['states'], beta['remote']), (['VA'], True))


def entries(xml_text):
    feed = ET.fromstring(xml_text)
    check('feed root is an Atom feed', feed.tag, f'{A}feed')
    for tag in ('id', 'title', 'updated', 'author'):
        check(f'feed has <{tag}>', feed.find(f'{A}{tag}') is not None, True)
    return feed, feed.findall(f'{A}entry')


feed, items = entries(first['feed.xml'])
check('feed.xml carries open rows only, newest first',
      [e.find(f'{A}title').text for e in items],
      ['Acme: Security Intern', 'Beta & Co: Cyber <Analyst> 🇺🇸', 'Gamma: SOC Analyst'])
check('feed updated is the newest date_added', feed.find(f'{A}updated').text,
      '2026-09-25T00:00:00Z')
entry = items[1]
check('entry id is a tag: URI from the stable id', entry.find(f'{A}id').text,
      f'{bs.TAG_AUTHORITY}:job:{data["rows"][1]["id"]}')
check('entry updated comes from date_added', entry.find(f'{A}updated').text,
      '2026-09-24T00:00:00Z')
check('entry links to the apply url', entry.find(f'{A}link').get('href'),
      'https://boards.greenhouse.io/acme/jobs/1')
content = entry.find(f'{A}content')
check('entry content is escaped html', content.get('type'), 'html')
for needle in ('Beta &amp; Co', 'Cyber &lt;Analyst&gt;', 'Arlington, VA; Remote (US)',
               'SOC &amp; Detection', 'New grad', '🇺🇸'):
    check(f'entry content names {needle!r}', needle in content.text, True)
for e in items:
    for tag in ('id', 'title', 'updated', 'link', 'content'):
        check(f'every entry has <{tag}>', e.find(f'{A}{tag}') is not None, True)

for kind, want in (('intern', ['Acme: Security Intern']),
                   ('newgrad', ['Beta & Co: Cyber <Analyst> 🇺🇸']),
                   ('earlycareer', ['Gamma: SOC Analyst'])):
    _, items = entries(first[f'feed-{kind}.xml'])
    check(f'feed-{kind}.xml carries its type only',
          [e.find(f'{A}title').text for e in items], want)

_, items = entries(bs.build_feed(bs.page_data(real, TODAY)['rows'])[1])
check('feed.xml caps at 50 entries', len(items), bs.FEED_SIZE)
check('real feed entry ids are unique', len({e.find(f'{A}id').text for e in items}), len(items))


if failures:
    print(f'\n{failures} site test(s) failed')
    sys.exit(1)
print('All site tests passed')
