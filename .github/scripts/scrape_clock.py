#!/usr/bin/env python3
"""Start the scrape once a daily slot has passed with no run for it.

GitHub starts a scheduled workflow hours after its cron time: over 30 days
the 11:17 UTC scrape started a median 5.9 hours late and never less than 2.9,
and the 23:17 one a median 3.2. One late event decided when the board
refreshed. scrape-clock.yml ticks every half hour instead, and a tick that
finds no scrape run since the latest slot dispatches one. Every tick is as
late as any other scheduled event, but the ticks arrive spread across the
day, so some tick lands soon after each slot and the scrape no longer waits
on a single event.

A run that GITHUB_TOKEN dispatched raises no workflow_run event when it ends:
the first scrape the clock started, on 2026-11-16, pushed its rows and neither
Pages nor Scraper Health ran after it. So the tick that starts a scrape waits
for it to finish, and every tick dispatches each follower that has not run
since the newest finished scrape. A later tick repairs whatever an earlier one
missed. Stdlib only, like health_check.py, whose client it shares.
"""

import os
import sys
import time
from datetime import UTC, datetime, timedelta

from health_check import GitHub

# Daily scrape times, UTC, as (hour, minute).
SLOTS = ((11, 17), (23, 17))
SCRAPE_WORKFLOW = 'scrape-jobs.yml'
# The workflows that follow a writer through workflow_run, and whether each
# runs after a scrape that did not succeed. A failed scrape pushed nothing for
# Pages to publish, and is what the health check is there to report.
FOLLOWERS = {'pages.yml': False, 'health.yml': True}
# How long the tick that started a scrape waits for it to finish, so Pages
# publishes the new rows at once. The scrape step stops at 25 minutes and the
# run can queue behind another writer first. A scrape that outlasts the wait
# is followed up by a later tick.
FINISH_TRIES = 70
FINISH_DELAY = 30
# How long a tick waits for the run it dispatched to show in the run list. The
# next tick reads that list, so leaving before the run is there could start a
# second scrape for the same slot.
CONFIRM_TRIES = 10
CONFIRM_DELAY = 3


def latest_slot(now):
    """The most recent slot time at or before `now`."""
    candidates = [(now - timedelta(days=back)).replace(hour=hour, minute=minute,
                                                       second=0, microsecond=0)
                  for back in (0, 1) for hour, minute in SLOTS]
    return max(t for t in candidates if t <= now)


def _created(run):
    value = run.get('created_at')
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


def scrape_is_due(now, runs):
    """True when no scrape run, in any state, was created since the latest slot.

    A queued, running, failed or cancelled run counts: the slot got its run,
    and health_check.py reports a failure. So does a run someone dispatched by
    hand after the slot.
    """
    slot = latest_slot(now)
    return not any(t and t >= slot for t in map(_created, runs))


def workflow_runs(gh, workflow):
    """A workflow's most recent runs, newest first, unfiltered since `status=` lags (#44)."""
    data = gh.call('GET', gh.repo_path(f'/actions/workflows/{workflow}/runs?per_page=20'))
    return (data or {}).get('workflow_runs', [])


def scrape_runs(gh):
    """The scrape workflow's most recent runs."""
    return workflow_runs(gh, SCRAPE_WORKFLOW)


def _dispatch(gh, workflow):
    gh.call('POST', gh.repo_path(f'/actions/workflows/{workflow}/dispatches'), {'ref': 'main'})


def _finished(run):
    value = run.get('updated_at')
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


def followers_due(scrapes, follower_runs):
    """The followers that have not run since the newest finished scrape.

    `follower_runs` maps each follower's file name to its recent runs. A
    follower run in any state created after the scrape finished counts, so one
    that workflow_run started for a scrape dispatched by hand is not doubled,
    and neither is one an earlier tick dispatched.
    """
    done = next((r for r in scrapes if r.get('status') == 'completed' and _finished(r)), None)
    if done is None:
        return []
    finished = _finished(done)
    due = []
    for workflow, after_failure in FOLLOWERS.items():
        if done.get('conclusion') != 'success' and not after_failure:
            continue
        if not any(t and t >= finished for t in map(_created, follower_runs.get(workflow, []))):
            due.append(workflow)
    return due


def follow_up(gh):
    """Dispatch each follower that is due and return their file names."""
    due = followers_due(scrape_runs(gh), {w: workflow_runs(gh, w) for w in FOLLOWERS})
    for workflow in due:
        _dispatch(gh, workflow)
    return due


def _slot_run_finished(now, runs):
    slot = latest_slot(now)
    run = next((r for r in runs if _created(r) and _created(r) >= slot), None)
    return run is not None and run.get('status') == 'completed'


def tick(gh, now, wait=time.sleep):
    """Dispatch the scrape if its slot is due, then any follower that is due.

    Returns (outcome, followers): outcome is 'not-due', 'started' or
    'unconfirmed', and followers lists the workflows dispatched.
    """
    outcome = 'not-due'
    if scrape_is_due(now, scrape_runs(gh)):
        _dispatch(gh, SCRAPE_WORKFLOW)
        outcome = 'unconfirmed'
        for _ in range(CONFIRM_TRIES):
            wait(CONFIRM_DELAY)
            if not scrape_is_due(now, scrape_runs(gh)):
                outcome = 'started'
                break
        if outcome == 'started':
            for _ in range(FINISH_TRIES):
                wait(FINISH_DELAY)
                if _slot_run_finished(now, scrape_runs(gh)):
                    break
    return outcome, follow_up(gh)


def main():
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    repo = os.environ.get('GITHUB_REPOSITORY')
    if not token or not repo:
        sys.exit('GITHUB_TOKEN and GITHUB_REPOSITORY are required')
    now = datetime.now(UTC)
    outcome, followers = tick(GitHub(token, repo), now)
    print(f'{now:%Y-%m-%d %H:%M} UTC, slot {latest_slot(now):%Y-%m-%d %H:%M}: {outcome}, '
          f'followers started: {", ".join(followers) or "none"}')
    # The dispatch was accepted but no run appeared. Fail so the tick shows
    # red; the next tick dispatches again if the run never arrives.
    if outcome == 'unconfirmed':
        sys.exit(1)


if __name__ == '__main__':
    main()
