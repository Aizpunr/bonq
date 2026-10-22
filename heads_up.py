"""Remind maintainers when a scheduled Bonq has no submission."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from submissions_poll import api, WorkerError

HERE = Path(__file__).resolve().parent


def expected_date(day, schedule):
    anchor = dt.date.fromisoformat(schedule['anchor'])
    delta = (day - anchor).days
    return delta >= 0 and delta % schedule['every_days'] == 0 and day.isoformat() not in schedule.get('skip_dates', [])


def previous_sunday(today):
    return today - dt.timedelta(days=(today.weekday() + 1) % 7 or 7)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--date', type=dt.date.fromisoformat, help='override today (UTC)')
    args = ap.parse_args()
    sunday = previous_sunday(args.date or dt.datetime.now(dt.timezone.utc).date())
    schedule = json.loads((HERE / 'submit_schedule.json').read_text(encoding='utf-8'))
    if not expected_date(sunday, schedule):
        print(f'{sunday}: no cup scheduled')
        return 0
    for path in (HERE / 'cups').glob('bonq_*.json'):
        if json.loads(path.read_text(encoding='utf-8-sig')).get('date') == sunday.isoformat():
            print(f'{sunday}: cup already present')
            return 0
    ctx = SimpleNamespace(worker=os.environ.get('BONQ_WORKER_URL', '').rstrip('/'), token=os.environ.get('BONQ_POLLER_TOKEN', ''))
    checked = False
    if ctx.worker and ctx.token:
        try:
            pending = api(ctx, 'GET', '/pending').get('submissions', [])
            checked = True
            if any(s.get('date') == sunday.isoformat() and s.get('status') in ('received', 'processed') for s in pending):
                print(f'{sunday}: submission pending or processed')
                return 0
        except WorkerError as exc:
            print(f'Cannot check pending: {exc}')
    else:
        print('Cannot check pending: BONQ_WORKER_URL / BONQ_POLLER_TOKEN missing')
    title = f'Bonq {sunday}: no submission yet'
    body = (f'A cup was scheduled for {sunday}, but nothing was submitted or processed.\n\n'
            'If the cup did not run, add this date to skip_dates in submit_schedule.json.\n')
    if args.dry_run:
        print(f'Would open issue (unless that title already exists, open or closed): {title}\n\n{body}')
        return 0
    if not checked:
        print('Pending check unavailable; no issue created.')
        return 1
    result = subprocess.run(['gh', 'issue', 'list', '--state', 'all', '--search', title + ' in:title', '--json', 'title'],
                            cwd=HERE, capture_output=True, text=True, check=True, timeout=120)
    if any(i['title'] == title for i in json.loads(result.stdout)):
        print('Issue already exists')
        return 0
    folder = Path(tempfile.mkdtemp(prefix='bonq-heads-up-'))
    bodyfile = folder / 'issue.md'
    bodyfile.write_text(body, encoding='utf-8')
    subprocess.run(['gh', 'issue', 'create', '--title', title, '--body-file', str(bodyfile)], cwd=HERE, check=True, timeout=120)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
