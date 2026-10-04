#!/usr/bin/env python3
"""Checks that the repo's pieces stay wired together.

    python .github/scripts/test_wiring.py

Each check guards a promise no other test sees break: a new test script that
CI never runs, a suite that reads the real clock, an action pinned to a tag, a
writer that commits without check_outputs.py, a category the issue form does
not offer, a script AGENTS.md never mentions. Workflows and the issue form are
read as YAML and the test scripts as Python syntax trees, so a comment can
neither satisfy a check nor trip one.
"""
import ast
import re
import sys
from pathlib import Path

import testkit  # noqa: F401

SCRIPTS = Path(__file__).parent
ROOT = SCRIPTS.parent.parent
sys.path.insert(0, str(SCRIPTS))
import classify  # noqa: E402
import yaml  # noqa: E402

WORKFLOWS = {p.name: yaml.safe_load(p.read_text())
             for p in sorted((ROOT / '.github/workflows').glob('*.yml'))}
AGENTS = (ROOT / 'AGENTS.md').read_text()
TESTS = sorted(SCRIPTS.glob('test_*.py'))
LOCAL_MODULES = {p.stem for p in SCRIPTS.glob('*.py')}

failures = 0


def check(name, ok):
    global failures
    if not ok:
        failures += 1
        print(f'FAIL {name}')


def jobs(workflow):
    return (workflow.get('jobs') or {}).items()


def code_lines(step):
    """The shell lines a step runs: comments dropped, continuations joined."""
    lines, pending = [], ''
    for raw in str(step.get('run') or '').splitlines():
        # A '#' at the start or after whitespace opens a shell comment.
        line = re.sub(r'(^|\s)#.*$', '', raw).strip()
        if not line:
            continue
        if line.endswith('\\'):
            pending += line[:-1] + ' '
            continue
        lines.append(pending + line)
        pending = ''
    return lines + ([pending] if pending else [])


# Every test script runs unconditionally in CI on every pull request and push,
# and is named where AGENTS.md lists the suite. PyYAML reads the 'on' key as True.
triggers = WORKFLOWS['tests.yml'].get(True) or WORKFLOWS['tests.yml'].get('on') or {}
check('tests.yml runs on every pull request and every push to main',
      'pull_request' in triggers and 'push' in triggers)
ci_steps = [step for _, job in jobs(WORKFLOWS['tests.yml'])
            if 'if' not in job and not job.get('continue-on-error')
            for step in job.get('steps') or []
            if 'if' not in step and not step.get('continue-on-error')]
ci_runs = {line for step in ci_steps for line in code_lines(step)}
for path in TESTS:
    check(f'tests.yml runs {path.name} in an unconditional step',
          f'python .github/scripts/{path.name}' in ci_runs)
    check(f'AGENTS.md names {path.name}', path.name in AGENTS)

# Every test script imports testkit before any module it tests, and reads no
# clock itself: a test file binds the real datetime classes before testkit
# loads, so its own now() would read the real date.
CLOCK_READS = {'now', 'today', 'utcnow', 'fromtimestamp', 'utcfromtimestamp'}
WALL_CLOCK = {'time', 'time_ns', 'localtime', 'gmtime', 'strftime', 'ctime', 'asctime',
              'mktime'}
