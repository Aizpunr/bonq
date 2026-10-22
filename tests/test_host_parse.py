import json
from pathlib import Path

import parse_bonq_from_host as H
import parse_bonq_from_log as P

ROOT = Path(__file__).resolve().parents[1]


def test_host_acceptance():
    host = H.load_host(ROOT / "host/bonq_1_topoutresults.json")
    golden = json.loads((ROOT / "cups/bonq_1.json").read_text(encoding="utf-8"))
    rounds, roster, names = H.host_rounds(host)
    if Path(P.PLAYERS_MASTER).exists():
        P.resolve_roster(roster, names, P.load_registry())
    else:
        roster = {s: P.strip_tag(n) for s, n in roster.items()}
    nuisance = {str(p["steamID"]) for r in host["rounds"] for p in r["playerResults"] if p.get("nuisance")}
    winners, finalists, others, nuis, excluded = H.mod_standings(host, rounds, roster, nuisance, 6)
    def project(rows, keys):
        return [tuple(p[k] for k in keys) for p in rows]
    for field, actual, keys in [
        ("winners", winners, ("sid", "won_scoring_round", "qualified_scoring_round", "map", "time")),
        ("finalists", finalists, ("sid", "qualified_scoring_round")),
        ("nuisance", nuis, ("sid", "points", "finishes"))]:
        assert project(actual, keys) == project(golden[field], keys)
    minkus = next(s for s, seen in names.items() if any(P.strip_tag(n) == "Minkus" for n in seen))
    assert minkus == "76561198212710700"
    assert [p["sid"] for p in others if p["sid"] not in {x["sid"] for x in golden["others"]}] == [minkus]
    assert others[-1]["sid"] == minkus
    assert others[-1]["rounds_played"] == 6
    log_minkus = next(p for p in golden["excluded"] if p["sid"] == minkus)
    assert log_minkus["rounds_played"] == 5
    assert project(others[:-1], ("sid", "points", "finishes")) == project(golden["others"], ("sid", "points", "finishes"))
    gwolf = next(s for s, seen in names.items() if any(P.strip_tag(n) == "GWolf" for n in seen))
    assert gwolf in {p["sid"] for p in excluded}
    assert {p["sid"] for p in excluded} == ({p["sid"] for p in golden["excluded"]} - {minkus}) | {gwolf}
    assert gwolf not in {p["sid"] for field in ("winners", "finalists", "others", "nuisance", "excluded") for p in golden[field]}
    by_sid = {p["sid"]: p for group in (others, nuis, excluded) for p in group}
    for group in ("others", "nuisance", "excluded"):
        for p in golden[group]:
            assert abs(by_sid[p["sid"]]["rounds_played"] - p["rounds_played"]) <= 1
    actual_rounds = H.rounds_out(rounds, roster, winners)
    assert len(actual_rounds) == len(golden["rounds"])
    for actual, expected in zip(actual_rounds, golden["rounds"]):
        assert project([actual], ("roster", "finishers", "winner")) == project([expected], ("roster", "finishers", "winner"))
        assert [p["sid"] for p in actual["top"]] == [p["sid"] for p in expected["top"]]
    assert list(dict.fromkeys(r["map"] for r in rounds)) == golden["rotation"]
    recomputed = P.compute_standings(rounds, roster, nuisance, 6)
    assert H.cross_check({"winners": winners, "finalists": finalists}, recomputed) == []


def test_existing_cup_error():
    _, errors = H.sequence_warnings(1, "2026-09-27")
    assert errors == ["Bonq #1 already exists (cups/bonq_1.json)"]
