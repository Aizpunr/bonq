import json
import shutil
import subprocess
from pathlib import Path

import pytest
import parse_bonq_from_host as H
import parse_bonq_from_log as P

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_parity():
    run = subprocess.run([shutil.which("node"), "--preserve-symlinks", "--preserve-symlinks-main", "tests/js/run_bonq_parser.js"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert run.returncode == 0, run.stderr
    js = json.loads(run.stdout)
    host = H.load_host(ROOT / "host/bonq_1_topoutresults.json")
    rounds, roster, _ = H.host_rounds(host)
    roster = {s: P.strip_tag(n) for s, n in roster.items()}
    nuisance = {str(p["steamID"]) for r in host["rounds"] for p in r["playerResults"] if p.get("nuisance")}
    result = H.mod_standings(host, rounds, roster, nuisance, 6)
    keys = [("sid", "name", "won_scoring_round", "qualified_scoring_round", "map", "time"),
            ("sid", "name", "qualified_scoring_round"), ("sid", "name", "points", "finishes"),
            ("sid", "name", "points")]
    for field, expected, fields in zip(("winners", "finalists", "others", "nuisance"), result, keys):
        assert [tuple(p[k] for k in fields) for p in js[field]] == [tuple(p[k] for k in fields) for p in expected]
    assert {p["sid"] for p in js["excluded"]} == {p["sid"] for p in result[4]}