for path in TESTS:
    tree = ast.parse(path.read_text())
    imports = [(node.lineno, alias.name.split('.')[0])
               for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names]
    imports += [(node.lineno, (node.module or '').split('.')[0])
                for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    pinned = [node.lineno for node in tree.body if isinstance(node, ast.Import)
              and any(alias.name == 'testkit' for alias in node.names)]
    local = [line for line, name in imports if name in LOCAL_MODULES - {'testkit'}]
    check(f'{path.name} imports testkit at top level before the modules it tests',
          bool(pinned) and all(line > pinned[0] for line in local))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'time':
            for alias in node.names:
                check(f'{path.name}:{node.lineno} imports time.{alias.name}, a clock read',
                      alias.name not in WALL_CLOCK)
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr, owner = node.func.attr, node.func.value
        on_time = isinstance(owner, ast.Name) and owner.id == 'time'
        if attr in CLOCK_READS or (on_time and attr in WALL_CLOCK):
            check(f'{path.name}:{node.lineno} reads the clock with .{attr}(); use testkit.TODAY',
                  False)

# Third-party actions run at a reviewed commit, and installs check hashes.
PINNED = re.compile(r'[\w.-]+/[\w./-]+@[0-9a-f]{40}|docker://[^@\s]+@sha256:[0-9a-f]{64}')
for name, workflow in WORKFLOWS.items():
    for job_name, job in jobs(workflow):
        refs = [job['uses']] if 'uses' in job else []
        refs += [step['uses'] for step in job.get('steps') or [] if 'uses' in step]
        for ref in refs:
            check(f'{name}: {job_name} uses {ref}, which is not pinned to a commit SHA',
                  ref.startswith('./') or PINNED.fullmatch(ref) is not None)
        for step in job.get('steps') or []:
            for line in code_lines(step):
                if re.search(r'\bpip3? install\b', line):
                    check(f'{name}: {line!r} uses --require-hashes', '--require-hashes' in line)

# A writer is any job that can push. It queues in readme-updates and runs
# check_outputs.py, unswallowed, before every commit, so no run pushes a
# charter-breaking row or a mass close.
GIT = r'\bgit\b(?:\s+-c\s+\S+)*\s+'
for name, workflow in WORKFLOWS.items():
    for job_name, job in jobs(workflow):
        permissions = job.get('permissions', workflow.get('permissions')) or {}
        steps = job.get('steps') or []
        lines = [line for step in steps for line in code_lines(step)]
        writes = (permissions == 'write-all'
                  or isinstance(permissions, dict) and permissions.get('contents') == 'write'
                  or any(re.search(GIT + 'push', line) for line in lines))
        if not writes:
            continue
        concurrency = job.get('concurrency') or workflow.get('concurrency') or {}
        group = concurrency.get('group') if isinstance(concurrency, dict) else concurrency
        check(f'{name}: writer job {job_name} queues in readme-updates', group == 'readme-updates')
        checked = False
        for step in steps:
            for line in code_lines(step):
                if 'python .github/scripts/check_outputs.py' in line:
                    check(f'{name}: {line!r} lets check_outputs.py fail the step',
                          '||' not in line and not step.get('continue-on-error'))
                    checked = True
                if re.search(GIT + 'commit', line):
                    check(f'{name}: {job_name} runs check_outputs.py before {line!r}', checked)
                    checked = False

# The issue form offers exactly the categories the classifier can give, and
# CONTRIBUTING.md names each one.
categories = set(classify.CATEGORY_NAMES) | set(classify.FALLBACK_CATEGORIES)
form = yaml.safe_load((ROOT / '.github/ISSUE_TEMPLATE/add-job.yml').read_text())
offered = {str(option) for field in form.get('body') or []
           if (field.get('attributes') or {}).get('label') == 'Category'
           for option in field['attributes'].get('options') or []}
check(f'issue form categories match the classifier: '
      f'missing {sorted(categories - offered)}, extra {sorted(offered - categories - {"Not sure"})}',
      offered == categories | {'Not sure'})
contributing = (ROOT / 'CONTRIBUTING.md').read_text()
for name in sorted(categories):
    check(f'CONTRIBUTING.md names the category {name!r}', name in contributing)

# Every script is named in AGENTS.md, a workflow or a skill, so none goes
# unexplained.
skills = ' '.join(p.read_text() for p in (ROOT / '.claude/skills').rglob('*.md'))
workflow_text = ' '.join(p.read_text() for p in (ROOT / '.github/workflows').glob('*.yml'))
mentioned = AGENTS + workflow_text + skills
for path in sorted(SCRIPTS.glob('*.py')):
    check(f'{path.name} is named in AGENTS.md, a workflow or a skill', path.name in mentioned)

if failures:
    print(f'\n{failures} wiring check(s) failed')
    sys.exit(1)
print('All wiring checks passed')
