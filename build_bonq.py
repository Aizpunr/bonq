"""cups/bonq_N.json -> bonq.json (what index.html renders).

Usage: python build_bonq.py

Same ranking systems as Kerki Comp (kerki/build_kerki.py), so the two pages
read the same way:
- Records: career W / 1st / 2nd / 3rd / Finalist counts.
- Rolling: placement points, best RANK_BEST_OF of the last RANK_WINDOW cups.
- Skill: convergent Glicko-style rating toward per-placement performance values.
Bonq has no troll cups and no seasons, so those Kerki pieces are left out.

Rank arrows compare against the same build minus the latest cup (Kerki keeps a
snapshot.json instead; recomputing needs no extra step per cup).

Dates are ISO (YYYY-MM-DD) everywhere, on purpose: Kerki's D/M/YY strings
have already crashed the cross-comp refresh once.
"""
import glob
import json
import math
import os
import re
from datetime import datetime, timezone

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CUPS_DIR = os.path.join(PROJECT_DIR, "cups")
OUT = os.path.join(PROJECT_DIR, "bonq.json")

# ── Kerki constants, copied as-is ──────────────────────────────────
RANK_POINTS = {'w1': 200, 'w2': 140, 'w3': 100, 'w4': 70, 'w5': 40, 'f': 10}
RANK_WINDOW = 26
RANK_BEST_OF = 18

GLICKO_PERF = {'w1': 2100, 'w2': 1950, 'w3': 1875, 'w4': 1850, 'w5': 1800, 'f': 1700, 'o': 1500}
GLICKO_TAU = 200
GLICKO_MU_START = 1500
GLICKO_SIGMA_START = 350
GLICKO_SIGMA_FLOOR = 80
GLICKO_SIGMA_CAP = 350
GLICKO_SIGMA_DECAY = 15
GLICKO_MU_DECAY = 0.02
GLICKO_SURPRISE_THRESHOLD = 200
GLICKO_SURPRISE_FACTOR = 0.3


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


def latest_names(cups):
    """sid -> the most recent name it raced under (parser already resolved it)."""
    names = {}
    for c in cups:
        for grp in ("winners", "finalists", "others", "nuisance"):
            for e in c.get(grp, []):
                names[e["sid"]] = e["name"]
    return names


