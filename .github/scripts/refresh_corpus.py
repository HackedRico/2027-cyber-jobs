#!/usr/bin/env python3
"""Rebuild the title list in fixtures/title_corpus.tsv from a live scrape.

    python .github/scripts/refresh_corpus.py             # scrapes every board
    python .github/scripts/refresh_corpus.py dump.json   # a saved list of postings

The scrape takes as long as a full dry run. A saved dump is a JSON list of
{"company", "title", "security_company"} objects. Every row of listings.json
is added either way, then test_corpus.py --rebuild picks the titles and writes
their verdicts in a fresh process, under the pinned clock.

Commit a refresh on its own, never inside a rule change: the rule change's
corpus diff should show moved verdicts only, not new titles.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import scrape_jobs as sj  # noqa: E402


def scrape(config):
    postings, broken = [], []
    for result in sj.scrape_boards(sj.build_tasks(config)):
        print(f'Checking {result["label"]}... {result["status"]} ({result["count"]} postings)')
        if result['status'] in ('FAILED', 'CRASHED'):
            broken.append(result['label'])
        postings += [{'company': j.get('company', ''), 'title': j.get('title', ''),
                      'security_company': result['security_company']}
                     for j in result['jobs']]
    if broken:
        # Their titles are missing from this refresh; rerun once they recover.
        print(f'\nWARNING: {len(broken)} board(s) returned nothing: {", ".join(broken)}')
    return postings


def main():
    config = sj.yaml.safe_load((ROOT / 'companies.yml').read_text())
    postings = (json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) > 1
                else scrape(config))
    flags = sj.security_company_flags(config)
    stored = [{'company': r.get('company', ''), 'title': r.get('role', ''),
               'security_company': flags.get(r.get('company', ''), False), 'stored': True}
              for r in json.loads((ROOT / 'listings.json').read_text())]
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(postings + stored, f)
    test_corpus = str(HERE / 'test_corpus.py')
    try:
        subprocess.run([sys.executable, test_corpus, '--rebuild', f.name], check=True)
    finally:
        Path(f.name).unlink()
    sys.exit(subprocess.run([sys.executable, test_corpus], check=False).returncode)


if __name__ == '__main__':
    main()
