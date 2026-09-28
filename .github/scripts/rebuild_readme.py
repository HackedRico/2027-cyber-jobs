#!/usr/bin/env python3
"""Rebuild the README job tables (and companies.md) from listings.json."""

import html
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from classify import CATEGORY_NAMES, FALLBACK_CATEGORIES

LISTINGS_FILE = Path('listings.json')
README_FILE = Path('README.md')
COMPANIES_YML = Path('companies.yml')
COMPANIES_MD = Path('companies.md')

APPLY_BADGE = 'https://img.shields.io/badge/Apply-0969da?style=flat-square'

# Internships lead: most readers are students hunting a summer role.
TABLE_TYPES = ('intern', 'newgrad', 'earlycareer')

# Written by replace_table rather than kept from the file, so a column change
# lands in one edit here instead of a hand edit to three README headers.
TABLE_HEADER = ('| Company | Role | Apply | Location | Added |\n'
                '| ------- | ---- | ----- | -------- | ----- |\n')
CLOSED_HEADER = ('| Company | Role | Closed |\n'
                 '| ------- | ---- | ------ |\n')

# Matches purge_stale_listings' default in classify.py.
CLOSED_RETENTION_DAYS = 60
NEW_DAYS = 7

# One line per category for the README legend. test_classification.py fails
# when a category in classify.CATEGORY_RULES has no line here.
CATEGORY_BLURBS = {
    'AI Security & Safety': 'securing ML systems, red-teaming models, and AI safety '
                            'and alignment work',
    'Offensive Security': 'penetration testing, red teaming, exploit development and '
                          'vulnerability research',
    'SOC & Detection': 'alert triage, detection engineering, threat hunting and '
                       'incident response in a security operations center',
    'Threat Intelligence': 'tracking threat actors and writing intelligence on their '
                           'campaigns',
    'Forensics & IR': 'digital forensics, malware analysis and reverse engineering',
    'AppSec & ProdSec': 'securing the software a company ships through code review, '
                        'secure development and DevSecOps',
    'Cloud & Infra Security': 'securing cloud accounts, networks and internal platforms',
    'Identity & IAM': 'identity, access management and zero trust, the systems that '
                      'decide who can log in to what',
    'GRC & Risk': 'governance, risk and compliance work such as audits, policy and '
                  'frameworks like NIST and FedRAMP',
    'Security Engineering': 'general security and cyber roles that fit no narrower '
                            'bucket',
    'Engineering @ Security Co': 'a software or IT role at a company whose main business '
                                 'is security',
}


def _company_sort_key(name):
    name = re.sub(r'[\U0001F000-\U0001FFFF☀-⛿✀-➿]', '', name)
    return name.strip().lower()


def escape_cell(text):
    """Neutralize markdown/HTML in listing fields (community- or scraper-supplied).

    Collapses whitespace first: an embedded newline in a scraped or hand-edited
    field would otherwise split one table row across physical lines and corrupt
    the rendered README.
    """
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    # Escape backslash first so it can't pair with the escapes added next and
    # render as a literal '\' that leaves the following '|' as a live delimiter.
    text = text.replace('\\', '\\\\')
    return re.sub(r'([|\[\]`])', r'\\\1', text)


def _escape_attr(text):
    # GFM splits table cells on '|' before it parses inline HTML, so a pipe
    # inside an attribute must be an entity too.
    text = re.sub(r'\s+', ' ', text).strip()
    return html.escape(text, quote=True).replace('|', '&#124;')


def is_open(entry):
    """True for a row the open tables show: not closed, and it has an apply URL.

    A url-less row without the closed flag is dead too (Amazon 'Software Dev
    Engineer II, Customer Service Security' sat in the headline that way), so
    the tables and the STATS block both judge by this one predicate.
    """
    return not entry.get('closed') and bool(entry.get('url', '').strip())


def _parse_date(value):
    try:
        return datetime.strptime(value or '', '%Y-%m-%d').date()
    except ValueError:
        return None


def _today(today):
    if isinstance(today, date):
        return today
    return _parse_date(today) or datetime.now().date()


def is_new(entry, today=None):
    """True when the row landed within the last NEW_DAYS days."""
    added = _parse_date(entry.get('date_added', ''))
    return added is not None and (_today(today) - added).days <= NEW_DAYS


def format_location(location):
    location = location.strip()
    if ';' not in location:
        return escape_cell(location)
    parts = [escape_cell(p.strip()) for p in location.split(';') if p.strip()]
    if len(parts) <= 1:
        return parts[0] if parts else escape_cell(location)
    inner = '<br>'.join(parts)
    return f'<details><summary>**{len(parts)} locations**</summary>{inner}</details>'


def format_date(date_added):
    parsed = _parse_date(date_added)
    return parsed.strftime('%b %-d') if parsed else date_added


