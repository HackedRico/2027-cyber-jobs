#!/usr/bin/env python3
"""Checks that the repo's pieces stay wired together.

    python .github/scripts/test_wiring.py

Each check guards a promise no other test sees break: a new test script that
CI never runs, a suite that reads the real clock, an action pinned to a tag, a
writer that commits without check_outputs.py, a category the issue form does
not offer, a script AGENTS.md never mentions.
"""
import re
import sys
from pathlib import Path

import testkit  # noqa: F401

SCRIPTS = Path(__file__).parent
ROOT = SCRIPTS.parent.parent
sys.path.insert(0, str(SCRIPTS))
import classify  # noqa: E402

WORKFLOWS = {p.name: p.read_text() for p in sorted((ROOT / '.github/workflows').glob('*.yml'))}
AGENTS = (ROOT / 'AGENTS.md').read_text()
TESTS = sorted(p.name for p in SCRIPTS.glob('test_*.py'))

failures = 0


def check(name, ok):
    global failures
    if not ok:
        failures += 1
        print(f'FAIL {name}')


# Every test script runs in CI, is named where AGENTS.md lists the suite, and
# pins the clock before it imports what it tests.
for name in TESTS:
    check(f'tests.yml runs {name}', f'python .github/scripts/{name}' in WORKFLOWS['tests.yml'])
    check(f'AGENTS.md names {name}', name in AGENTS)
    source = (SCRIPTS / name).read_text()
    first_local = source.find('sys.path.insert')
    check(f'{name} imports testkit before the modules it tests',
          'import testkit' in source and source.find('import testkit') < first_local)

# Third-party actions run at a reviewed commit, and installs check hashes.
for name, text in WORKFLOWS.items():
    for ref in re.findall(r'uses:\s*([^\s#]+)', text):
        check(f'{name}: {ref} is pinned to a full commit SHA',
              ref.startswith('./') or re.fullmatch(r'[^@]+@[0-9a-f]{40}', ref) is not None)
    for line in text.splitlines():
        if 'pip install' in line and not line.lstrip().startswith('#'):
            check(f'{name}: {line.strip()!r} uses --require-hashes', '--require-hashes' in line)

# A writer shares the readme-updates queue and checks its output before every
# commit, so no run can push a charter-breaking row or a mass close.
for name, text in WORKFLOWS.items():
    if 'group: readme-updates' not in text:
        continue
    commits = [m.start() for m in re.finditer(r'git commit', text)]
    checks = [m.start() for m in re.finditer(r'check_outputs\.py', text)]
    check(f'{name} commits', bool(commits))
    for at in commits:
        check(f'{name}: check_outputs.py runs before the commit at offset {at}',
              any(c < at for c in checks))

# The issue form offers exactly the categories the classifier can give, and
# CONTRIBUTING.md names each one.
categories = set(classify.CATEGORY_NAMES) | set(classify.FALLBACK_CATEGORIES)
form = (ROOT / '.github/ISSUE_TEMPLATE/add-job.yml').read_text()
block = form[form.index('label: Category'):]
block = block[block.index('options:'):block.index('validations:')]
offered = set(re.findall(r'^\s*-\s*(.+?)\s*$', block, re.M))
check(f'issue form categories match the classifier: '
      f'missing {sorted(categories - offered)}, extra {sorted(offered - categories - {"Not sure"})}',
      offered == categories | {'Not sure'})
contributing = (ROOT / 'CONTRIBUTING.md').read_text()
for name in sorted(categories):
    check(f'CONTRIBUTING.md names the category {name!r}', name in contributing)

# Every script is named in AGENTS.md, a workflow or a skill, so none goes
# unexplained.
skills = ' '.join(p.read_text() for p in (ROOT / '.claude/skills').rglob('*.md'))
mentioned = AGENTS + ' '.join(WORKFLOWS.values()) + skills
for path in sorted(SCRIPTS.glob('*.py')):
    check(f'{path.name} is named in AGENTS.md, a workflow or a skill', path.name in mentioned)

if failures:
    print(f'\n{failures} wiring check(s) failed')
    sys.exit(1)
print('All wiring checks passed')