def compute(cups, names):
    """Records / Rolling / Skill for a list of cups. Keyed by sid, labelled by name."""
    players = {}
    for c in cups:
        k = c["cup"]
        seen = set()
        for res, grp in [(f"w{w['rank']}", [w]) for w in c["winners"]] + \
                        [("f", c["finalists"]), ("o", c["others"])]:
            for e in grp:
                if e["sid"] in seen:
                    continue
                seen.add(e["sid"])
                p = players.setdefault(e["sid"], {
                    'sid': e["sid"], 'apps': 0, 'wins': 0,
                    'w1': 0, 'w2': 0, 'w3': 0, 'w4': 0, 'w5': 0,
                    'finalist': 0, 'best': 99, 'history': []})
                p['apps'] += 1
                if res.startswith('w'):
                    p['wins'] += 1
                    p[res] += 1
                    p['best'] = min(p['best'], int(res[1]))
                elif res == 'f':
                    p['finalist'] += 1
                    p['best'] = min(p['best'], 6)
                p['history'].append({'k': k, 'result': res})

    player_list = []
    for sid, p in sorted(players.items(), key=lambda x: (-x[1]['wins'], -x[1]['apps'])):
        player_list.append({**p, 'name': names.get(sid, sid),
                            'best': p['best'] if p['best'] < 99 else None})

    # Rolling
    ids = sorted(c["cup"] for c in cups)
    eligible = set(ids[-RANK_WINDOW:])
    ranking = []
    for pl in player_list:
        scoring, window_apps = [], 0
        for h in pl['history']:
            if h['k'] not in eligible:
                continue
            window_apps += 1
            pts = RANK_POINTS.get(h['result'], 0)
            if pts > 0:
                scoring.append({'k': h['k'], 'result': h['result'], 'points': pts})
        if not scoring:
            continue
        scoring.sort(key=lambda x: -x['points'])
        for i, e in enumerate(scoring):
            e['counted'] = i < RANK_BEST_OF
        res = [e['result'] for e in scoring]
        counted = sum(1 for e in scoring if e['counted'])
        ranking.append({
            'name': pl['name'], 'sid': pl['sid'],
            'points': sum(e['points'] for e in scoring if e['counted']),
            'points_all': sum(e['points'] for e in scoring),
            'apps': window_apps,
            'wins': sum(1 for r in res if r.startswith('w')),
            'w1': res.count('w1'), 'w2': res.count('w2'), 'w3': res.count('w3'),
            'w4': res.count('w4'), 'w5': res.count('w5'), 'finalist': res.count('f'),
            'counted': counted, 'dropped': len(scoring) - counted,
            'history': sorted(scoring, key=lambda x: x['k']),
        })
    ranking.sort(key=lambda x: (-x['points'], -x['wins'], -x['w1']))
    for i, r in enumerate(ranking, 1):
        r['rank'] = i

    # Skill
    events = {pl['sid']: sorted((h['k'], h['result']) for h in pl['history']) for pl in player_list}
    ratings = {}
    for kid in ids:
        here = {sid for sid, ev in events.items() if any(k == kid for k, _ in ev)}
        for sid, r in ratings.items():
            if sid not in here:
                r['mu'] = GLICKO_MU_START + (r['mu'] - GLICKO_MU_START) * (1 - GLICKO_MU_DECAY)
                r['sigma'] = min(r['sigma'] + GLICKO_SIGMA_DECAY, GLICKO_SIGMA_CAP)
        for sid in here:
            result = next(res for k, res in events[sid] if k == kid)
            r = ratings.setdefault(sid, {'mu': GLICKO_MU_START, 'sigma': GLICKO_SIGMA_START, 'history': []})
            perf = GLICKO_PERF.get(result, 1500)
            surprise = abs(perf - r['mu'])
            if surprise > GLICKO_SURPRISE_THRESHOLD:
                r['sigma'] = min(r['sigma'] + (surprise - GLICKO_SURPRISE_THRESHOLD) * GLICKO_SURPRISE_FACTOR,
                                 GLICKO_SIGMA_CAP)
            lr = r['sigma'] ** 2 / (r['sigma'] ** 2 + GLICKO_TAU ** 2)
            r['mu'] += lr * (perf - r['mu'])
            r['sigma'] = max(math.sqrt((1 - lr) * r['sigma'] ** 2), GLICKO_SIGMA_FLOOR)
            r['history'].append({'k': kid, 'mu': round(r['mu'], 1), 'sigma': round(r['sigma'], 1),
                                 'result': result})
    glicko = []
    for sid, r in ratings.items():
        if not any(h['result'] != 'o' for h in r['history']):
            continue
        res = [h['result'] for h in r['history']]
        glicko.append({
            'name': names.get(sid, sid), 'sid': sid,
            'mu': round(r['mu'], 1), 'sigma': round(r['sigma'], 1),
            'apps': len(res), 'wins': sum(1 for x in res if x.startswith('w')),
            'w1': res.count('w1'), 'w2': res.count('w2'), 'w3': res.count('w3'),
            'w4': res.count('w4'), 'w5': res.count('w5'), 'finalist': res.count('f'),
            'history': r['history'],
        })
    glicko.sort(key=lambda x: -x['mu'])
    for i, g in enumerate(glicko, 1):
        g['rank'] = i

    return player_list, ranking, glicko, eligible


