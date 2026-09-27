"""cups/bonq_N.json -> bonq.json (what index.html renders).

Usage: python build_bonq.py

Dates are ISO (YYYY-MM-DD) everywhere, on purpose: Kerki's D/M/YY strings
have already crashed the cross-comp refresh once.
"""
import glob
import json
import os
import re
from datetime import datetime, timezone

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CUPS_DIR = os.path.join(PROJECT_DIR, "cups")
OUT = os.path.join(PROJECT_DIR, "bonq.json")

# Same result points as Kerki's ranking, so a Bonq W1 weighs like a Kerki W1.
RANK_POINTS = {"w1": 200, "w2": 140, "w3": 100, "w4": 70, "w5": 40, "f": 10}


def load_cups():
    cups = []
    for path in glob.glob(os.path.join(CUPS_DIR, "bonq_*.json")):
        if not re.search(r"bonq_\d+\.json$", path):
            continue
        with open(path, encoding="utf-8") as f:
            cups.append(json.load(f))
    return sorted(cups, key=lambda c: c["cup"])


def map_finish(rounds):
    """Finishers / lobby per map across its rounds, hardest first."""
    agg = {}
    for r in rounds:
        a = agg.setdefault(r["map"], [0, 0])
        a[0] += r.get("finishers", 0)
        a[1] += r.get("roster", 0)
    return sorted(([m, f, p] for m, (f, p) in agg.items()), key=lambda x: x[1] / x[2] if x[2] else 1)


def build():
    cups = load_cups()
    players = {}

    def player(sid, name):
        p = players.get(sid)
        if p is None:
            p = players[sid] = {"sid": sid, "name": name, "apps": 0, "wins": 0,
                                "w1": 0, "w2": 0, "w3": 0, "w4": 0, "w5": 0,
                                "finalist": 0, "points": 0, "finished": 0, "present": 0, "best": None, "history": []}
        p["name"] = name   # latest canonical wins
        return p

    cups_out = []
    for c in cups:
        n = c["cup"]
        ordinal = []   # winners -> finalists -> others; nuisance excluded (cross-comp convention)
        for w in c["winners"]:
            p = player(w["sid"], w["name"])
            key = f"w{w['rank']}"
            p[key] += 1
            p["wins"] += 1
            res = key
            ordinal.append(w)
            p["history"].append({"cup": n, "result": res, "points": RANK_POINTS[res]})
        for f_ in c["finalists"]:
            p = player(f_["sid"], f_["name"])
            p["finalist"] += 1
            ordinal.append(f_)
            p["history"].append({"cup": n, "result": "f", "points": RANK_POINTS["f"]})
        for o in c["others"]:
            p = player(o["sid"], o["name"])
            ordinal.append(o)
            p["history"].append({"cup": n, "result": "o", "cup_points": o["points"], "points": 0})
        for pos, e in enumerate(ordinal, 1):
            p = players[e["sid"]]
            p["apps"] += 1
            p["best"] = pos if p["best"] is None else min(p["best"], pos)
            p["history"][-1]["pos"] = pos

        cups_out.append({
            "id": n, "date": c["date"], "header": c.get("header"),
            "maps": c.get("rotation", []),
            "scoring_rounds": c.get("scoring_rounds_count"),
            "lobby_size": len(ordinal),
            "winners": [{"name": w["name"], "sid": w["sid"], "map": w["map"], "time": w["time"],
                         "won": w["won_scoring_round"], "qualified": w["qualified_scoring_round"]}
                        for w in c["winners"]],
            "finalists": [{"name": f_["name"], "sid": f_["sid"],
                           "qualified": f_["qualified_scoring_round"]} for f_ in c["finalists"]],
            "others": [{"name": o["name"], "sid": o["sid"], "points": o["points"],
                        "rounds": o["rounds_played"], "finishes": o["finishes"]} for o in c["others"]],
            "nuisances": [{"name": x["name"], "sid": x["sid"], "points": x["points"]}
                          for x in c.get("nuisance", [])],
            "rounds": c.get("rounds", []),
            "finish": {x["sid"]: [x["finished"], x["present"]] for x in c.get("finish_stats", [])},
            "map_finish": map_finish(c.get("rounds", [])),
        })
        for x in c.get("finish_stats", []):
            if x["sid"] in players:
                players[x["sid"]]["finished"] += x["finished"]
                players[x["sid"]]["present"] += x["present"]

    for p in players.values():
        p["points"] = sum(h["points"] for h in p["history"])
    ranking = sorted(players.values(),
                     key=lambda p: (-p["points"], -p["wins"], -p["finalist"],
                                    p["best"] or 999, p["name"].lower()))
    for i, p in enumerate(ranking, 1):
        p["rank"] = i

    out = {
        "meta": {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "cups": len(cups_out), "players": len(ranking),
                 "rank_points": RANK_POINTS},
        "cups": cups_out,
        "ranking": ranking,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f"bonq.json: {len(cups_out)} cups, {len(ranking)} players")


if __name__ == "__main__":
    build()
