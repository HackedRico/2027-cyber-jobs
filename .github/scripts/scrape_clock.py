#!/usr/bin/env python3
"""Start the scrape once a daily slot has passed with no run for it.

GitHub starts a scheduled workflow hours after its cron time: over 30 days
the 11:17 UTC scrape started a median 5.9 hours late and never less than 2.9,
and the 23:17 one a median 3.2. One late event decided when the board
refreshed. scrape-clock.yml ticks every half hour instead, and a tick that
finds no scrape run since the latest slot dispatches one. Every tick is as
late as any other scheduled event, but the ticks arrive spread across the
day, so some tick lands soon after each slot and the scrape no longer waits
on a single event. Stdlib only, like health_check.py, whose client it shares.
"""

import os
import sys
import time
from datetime import UTC, datetime, timedelta

from health_check import GitHub

# Daily scrape times, UTC, as (hour, minute).
SLOTS = ((11, 17), (23, 17))
SCRAPE_WORKFLOW = 'scrape-jobs.yml'
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


def scrape_runs(gh):
    """The scrape workflow's most recent runs, unfiltered, since `status=` lags (#44)."""
    data = gh.call('GET', gh.repo_path(f'/actions/workflows/{SCRAPE_WORKFLOW}/runs?per_page=20'))
    return (data or {}).get('workflow_runs', [])


def tick(gh, now, wait=time.sleep):
    """Dispatch the scrape if its slot is due. Returns 'not-due', 'started' or 'unconfirmed'."""
    if not scrape_is_due(now, scrape_runs(gh)):
        return 'not-due'
    gh.call('POST', gh.repo_path(f'/actions/workflows/{SCRAPE_WORKFLOW}/dispatches'),
            {'ref': 'main'})
    for _ in range(CONFIRM_TRIES):
        wait(CONFIRM_DELAY)
        if not scrape_is_due(now, scrape_runs(gh)):
            return 'started'
    return 'unconfirmed'


def main():
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    repo = os.environ.get('GITHUB_REPOSITORY')
    if not token or not repo:
        sys.exit('GITHUB_TOKEN and GITHUB_REPOSITORY are required')
    now = datetime.now(UTC)
    outcome = tick(GitHub(token, repo), now)
    print(f'{now:%Y-%m-%d %H:%M} UTC, slot {latest_slot(now):%Y-%m-%d %H:%M}: {outcome}')
    # The dispatch was accepted but no run appeared. Fail so the tick shows
    # red; the next tick dispatches again if the run never arrives.
    if outcome == 'unconfirmed':
        sys.exit(1)


if __name__ == '__main__':
    main()