def build():
    cups = load_cups()
    names = latest_names(cups)
    player_list, ranking, glicko, eligible = compute(cups, names)

    # Arrows: the same build without the latest cup.
    prev_p, prev_r, prev_g = {}, {}, {}
    if len(cups) > 1:
        pp, pr, pg, _ = compute(cups[:-1], names)
        prev_p = {p['sid']: p['history'] for p in pp}
        prev_r = {r['sid']: r for r in pr}
        prev_g = {g['sid']: g for g in pg}
    for r in ranking:
        o = prev_r.get(r['sid'])
        r['prev_rank'], r['prev_points'] = (o['rank'], o['points']) if o else (None, None)
    for g in glicko:
        o = prev_g.get(g['sid'])
        g['prev_rank'], g['prev_mu'] = (o['rank'], o['mu']) if o else (None, None)
    for p in player_list:
        p['prev_history'] = prev_p.get(p['sid'], [])

    # Rounds finished, all cups. Rounds a player raced as nuisance (mapper)
    # don't count: they know their own maps.
    def racing_stats(c):
        nuis = {n["sid"] for n in c.get("nuisance", [])}
        return [x for x in c.get("finish_stats", []) if x["sid"] not in nuis and x["present"]]

    fin = {}
    for c in cups:
        for x in racing_stats(c):
            a = fin.setdefault(x["sid"], [0, 0])
            a[0] += x["finished"]
            a[1] += x["present"]
    for p in player_list:
        p['finished'], p['present'] = fin.get(p['sid'], [0, 0])

    # Finishes leaderboard: everyone who raced in a Bonq lobby, drop-ins
    # included (the page filters by minimum rounds), nuisance cups excluded.
    fl = {}
    for c in cups:
        for x in racing_stats(c):
            e = fl.setdefault(x["sid"], {'sid': x["sid"], 'name': names.get(x["sid"], x["name"]),
                                         'finished': 0, 'present': 0, 'cups': 0, 'history': []})
            e['finished'] += x["finished"]
            e['present'] += x["present"]
            e['cups'] += 1
            e['history'].append({'k': c["cup"], 'f': x["finished"], 'p': x["present"]})
    finishers = sorted(fl.values(), key=lambda e: (-e['finished'], -e['finished'] / e['present'],
                                                    e['name'].lower()))
    for e in finishers:
        e['pct'] = round(100 * e['finished'] / e['present'], 1)

    cups_out = []
    for c in cups:
        ordinal = c["winners"] + c["finalists"] + c["others"]
        cups_out.append({
            # Kerki's event shape: names in placement order ...
            'id': c["cup"], 'date': c["date"], 'troll': False,
            'winners': [w["name"] for w in c["winners"]],
            'finalists': [f["name"] for f in c["finalists"]],
            'others': [o["name"] for o in c["others"]],
            'nuisances': [n["name"] for n in c.get("nuisance", [])],
            'lobby_size': len(ordinal),
            'sof': c.get("sof"),
            # ... plus the Bonq extras.
            'header': c.get("header"),
            'maps': c.get("rotation", []),
            'scoring_rounds': c.get("scoring_rounds_count"),
            'winners_detail': [{'name': w["name"], 'map': w["map"], 'time': w["time"],
                                'won': w["won_scoring_round"], 'qualified': w["qualified_scoring_round"]}
                               for w in c["winners"]],
            'others_points': [o["points"] for o in c["others"]],
            'rounds': c.get("rounds", []),
            'finish': {names.get(x["sid"], x["name"]): [x["finished"], x["present"]]
                       for x in c.get("finish_stats", [])},
            'map_finish': map_finish(c.get("rounds", [])),
            'finish_source': "host" if c.get("finish_source") else "log",
        })

    out = {
        'meta': {
            'total_cups': len(cups_out),
            'total_players': len(player_list),
            'last_cup': max((c["cup"] for c in cups), default=0),
            'generated': datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        'cups': cups_out,
        'players': player_list,
        'ranking': {
            'window': RANK_WINDOW, 'best_of': RANK_BEST_OF,
            'window_start': min(eligible) if eligible else 0,
            'window_end': max(eligible) if eligible else 0,
            'eligible_cups': len(eligible),
            'players': ranking,
        },
        'glicko': {'start': GLICKO_MU_START, 'decay': GLICKO_MU_DECAY, 'players': glicko},
        'finishers': finishers,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f"bonq.json: {len(cups_out)} cups, {len(player_list)} players, "
          f"{len(ranking)} ranked, {len(glicko)} rated")
    for r in ranking[:6]:
        print(f"  #{r['rank']} {r['name']:15s} {r['points']:>4} pts")


if __name__ == "__main__":
    build()
