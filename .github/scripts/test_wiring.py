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
import datetime
import re
import socket
import sys
from pathlib import Path

import testkit

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


def fails_on_error(workflow, job, step):
    """True when the step always runs and any failing line fails it.

    GitHub runs a step with no shell as `bash -e {0}`, and `shell: bash` adds
    pipefail. `shell: bash {0}`, a `set +e` or continue-on-error run on past a
    failure, and an if: can skip the step.
    """
    shell = (step.get('shell') or ((job.get('defaults') or {}).get('run') or {}).get('shell')
             or ((workflow.get('defaults') or {}).get('run') or {}).get('shell'))
    return ('if' not in step and not step.get('continue-on-error')
            and shell in (None, 'bash')
            and not any(re.search(r'\bset\s+\+(?:\w*e|o\s+errexit)', line)
                        for line in code_lines(step)))


# Every test script runs unconditionally in CI on every pull request and push,
# and is named where AGENTS.md lists the suite. PyYAML reads the 'on' key as True.
# A paths or branches filter, or a step that runs more than its suite, kept
# this green while CI skipped a suite or passed one that failed.
tests_yml = WORKFLOWS['tests.yml']
triggers = tests_yml.get(True) or tests_yml.get('on') or {}
pull_request, push = triggers.get('pull_request') or {}, triggers.get('push') or {}
check('tests.yml runs on every pull request and every push to main',
      'pull_request' in triggers and 'push' in triggers
      and not {'paths', 'paths-ignore', 'branches', 'branches-ignore', 'types'} & set(pull_request)
      and not {'paths', 'paths-ignore'} & set(push)
      and 'main' in (push.get('branches') or []))
ci_runs = {code_lines(step)[0] for _, job in jobs(tests_yml)
           if 'if' not in job and not job.get('continue-on-error')
           for step in job.get('steps') or []
           if fails_on_error(tests_yml, job, step) and len(code_lines(step)) == 1}
for path in TESTS:
    check(f'tests.yml runs {path.name} alone in an unconditional step that fails with it',
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
    # `import time as clock` then clock.time() read the real date unflagged.
    time_names = {alias.asname or 'time' for node in ast.walk(tree)
                  if isinstance(node, ast.Import) for alias in node.names
                  if alias.name == 'time'} | {'time'}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'time':
            for alias in node.names:
                check(f'{path.name}:{node.lineno} imports time.{alias.name}, a clock read',
                      alias.name not in WALL_CLOCK)
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr, owner = node.func.attr, node.func.value
        on_time = isinstance(owner, ast.Name) and owner.id in time_names
        if attr in CLOCK_READS or (on_time and attr in WALL_CLOCK):
            check(f'{path.name}:{node.lineno} reads the clock with .{attr}(); use testkit.TODAY',
                  False)


# testkit does pin the clock and cut the network. With its two pin lines
# deleted every suite stayed green until the 2026 cohort titles failed on
# 1 Jan 2027. Checked by identity, since a test file reads no clock itself.
def refused(call):
    try:
        call()
    except testkit.NetworkAccess:
        return True
    except OSError:
        pass
    return False


check('testkit pins the date and datetime classes classify.py reads',
      datetime.date is testkit._PinnedDate and datetime.datetime is testkit._PinnedDatetime
      and classify.datetime is testkit._PinnedDatetime)
# TEST-NET-1 and .invalid reach nothing even when the cut is gone.
check('testkit refuses a connection past loopback',
      refused(lambda: socket.create_connection(('192.0.2.1', 443), timeout=1).close()))
check('testkit refuses a DNS lookup',
      refused(lambda: socket.getaddrinfo('example.invalid', 443)))

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

# A writer is any job that can push. It queues in readme-updates and, in the
# step that commits, runs check_outputs.py unswallowed before every commit, so
# no run pushes a charter-breaking row or a mass close. The line stands alone
# in its step. `&& echo`, `; true`, `| tee`, an if: on the step, a
# `shell: bash {0}`, or the check in a step of its own ahead of a commit step
# under `if: always()` each let the commit run after a failed check, and this
# check passed them all.
GIT = r'\bgit\b(?:\s+-c\s+\S+)*\s+'
CHECK_OUTPUTS = re.compile(r'python \.github/scripts/check_outputs\.py'
                           r'(?: --baseline "\$RUNNER_TEMP/listings\.base\.json")?')
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
        for step in steps:
            checked = False
            for line in code_lines(step):
                if 'check_outputs.py' in line:
                    check(f'{name}: {line!r} lets check_outputs.py fail the step',
                          CHECK_OUTPUTS.fullmatch(line) is not None
                          and fails_on_error(workflow, job, step))
                    checked = True
                if re.search(GIT + 'commit', line):
                    check(f'{name}: {job_name} runs check_outputs.py in the step before {line!r}',
                          checked)
                    checked = False

# A workflow that follows the writers through workflow_run also runs on a
# schedule. GitHub's 5 Oct 2026 Actions incident left two such runs unassigned
# to any runner ('The job was not acquired by Runner of type hosted even after
# multiple attempts'), and Pages served the older build until the next writer.
for name, workflow in WORKFLOWS.items():
    on = workflow.get(True) or workflow.get('on') or {}
    if isinstance(on, dict) and 'workflow_run' in on:
        check(f'{name} follows the writers through workflow_run, so it needs a schedule '
              'that catches up a run lost to a runner outage', 'schedule' in on)

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