def apply_btn(url, label=''):
    # A real apply URL never contains whitespace/control chars; rejecting them
    # stops an embedded newline/tab from breaking out of the table cell and
    # injecting a fake row with a working (phishing) link. '|' is escaped so a
    # bare pipe can't open a new column either.
    if not url or not re.match(r'^https?://', url) or re.search(r'\s', url):
        return '🔒'
    href = html.escape(url, quote=True).replace('|', '&#124;')
    # A screen reader otherwise hears 198 identical "Apply" links.
    alt = _escape_attr(f'Apply: {label}' if label.strip() else 'Apply')
    return f'<a href="{href}"><img src="{APPLY_BADGE}" alt="{alt}"></a>'


def format_role(entry, today=None):
    """Role cell: 🆕, the title, 🇺🇸, and the category underneath.

    The flag lives here rather than on the company cell because a ↳ row
    replaces the company cell, and the flag belongs to the row, not the group.
    """
    role = escape_cell(entry['role'].strip())
    if is_new(entry, today):
        role = f'🆕 {role}'
    if entry.get('clearance'):
        role += ' 🇺🇸'
    category = escape_cell(entry.get('category', '') or 'Security Engineering')
    return f'{role}<br><sub>{category}</sub>'


def format_row(entry, company_col, today=None):
    role = format_role(entry, today)
    label = f'{entry["company"]} {entry["role"]}'
    btn = apply_btn(entry.get('url', '').strip(), label)
    location = format_location(entry.get('location', ''))
    added = format_date(entry.get('date_added', ''))
    return f'| {company_col} | {role} | {btn} | {location} | {added} |'


def _closed_date(entry):
    return entry.get('closed_date') or entry.get('date_added', '')


def _grouped(entries, date_of):
    """Yield (entry, company_col), newest first, with ↳ for a repeat company.

    The ↳ group keys on (company, date) inside one list, so a row only ever
    points at the row printed directly above its group.
    """
    def sort_key(e):
        parsed = _parse_date(date_of(e))
        return (-(parsed or date.min).toordinal(), _company_sort_key(e['company']))

    seen = set()
    for entry in sorted(entries, key=sort_key):
        key = (_company_sort_key(entry['company']), date_of(entry))
        if key in seen:
            yield entry, '↳'
        else:
            seen.add(key)
            yield entry, escape_cell(entry['company'].strip())


def build_table(entries, today=None):
    """Open rows, newest first by date added, with ↳ grouping."""
    return [format_row(entry, company_col, today)
            for entry, company_col in _grouped(entries, lambda e: e.get('date_added', ''))]


def build_closed_table(entries):
    """Closed rows, newest closure first, with ↳ grouping."""
    rows = []
    for entry, company_col in _grouped(entries, _closed_date):
        role = escape_cell(entry['role'].strip())
        rows.append(f'| {company_col} | {role} | {format_date(_closed_date(entry))} |')
    return rows


def closed_block(entries):
    """The folded table of closed rows, or '' when a type has none."""
    if not entries:
        return ''
    rows = '\n'.join(build_closed_table(entries))
    return (f'<details><summary>🔒 {len(entries)} closed in the last '
            f'{CLOSED_RETENTION_DAYS} days</summary>\n\n'
            f'{CLOSED_HEADER}{rows}\n\n</details>\n')


def replace_block(content, start_marker, end_marker, body):
    """Replace everything between two marker lines with body."""
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker, start_idx)
    if start_idx == -1 or end_idx == -1:
        print(f'ERROR: Could not find markers {start_marker} ... {end_marker}')
        sys.exit(1)
    head = content[:start_idx + len(start_marker)]
    inner = f'\n\n{body.rstrip()}\n\n' if body.strip() else '\n'
    return head + inner + content[end_idx:]


def replace_table(content, marker, rows):
    """Write TABLE_HEADER and rows between the TABLE_START/END markers."""
    body = TABLE_HEADER + '\n'.join(rows)
    return replace_block(content, f'<!-- TABLE_START {marker} -->',
                         f'<!-- TABLE_END {marker} -->', body)


def count_open_by_type(listings):
    """Open rows per type, by the same predicate the tables use."""
    return Counter(e.get('type') for e in listings if is_open(e))


def stats_line(listings, today=None):
    """The STATS headline: open rows per type, new this week, 🇺🇸 count."""
    today = _today(today)
    open_rows = [e for e in listings if is_open(e)]
    counts = count_open_by_type(listings)
    new = sum(1 for e in open_rows if is_new(e, today))
    flagged = sum(1 for e in open_rows if e.get('clearance'))
    return (f'**{counts["intern"]}** internships · **{counts["newgrad"]}** new grad · '
            f'**{counts["earlycareer"]}** early career open · '
            f'**{new}** added in the last {NEW_DAYS} days · '
            f'**{flagged}** need a clearance or U.S. citizenship 🇺🇸 · '
            f'updated {today.strftime("%b %-d, %Y")}')


