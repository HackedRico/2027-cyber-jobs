#!/usr/bin/env python3
"""Rebuild the README job tables (and companies.md) from listings.json."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

LISTINGS_FILE = Path('listings.json')
README_FILE = Path('README.md')
COMPANIES_YML = Path('companies.yml')
COMPANIES_MD = Path('companies.md')

APPLY_BADGE = 'https://img.shields.io/badge/Apply-2ea043?style=flat-square'


def _company_sort_key(name):
    name = re.sub(r'[\U0001F000-\U0001FFFF☀-⛿✀-➿]', '', name)
    return name.strip().lower()


def format_company(entry):
    name = entry['company'].strip()
    if entry.get('clearance'):
        name += ' 🇺🇸'
    return name


def format_location(location):
    location = location.strip()
    if ';' not in location:
        return location
    parts = [p.strip() for p in location.split(';') if p.strip()]
    if len(parts) <= 1:
        return parts[0] if parts else location
    inner = '</br>'.join(parts)
    return f'<details><summary>**{len(parts)} locations**</summary>{inner}</details>'


def format_date(date_added):
    try:
        dt = datetime.strptime(date_added, '%Y-%m-%d')
        return dt.strftime('%b %-d')
    except Exception:
        return date_added


def apply_btn(url):
    if not url:
        return '🔒'
    return (f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
            f'<img src="{APPLY_BADGE}" alt="Apply"></a>')


def format_row(entry, company_col):
    role = entry['role'].strip()
    location = format_location(entry.get('location', ''))
    category = entry.get('category', 'Security Engineering')
    btn = apply_btn(entry.get('url', '').strip())
    date = format_date(entry.get('date_added', ''))
    return f'| {company_col} | {role} | {location} | {category} | {btn} | {date} |'


def build_table(entries):
    """Sort by date desc then company, with ↳ grouping for repeat companies."""
    def sort_key(e):
        try:
            dt = datetime.strptime(e.get('date_added', ''), '%Y-%m-%d')
        except Exception:
            dt = datetime.min
        return (-dt.timestamp(), _company_sort_key(e['company']))

    rows = []
    group_tracker = set()
    for entry in sorted(entries, key=sort_key):
        group_key = (_company_sort_key(entry['company']), entry.get('date_added', ''))
        if group_key in group_tracker:
            company_col = '↳'
        else:
            company_col = format_company(entry)
            group_tracker.add(group_key)
        rows.append(format_row(entry, company_col))
    return rows


def replace_table(content, marker, rows):
    start_marker = f'<!-- TABLE_START {marker} -->'
    end_marker = f'<!-- TABLE_END {marker} -->'
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        print(f'ERROR: Could not find markers for table: {marker}')
        sys.exit(1)

    after_start = content[start_idx:]
    sep_match = re.search(r'\| [-| :]+\|\n', after_start)
    if not sep_match:
        print(f'ERROR: Could not find separator row for table: {marker}')
        sys.exit(1)

    header_end = start_idx + sep_match.end()
    header = content[start_idx:header_end]
    footer = content[end_idx:]
    body = '\n'.join(rows) + '\n' if rows else ''
    return content[:start_idx] + header + body + footer


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


def rebuild_companies_md():
    """Regenerate companies.md from companies.yml (best effort)."""
    try:
        import yaml
    except ImportError:
        print('pyyaml not installed — skipping companies.md')
        return
    if not COMPANIES_YML.exists():
        return
    with open(COMPANIES_YML) as f:
        config = yaml.safe_load(f) or {}

    lines = [
        '# Tracked Companies',
        '',
        'Employers whose job boards are scraped automatically (see'
        ' [companies.yml](companies.yml)). 🛡️ marks pure-play security'
        ' companies, where every engineering role is a security-industry job.',
        '',
    ]
    total = 0
    for platform in sorted(config):
        entries = config[platform] or []
        if not entries:
            continue
        label = PLATFORM_LABELS.get(platform, platform.title())
        lines.append(f'## {label} ({len(entries)})')
        lines.append('')
        for entry in sorted(entries, key=lambda e: e['name'].lower()):
            shield = ' 🛡️' if entry.get('security_company') else ''
            lines.append(f'- {entry["name"]}{shield}')
            total += 1
        lines.append('')
    lines.insert(4, f'**{total} companies tracked.**')
    lines.insert(5, '')
    COMPANIES_MD.write_text('\n'.join(lines))
    print(f'companies.md rebuilt ({total} companies)')


def main():
    if not LISTINGS_FILE.exists():
        print('ERROR: listings.json not found')
        sys.exit(1)
    if not README_FILE.exists():
        print('ERROR: README.md not found')
        sys.exit(1)

    with open(LISTINGS_FILE) as f:
        listings = json.load(f)

    newgrad = [e for e in listings if e.get('type') == 'newgrad']
    earlycareer = [e for e in listings if e.get('type') == 'earlycareer']
    print(f'Loaded {len(listings)} listings: '
          f'{len(newgrad)} new grad, {len(earlycareer)} early career')

    with open(README_FILE) as f:
        content = f.read()

    content = replace_table(content, 'newgrad', build_table(newgrad))
    content = replace_table(content, 'earlycareer', build_table(earlycareer))
    # The markers stay on their own lines: text on the same line as an HTML
    # comment is a raw-HTML block, so **bold** would not render on GitHub.
    content = re.sub(
        r'(<!-- STATS -->).*?(<!-- /STATS -->)',
        f'\\g<1>\n\n**{len(listings)}** open roles tracked · updated '
        f'{datetime.now().strftime("%B %-d, %Y")}\n\n\\g<2>',
        content,
        flags=re.DOTALL,
    )

    with open(README_FILE, 'w') as f:
        f.write(content)
    print('README.md rebuilt successfully')

    rebuild_companies_md()


if __name__ == '__main__':
    main()
