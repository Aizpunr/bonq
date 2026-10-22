import datetime as dt
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    import sys
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


poller = module('submissions_poll')
heads = module('heads_up')


def submission(**overrides):
    return dict({'id': '20261025T1800-12345678', 'cup': 3, 'date': '2026-10-25', 'nuisance': [], 'header': '', 'submitter': ''}, **overrides)


def test_pr_body():
    report = {'header': 'Cup three', 'date': '2026-10-25', 'winners': ['Beans', 'Second'],
              'finalists': ['Third'], 'nuisance': ['Host'], 'players': 22, 'rounds': 12,
              'warnings': ['Incomplete'], 'cross_check': ['flags differ'],
              'renamed': [{'sid': '76561198842622746', 'seen': 'Old', 'as': 'New'}],
              'unknown': [{'sid': '76561198842622747', 'seen': 'Unknown'}]}
    body = poller.pr_body(submission(submitter='aizpun'), report, 'mismatch <test>')
    for text in ['Cup three', 'Beans, Second', 'Third', 'Host', '22', '12', 'Incomplete',
                 'flags differ', 'Old -> New', '76561198842622747', 'Unknown', '<details>',
                 'mismatch &lt;test&gt;', 'aizpun', '20261025T1800-12345678']:
        assert text in body
    assert body.endswith('Generated with [Claude Code](https://claude.com/claude-code)')
    assert 'agree' in poller.pr_body(submission(), {})


def test_best_submission():
    subs = [{'id': 'a', 'received_at': '2026-10-25T18:00:00Z'},
            {'id': 'b', 'received_at': '2026-10-25T19:00:00Z'}]
    assert poller.best_submission(subs, {'a': (10, 200), 'b': (9, 900)})['id'] == 'a'
    assert poller.best_submission(subs, {'a': (10, 200), 'b': (10, 201)})['id'] == 'b'
    assert poller.best_submission(subs, {'a': (10, 200), 'b': (10, 200)})['id'] == 'b'


@pytest.mark.parametrize('override', [dict(header='--help'), dict(header='x\ny'), dict(submitter='a\0b'),
    dict(header='a\rb'), dict(cup=True), dict(cup=1), dict(cup=1000), dict(date='2026-02-30'),
    dict(nuisance=['123']), dict(nuisance=[76561198842622746]), dict(nuisance='bad'),
    dict(nuisance=['76561198842622746'] * 21), dict(id='../outside'), dict(header='x' * 161)])
def test_unsafe_args(override):
    assert poller.unsafe_args(submission(**override))


def test_safe_args():
    assert poller.unsafe_args(submission()) is None
    assert poller.unsafe_args(submission(nuisance=['76561198842622746'])) is None


def test_schedule():
    schedule = {'anchor': '2026-09-27', 'every_days': 14, 'skip_dates': []}
    assert heads.expected_date(dt.date(2026, 9, 27), schedule)
    assert heads.expected_date(dt.date(2026, 10, 25), schedule)
    assert not heads.expected_date(dt.date(2026, 10, 18), schedule)
    assert not heads.expected_date(dt.date(2026, 9, 13), schedule)
    schedule['skip_dates'] = ['2026-10-25']
    assert not heads.expected_date(dt.date(2026, 10, 25), schedule)
    assert heads.previous_sunday(dt.date(2026, 10, 26)) == dt.date(2026, 10, 25)
    assert heads.previous_sunday(dt.date(2026, 10, 25)) == dt.date(2026, 10, 18)


def test_ingest_empty_nuisance_and_nonfatal_validation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import json
    commands, statuses = [], []
    ctx = SimpleNamespace(python='python', repo=str(tmp_path), workdir=str(tmp_path),
                          dry_run=False, ci=False, toast=False)

    def run(argv, **kwargs):
        commands.append(argv)
        if 'parse_bonq_from_host.py' in argv:
            (tmp_path / 'report.json').write_text(json.dumps({'players': 2}), encoding='utf-8')
            return SimpleNamespace(returncode=0, stdout='BONQ 3 COMPLETE', stderr='')
        if 'validate_vs_topout.py' in argv:
            return SimpleNamespace(returncode=1, stdout='mismatch', stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(poller.subprocess, 'run', run)
    monkeypatch.setattr(poller, 'post_status', lambda ctx, sid, status, note='': statuses.append(status))
    poller.run_cup(ctx, submission(), (tmp_path / 'host.json', tmp_path / 'log.txt'))
    argv = commands[0]
    assert argv[argv.index('--nuisance') + 1] == ''
    assert '--log' in argv
    assert len(commands) == 3
    assert statuses == ['processed']


def test_host_score_preserves_large_ids(tmp_path):
    path = tmp_path / 'host.json'
    path.write_text('\ufeff{"rounds":[{"playerResults":[{"steamID":76561198842622746}]}]}', encoding='utf-8')
    assert poller.host_score(path) == (1, 1)


def test_download_rejects_bad_sha(tmp_path, monkeypatch):
    monkeypatch.setattr(poller, 'api', lambda *a, **kw: (b'wrong bytes', {}))
    with pytest.raises(poller.WorkerError, match='SHA-256 mismatch'):
        poller.download(None, submission(sha='0' * 64), tmp_path)
    assert not list(tmp_path.iterdir())
