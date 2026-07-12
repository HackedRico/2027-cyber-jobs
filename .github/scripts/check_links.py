#!/usr/bin/env python3
"""Mark dead application links as closed (🔒) in README.md and listings.json."""

import json
import re
import time
from pathlib import Path

import requests

# Domains that block bots with 403/404 even for live jobs.
SKIP_DOMAINS = [
    'careers.ibm.com',
    'lockheedmartinjobs.com',
    'usajobs.gov',
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/126.0.0.0 Safari/537.36'
    )
}

APPLY_BTN_PATTERN = re.compile(
    r'<a href="([^"]+)" target="_blank" rel="noopener noreferrer">'
    r'<img src="https://img\.shields\.io/badge/Apply[^"]*" alt="Apply"></a>'
)


def should_skip(url):
    return any(domain in url for domain in SKIP_DOMAINS)


def is_link_alive(url):
    try:
        resp = requests.get(url, timeout=12, allow_redirects=True, headers=HEADERS)
        return resp.status_code < 404
    except requests.RequestException as e:
        print(f'  Request error: {e}')
        return True  # network flake — don't mark closed on ambiguity


def main():
    with open('README.md') as f:
        content = f.read()

    matches = list(APPLY_BTN_PATTERN.finditer(content))
    print(f'Found {len(matches)} links to check')

    dead = []
    for match in matches:
        url = match.group(1)
        if should_skip(url):
            print(f'  SKIP (bot-blocked domain): {url}')
            continue
        alive = is_link_alive(url)
        print(f'  {"OK  " if alive else "DEAD"}: {url}')
        if not alive:
            dead.append((url, match.group(0)))
        time.sleep(0.75)

    if not dead:
        print('\nAll checked links are active')
        return

    dead_urls = {url for url, _ in dead}
    for url, btn in dead:
        content = content.replace(btn, '🔒')
    with open('README.md', 'w') as f:
        f.write(content)

    listings_file = Path('listings.json')
    if listings_file.exists():
        with open(listings_file) as f:
            listings = json.load(f)
        for entry in listings:
            if entry.get('url', '') in dead_urls:
                entry['url'] = ''
        tmp = listings_file.with_suffix('.tmp')
        with open(tmp, 'w') as f:
            json.dump(listings, f, indent=2)
        tmp.replace(listings_file)

    print(f'\nMarked {len(dead)} dead link(s) as 🔒')


if __name__ == '__main__':
    main()