def legend_lines():
    """One bullet per category, in classifier rule order, then the fallbacks."""
    names = list(CATEGORY_NAMES)
    names += [n for n in FALLBACK_CATEGORIES if n not in names]
    lines = []
    for name in names:
        blurb = CATEGORY_BLURBS.get(name)
        lines.append(f'- **{escape_cell(name)}**: {blurb}' if blurb
                     else f'- **{escape_cell(name)}**')
    return lines


PLATFORM_LABELS = {
    'greenhouse': 'Greenhouse',
    'lever': 'Lever',
    'ashby': 'Ashby',
    'smartrecruiters': 'SmartRecruiters',
    'workable': 'Workable',
    'recruitee': 'Recruitee',
    'pinpoint': 'Pinpoint',
    'workday': 'Workday',
}


def hiring_now_lines(listings):
    """companies.md table of employers with open rows, most open student roles first."""
    per_company = {}
    for entry in listings:
        if not is_open(entry):
            continue
        counts = per_company.setdefault(entry['company'].strip(), Counter())
        counts[entry.get('type')] += 1
    if not per_company:
        return []

    def sort_key(item):
        name, counts = item
        return (-(counts['intern'] + counts['newgrad']), -counts['earlycareer'],
                _company_sort_key(name))

    lines = [
        f'## Hiring students now ({len(per_company)})',
        '',
        'Employers with open roles on the board today.',
        '',
        '| Company | Internships | New grad | Early career |',
        '| ------- | ----------- | -------- | ------------ |',
    ]
    for name, counts in sorted(per_company.items(), key=sort_key):
        cells = [str(counts[t]) if counts[t] else '' for t in TABLE_TYPES]
        lines.append(f'| {escape_cell(name)} | {" | ".join(cells)} |')
    lines.append('')
    return lines


def rebuild_companies_md(listings=()):
    """Regenerate companies.md from companies.yml and the open listings (best effort)."""
    try:
        import yaml
    except ImportError:
        print('pyyaml not installed, skipping companies.md')
        return
    if not COMPANIES_YML.exists():
        return
    with open(COMPANIES_YML) as f:
        config = yaml.safe_load(f) or {}

    tracked = []
    total = 0
    for platform in sorted(config):
        entries = config[platform] or []
        if not entries:
            continue
        label = PLATFORM_LABELS.get(platform, platform.title())
        tracked.append(f'### {label} ({len(entries)})')
        tracked.append('')
        for entry in sorted(entries, key=lambda e: e['name'].lower()):
            shield = ' 🛡️' if entry.get('security_company') else ''
            tracked.append(f'- {entry["name"]}{shield}')
            total += 1
        tracked.append('')

    lines = [
        '# Tracked Companies',
        '',
        'Employers whose job boards are scraped automatically (see'
        ' [companies.yml](companies.yml)). 🛡️ marks pure-play security'
        ' companies, where every engineering role is a security-industry job.',
        '',
        f'**{total} companies tracked.**',
        '',
        *hiring_now_lines(listings),
        f'## Every tracked board ({total})',
        '',
        *tracked,
    ]
    COMPANIES_MD.write_text('\n'.join(lines))
    print(f'companies.md rebuilt ({total} companies)')


def render_readme(content, listings, today=None):
    """Return content with every generated block rebuilt from listings."""
    today = _today(today)
    for kind in TABLE_TYPES:
        rows = [e for e in listings if e.get('type') == kind]
        open_rows = [e for e in rows if is_open(e)]
        closed_rows = [e for e in rows if not is_open(e)]
        content = replace_table(content, kind, build_table(open_rows, today))
        content = replace_block(content, f'<!-- CLOSED_START {kind} -->',
                                f'<!-- CLOSED_END {kind} -->', closed_block(closed_rows))
    # The markers stay on their own lines: text on the same line as an HTML
    # comment is a raw-HTML block, so **bold** would not render on GitHub.
    content = replace_block(content, '<!-- STATS -->', '<!-- /STATS -->',
                            stats_line(listings, today))
    return replace_block(content, '<!-- LEGEND -->', '<!-- /LEGEND -->',
                         '\n'.join(legend_lines()))


def main():
    if not LISTINGS_FILE.exists():
        print('ERROR: listings.json not found')
        sys.exit(1)
    if not README_FILE.exists():
        print('ERROR: README.md not found')
        sys.exit(1)

    with open(LISTINGS_FILE) as f:
        listings = json.load(f)
    counts = count_open_by_type(listings)
    print(f'Loaded {len(listings)} listings, open: {counts["intern"]} intern, '
          f'{counts["newgrad"]} new grad, {counts["earlycareer"]} early career')

    content = README_FILE.read_text()
    README_FILE.write_text(render_readme(content, listings))
    print('README.md rebuilt successfully')

    rebuild_companies_md(listings)


if __name__ == '__main__':
    main()
