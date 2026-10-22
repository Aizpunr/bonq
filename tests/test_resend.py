"""A file sent for an already-published cup is kept and compared with the published result."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import submissions_poll as SP

REPO = Path(__file__).resolve().parent.parent
HOST1 = REPO / 'host' / 'bonq_1_topoutresults.json'
PUB1 = json.loads((REPO / 'cups' / 'bonq_1.json').read_text(encoding='utf-8'))
MAPPERS = ['76561199380118922', '76561199733601754', '76561198348911001']   # Victor, LKat, Kreeeeee


def ctx(tmp_path):
    return SimpleNamespace(python=sys.executable, repo=str(REPO), workdir=str(tmp_path), no_git=True)


def sub(**kw):
    base = {'id': 'test-1', 'cup': 1, 'date': '2026-09-27', 'nuisance': MAPPERS}
    base.update(kw)
    return base


def test_completed_is_an_exact_line():
    assert SP.completed('x\nBONQ 2 COMPLETE\n', 2)
    assert not SP.completed('BONQ 12 COMPLETE\n', 2)
    assert not SP.completed('not BONQ 2 COMPLETE yet\n', 2)


def test_resend_of_bonq_1_only_differs_at_the_presence_boundary(tmp_path):
    # Bonq #1 was published from the log. Read from the host file alone, Minkus has 6
    # rounds present (at round close) instead of 5 and joins Others. Nothing else differs.
    note = SP.compare_with_published(ctx(tmp_path), sub(), (HOST1, None), PUB1)
    assert note == 'REVIEW: DIFFERS from the published Bonq #1: 29 players (published 28)'


def test_resend_that_agrees_says_so(tmp_path):
    pub = dict(PUB1, others=PUB1['others'] + [{'sid': '76561198212710700', 'name': 'Minkus'}])
    note = SP.compare_with_published(ctx(tmp_path), sub(), (HOST1, None), pub)
    assert note == 'matches the published Bonq #1'


def test_resend_with_other_settings_is_flagged(tmp_path):
    note = SP.compare_with_published(ctx(tmp_path), sub(nuisance=[], date='2026-10-11'),
                                     (HOST1, None), PUB1)
    assert note.startswith('REVIEW: DIFFERS from the published Bonq #1: ')
    assert 'nuisance none (published Kreeeeee, Victor, LKat)' in note
    assert 'date 2026-10-11 (published 2026-09-27)' in note


def test_unparseable_resend_is_flagged(tmp_path):
    bad = tmp_path / 'bad.json'
    bad.write_text('{"settings": {}, "rounds": []}', encoding='utf-8')
    note = SP.compare_with_published(ctx(tmp_path), sub(), (bad, None), PUB1)
    assert note.startswith('REVIEW: resend of published Bonq #1 could not be parsed')
