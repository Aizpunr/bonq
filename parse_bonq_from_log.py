"""Parse a Bonq Cup from BepInEx LiveLeaderboardLogger.log -> cups/bonq_N.json.

Usage:
    python parse_bonq_from_log.py 1 --date 2026-09-27 --maps "Map A,Map B,Map C,Map D,Map E"
    python parse_bonq_from_log.py 1 --date 2026-09-27 --from 19:00 --to 21:30
    python parse_bonq_from_log.py 1 --date 2026-09-27 --maps ... --nuisance <sid>,<sid> --header "..."

Round selection (Bonq maps are not guaranteed to carry a cup tag):
- default: rounds whose map name contains "Bonq" (case-insensitive)
- --maps: comma-separated map-name substrings (replaces the default)
- --from/--to: HH:MM window on the log's local timestamps; every round that
  STARTED inside it is taken, whatever the map. Combine with --date.
The script prints every selected round; eyeball it before trusting the result.

Rules: the Top->Out mod settings Victor sent 2026-09-27, checked against the
host's topoutresults.json for Bonq #1 (validate_vs_topout.py):
- 150 125 110 100 92 85 79 73 68 63 59 55 51 48 45, 16th+ unknown (scored 35, warned)
- DNF = 20 for anyone in the lobby when the round closes and not finished
- 1000 -> finalist, points capped. Pos 1 as an EXISTING finalist wins; crossing 1000 at pos 1
  does NOT win that round (host file, Bonq #1). 5 winners.
- No discovery round: every selected round scores.
- Nuisance (--nuisance): points off-pool, a nuisance pos 1 blocks the round's W.
- Last-round rule: qualifying in the LAST scoring round gives no finalist tag.
- Participation filter: rostered >= MIN_ROUNDS scoring rounds AND >= 1 finish.
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date as _date

DEFAULT_LOG = r"C:\Program Files (x86)\Steam\steamapps\common\Zeepkist\BepInEx\LiveLeaderboardLogger.log"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CUPS_DIR = os.path.join(PROJECT_DIR, "cups")
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")
MIN_ROUNDS_PLAYED = 6   # Kerki's rule; revisit once Bonq has a few cups

# Read off the host's topoutresults.json for Bonq #1: NOT Kerki's list past 12th
# (Kerki: 52 49 46). Only 15 places were ever reached, so 16th+ is unknown.
_TOP = [150, 125, 110, 100, 92, 85, 79, 73, 68, 63, 59, 55, 51, 48, 45]
POINTS = {
    "name": "Bonq Top->Out (host file verified to 15th)",
    "threshold": 1000,
    "dnf": 20,
    "floor": 35,        # ZeepTourney baseline; where the list meets it is unknown
    "winners": 5,
}
_unverified_positions = set()


def points_for_pos(pos):
    if pos <= len(_TOP):
        return _TOP[pos - 1]
    _unverified_positions.add(pos)
    return POINTS["floor"]


def strip_tag(name):
    """'[CSC] Shadynook' -> 'Shadynook' (in-game clan tag prefix)."""
    return re.sub(r"^\[[^\]]*\]\s*", "", name or "").strip()


# ── Steam ID registry (shared across all comps) ───────────────────
# Same logic as kerki/parse_kerki_from_log.py: Steam ID is the identity, the
# in-game name is not (players rename mid-cup).
PLAYERS_MASTER = os.path.normpath(
    os.path.join(PROJECT_DIR, '..', 'zeepkist cotd elo', 'players.json'))

IDENTITY_EXCEPTIONS = {
    '76561199498272376': 'Butter/Pants are brothers and share accounts',
    '76561199529259554': 'Butter/Pants are brothers and share accounts',
}


def load_registry(path=PLAYERS_MASTER):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  WARN: could not read {path}: {e}", file=sys.stderr)
        return {}
    aliased = set()
    for canon, rec in data.items():
        for a in (rec.get('aliases') or []):
            if a != canon:
                aliased.add(a.lower())
    by_sid = {}
    for canon, rec in data.items():
        sid = rec.get('steam_id')
        if sid:
            by_sid.setdefault(str(sid), []).append(canon)
    registry = {}
    for sid, canons in by_sid.items():
        if len(canons) > 1:
            canons = sorted(canons, key=lambda c: (
                c.startswith('['), c.lower() in aliased,
                -len(data[c].get('aliases') or []), c.lower()))
        names = set()
        for c in canons:
            for n in [c] + list(data[c].get('aliases') or []):
                names.add(n.lower())
                names.add(strip_tag(n).lower())
        spellings = {strip_tag(c).lower(): strip_tag(c) for c in canons}
        registry[sid] = (canons[0], names, spellings)
    return registry


def resolve_roster(roster, roster_names, registry, apply_fix=True):
    """Rewrite names to the registry canonical, keyed on Steam ID.
    Returns (substitutions, unknown_sids, exceptions)."""
    subs, unknown, exempt = [], [], []
    owner = {}
    for sid, (_canon, names, _sp) in registry.items():
        for n in names:
            owner.setdefault(n, sid)
    for sid, seen in sorted(roster_names.items()):
        if sid in IDENTITY_EXCEPTIONS:
            exempt.append((sid, sorted(seen), IDENTITY_EXCEPTIONS[sid]))
            continue
        entry = registry.get(sid)
        if entry is None:
            unknown.append((sid, sorted(seen)))
            if apply_fix:
                roster[sid] = strip_tag(roster.get(sid, ''))
            continue
        canon, _known, spellings = entry
        shown = roster.get(sid, '')
        key = strip_tag(shown).lower()
        if key in spellings:
            if apply_fix:
                roster[sid] = spellings[key]
            continue
        target = strip_tag(canon)
        stolen = sorted({owner[k] for k in (shown.lower(), key)
                         if owner.get(k) and owner[k] != sid})
        subs.append((sid, sorted(seen), target, stolen))
        if apply_fix:
            roster[sid] = target
    return subs, unknown, exempt


def report_identity(subs, unknown, exempt, registry_loaded, apply_fix):
    if not registry_loaded:
        print(f"\n  !! STEAM ID CHECK SKIPPED: no registry at {PLAYERS_MASTER}")
        return
    if subs:
        verb = "resolved" if apply_fix else "NOT resolved (--accept-names)"
        print(f"\n  Steam ID {verb}: {len(subs)}")
        for sid, seen, canon, stolen in subs:
            print(f"    {sid}  saw {', '.join(repr(n) for n in seen)}  ->  {canon!r}")
            if stolen:
                print(f"      !! that name belongs to {', '.join(stolen)}")
    else:
        print("\n  Steam ID check: every name matches the registry")
    for sid, seen, why in exempt:
        print(f"  Identity exception, name kept: {sid}  {', '.join(repr(n) for n in seen)}  ({why})")
    if unknown:
        print(f"  Not in the registry ({len(unknown)}), add them to players.json:")
        for sid, seen in unknown:
            print(f"    {sid}  {', '.join(repr(n) for n in seen)}")


# ── Log parsing ────────────────────────────────────────────────────
def parse_log(log_path, restrict_date=None):
    """All rounds in the log (optionally one date) + roster.
    Returns (rounds, roster, roster_names)."""
    roster, roster_names, rounds = {}, {}, []
    current_round, current_lb = None, None
    current_session, session_active = 0, False

    def close():
        nonlocal current_round, current_lb
        if current_round is not None:
            current_round["last_lb"] = current_lb
            rounds.append(current_round)
        current_round, current_lb = None, None

    with open(log_path, encoding="utf-8-sig") as f:
        for line in f:
            m = re.match(r"^([\d\-T:.]+) \[LiveLeaderboardLogger\] (.+)$", line.rstrip("\n"))
            if not m:
                continue
            ts, evt = m.group(1), m.group(2)
            if restrict_date and not ts.startswith(restrict_date):
                continue
            parts = evt.split("|")
            kind = parts[0]
            if kind == "SESSION_START":
                current_session += 1
                session_active = True
                close()
            elif kind == "SESSION_END":
                close()
                session_active = False
            elif not session_active:
                continue
            elif kind == "ROSTER":
                sid, name = parts[1], parts[2]
                roster[sid] = name
                roster_names.setdefault(sid, set()).add(name)
                if current_round is not None:
                    current_round["roster"].add(sid)
            elif kind == "ROUND_STARTED":
                close()
                current_round = {
                    "num": int(parts[1]), "hash": parts[2],
                    "map": parts[3] if len(parts) > 3 else "",
                    "session": current_session, "last_lb": None, "ended": False,
                    "roster": set(), "ts": ts,
                }
            elif kind == "ROUND_ENDED":
                if current_round is not None:
                    current_round["ended"] = True
            elif kind == "LEADERBOARD":
                if current_round is None or current_round.get("ended"):
                    continue
                payload = parts[3] if len(parts) > 3 else ""
                entries = []
                for tok in payload.split(",") if payload else []:
                    p = tok.split(":")
                    if len(p) >= 3:
                        entries.append({"pos": int(p[0]), "sid": p[1], "time": p[2]})
                if current_lb is None or len(entries) >= len(current_lb):
                    current_lb = entries
    close()
    return rounds, roster, roster_names


def select_rounds(rounds, maps=(), t_from=None, t_to=None):
    maps = [m.lower() for m in maps if m]
    out = []
    for r in rounds:
        hhmm = r["ts"][11:16]
        if t_from or t_to:
            if t_from and hhmm < t_from:
                continue
            if t_to and hhmm > t_to:
                continue
            if maps and not any(x in r["map"].lower() for x in maps):
                continue
            out.append(r)
        elif maps:
            if any(x in r["map"].lower() for x in maps):
                out.append(r)
        elif "bonq" in r["map"].lower():
            out.append(r)
    return out


# ── Standings ──────────────────────────────────────────────────────
def compute_standings(cup_rounds, roster, nuisance_sids, min_rounds=MIN_ROUNDS_PLAYED):
    threshold = POINTS["threshold"]
    scoring = [r for r in cup_rounds if r["last_lb"]]   # aborted rounds dropped

    rotation = []
    for r in scoring:
        if r["map"] not in rotation:
            rotation.append(r["map"])

    points = defaultdict(int)
    nuisance_points = defaultdict(int)
    qualified_round, won_in_round, winners_in_order = {}, {}, []
    participants = set()
    rounds_played = defaultdict(int)
    finishes = defaultdict(int)

    def add(sid, pts, s_idx):
        if sid in nuisance_sids:
            nuisance_points[sid] += pts
            return
        if sid in qualified_round:
            return
        total = points[sid] + pts
        if total >= threshold:
            points[sid] = threshold
            qualified_round[sid] = s_idx
        else:
            points[sid] = total

    # The mod pays the DNF 20 to whoever is in the lobby when the round closes:
    # mid-round joiners get it, mid-round leavers get 0. The log has no leave
    # event, so "still here at the next ROUND_STARTED" stands in for it
    # (Bonq #1: 2 mismatches vs the host file out of ~400 player-rounds).
    pos_in_all = {id(r): i for i, r in enumerate(cup_rounds)}

    def at_round_close(r):
        j = pos_in_all[id(r)] + 1
        return cup_rounds[j]["roster"] if j < len(cup_rounds) else r["roster"]

    for s_idx, r in enumerate(scoring, start=1):
        for sid in r["roster"]:
            rounds_played[sid] += 1
            participants.add(sid)
        lb = sorted(r["last_lb"], key=lambda x: x["pos"])
        finished = set()
        for rank0, e in enumerate(lb):
            participants.add(e["sid"])
            finishes[e["sid"]] += 1
            finished.add(e["sid"])
            add(e["sid"], points_for_pos(rank0 + 1), s_idx)
        for sid in at_round_close(r):
            if sid not in finished:
                participants.add(sid)
                add(sid, POINTS["dnf"], s_idx)
        # Must already be a finalist BEFORE this round. Crossing 1000 at pos 1
        # only makes you a finalist: the host's topoutresults.json for Bonq #1
        # shows RoundNzt 921 -> 1071 at pos 1 in S17 with no W (won S20).
        w = lb[0]["sid"] if lb else None
        if (w and w not in nuisance_sids and w in qualified_round
                and qualified_round[w] < s_idx and w not in won_in_round
                and len(winners_in_order) < POINTS["winners"]):
            won_in_round[w] = s_idx
            winners_in_order.append(w)

    last = len(scoring)
    finalists_valid = {s: q for s, q in qualified_round.items() if q < last or s in won_in_round}
    winner_set = set(winners_in_order)

    def name_of(sid):
        return roster.get(sid, f"<{sid}>")

    winners = []
    for i, sid in enumerate(winners_in_order, 1):
        r = scoring[won_in_round[sid] - 1]
        e = next((x for x in r["last_lb"] if x["sid"] == sid), None)
        winners.append({"rank": i, "sid": sid, "name": name_of(sid),
                        "won_scoring_round": won_in_round[sid],
                        "qualified_scoring_round": qualified_round[sid],
                        "map": r["map"], "time": e["time"] if e else "?"})
    finalists = [{"rank": i, "sid": sid, "name": name_of(sid),
                  "qualified_scoring_round": finalists_valid[sid]}
                 for i, sid in enumerate(sorted((s for s in finalists_valid if s not in winner_set),
                                                key=lambda s: finalists_valid[s]), 1)]
    rest, excluded = [], []
    for sid in participants:
        if sid in winner_set or sid in finalists_valid or sid in nuisance_sids:
            continue
        if rounds_played[sid] >= min_rounds and finishes[sid] >= 1:
            rest.append(sid)
        else:
            excluded.append(sid)
    rest.sort(key=lambda s: (-points[s], name_of(s).lower()))
    others = [{"rank": i, "sid": s, "name": name_of(s), "points": points[s],
               "rounds_played": rounds_played[s], "finishes": finishes[s]}
              for i, s in enumerate(rest, 1)]
    nuisance = [{"sid": s, "name": name_of(s), "points": nuisance_points[s],
                 "rounds_played": rounds_played[s], "finishes": finishes[s]}
                for s in sorted(nuisance_sids, key=lambda s: -nuisance_points[s])
                if s in participants or rounds_played[s] > 0]
    excl = [{"sid": s, "name": name_of(s), "points": points[s],
             "rounds_played": rounds_played[s], "finishes": finishes[s]}
            for s in sorted(excluded, key=lambda s: -rounds_played[s])]

    # Rounds finished vs rounds present. "Present" = in the lobby roster for the
    # round; the log can't tell a driver from a spectator/AFK (the game's
    # ZeepkistState isn't logged), so the % is a floor on the real finish rate.
    finish_stats = sorted(
        ({"sid": s, "name": name_of(s), "present": rounds_played[s], "finished": finishes[s],
          "pct": round(100 * finishes[s] / rounds_played[s], 1) if rounds_played[s] else None}
         for s in participants | set(rounds_played)),
        key=lambda x: (-x["finished"], -(x["pct"] or 0), x["name"].lower()))

    # Per-round results, for the page's round-by-round view.
    rounds_out = []
    for s_idx, r in enumerate(scoring, start=1):
        lb = sorted(r["last_lb"], key=lambda x: x["pos"])
        rounds_out.append({
            "n": s_idx, "map": r["map"], "ts": r["ts"],
            "roster": len(r["roster"]), "finishers": len(lb),
            "finish_pct": round(100 * len(lb) / len(r["roster"]), 1) if r["roster"] else None,
            "top": [{"sid": e["sid"], "name": name_of(e["sid"]), "time": e["time"]} for e in lb[:3]],
            "winner": next((w["name"] for w in winners if w["won_scoring_round"] == s_idx), None),
        })

    return {"rotation": rotation, "scoring_rounds_count": last,
            "winners": winners, "finalists": finalists, "others": others,
            "nuisance": nuisance, "excluded": excl, "rounds": rounds_out,
            "finish_stats": finish_stats}


def apply_host_file(st, host_file, roster):
    """When the host's topoutresults.json exists it wins over the log for:
    - Others' points (final pointsTotal), re-ranked;
    - rounds finished: finished = has a time, present = finished or DNF paid 20
      (in the lobby at round close; DNF 0 = had left). The log counts presence
      at round START; the two differ by <=1 round per player (Bonq #1).
    Winners, finalists and nuisance are NOT touched: validate_vs_topout.py checks those."""
    with open(host_file, encoding="utf-8-sig") as f:
        hrounds = json.load(f)["rounds"]
    if len(hrounds) != len(st["rounds"]):
        sys.exit(f"  ERROR: host file has {len(hrounds)} rounds, log has {len(st['rounds'])}. "
                 f"Run validate_vs_topout.py before using --host-file.")

    fin, pres = defaultdict(int), defaultdict(int)
    for h, r in zip(hrounds, st["rounds"]):
        f_n = p_n = 0
        for p in h["playerResults"]:
            sid, done = str(p["steamID"]), not isinstance(p["time"], str)
            if done or p["gainedPoints"] > 0:
                pres[sid] += 1
                p_n += 1
                if done:
                    fin[sid] += 1
                    f_n += 1
        r["finishers"], r["roster"] = f_n, p_n
        r["finish_pct"] = round(100 * f_n / p_n, 1) if p_n else None
    stats = {x["sid"]: x for x in st["finish_stats"]}
    for sid in set(pres) | set(stats):
        x = stats.setdefault(sid, {"sid": sid, "name": roster.get(sid, f"<{sid}>")})
        x["present"], x["finished"] = pres[sid], fin[sid]
        x["pct"] = round(100 * fin[sid] / pres[sid], 1) if pres[sid] else None
    st["finish_stats"] = sorted(stats.values(),
                                key=lambda x: (-x["finished"], -(x["pct"] or 0), x["name"].lower()))
    st["finish_source"] = "host topoutresults.json"

    tot = {str(p["steamID"]): p["pointsTotal"] for p in hrounds[-1]["playerResults"]}
    for o in st["others"]:
        h = tot.get(o["sid"])
        if h is not None and h != o["points"]:
            print(f"  host total: {o['name']} {o['points']} -> {h}")
            o["points"] = h
    st["others"].sort(key=lambda o: (-o["points"], o["name"].lower()))
    for i, o in enumerate(st["others"], 1):
        o["rank"] = i
    st["points_source"] = "host topoutresults.json"


def main():
    # Player names carry emoji / math alphanumerics; the Windows console is cp1252.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("cup", type=int, help="Bonq Cup number")
    p.add_argument("--log", default=DEFAULT_LOG)
    p.add_argument("--date", default=None, help="YYYY-MM-DD (cup date; also filters the log)")
    p.add_argument("--maps", default="", help="Comma-separated map-name substrings")
    p.add_argument("--from", dest="t_from", default=None, help="HH:MM, first round start to include")
    p.add_argument("--to", dest="t_to", default=None, help="HH:MM, last round start to include")
    p.add_argument("--nuisance", default="", help="Comma-separated steamIDs (mappers/hosts who raced)")
    p.add_argument("--header", default=None, help="e.g. 'Bonq Cup #1 - Maps by A, B, C'")
    p.add_argument("--min-rounds", type=int, default=MIN_ROUNDS_PLAYED)
    p.add_argument("--accept-names", action="store_true", help="Skip Steam ID resolution")
    p.add_argument("--dry-run", action="store_true", help="Print only, write nothing")
    p.add_argument("--host-file", default=None,
                   help="Host's topoutresults.json: its final pointsTotal overrides ours for "
                        "Others (the mod's own bookkeeping has quirks the log can't reproduce)")
    args = p.parse_args()

    nuisance = {s.strip() for s in args.nuisance.split(",") if s.strip()}
    maps = [s.strip() for s in args.maps.split(",") if s.strip()]

    print(f"== Bonq Cup #{args.cup} ==")
    print(f"  points: {POINTS['name']}  threshold={POINTS['threshold']}  dnf={POINTS['dnf']}")
    print(f"  log: {args.log}   date: {args.date or '(any)'}")

    rounds, roster, roster_names = parse_log(args.log, restrict_date=args.date)
    print(f"  rounds in log: {len(rounds)}")
    cup_rounds = select_rounds(rounds, maps, args.t_from, args.t_to)
    if not cup_rounds:
        print("  ERROR: no rounds selected. Maps seen in the log:", file=sys.stderr)
        for m in sorted({r['map'] for r in rounds}):
            print(f"    {m}", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Selected rounds ({len(cup_rounds)}):")
    for r in cup_rounds:
        n = len(r["last_lb"] or [])
        flag = "" if n else "   <- no finishers, dropped"
        print(f"    {r['ts'][11:19]}  s{r['session']} r{r['num']:<3} {r['map'][:40]:40s} {n:>2} fin{flag}")

    registry = load_registry()
    subs, unknown, exempt = resolve_roster(roster, roster_names, registry,
                                           apply_fix=not args.accept_names)
    report_identity(subs, unknown, exempt, bool(registry), not args.accept_names)

    st = compute_standings(cup_rounds, roster, nuisance, args.min_rounds)
    if args.host_file:
        apply_host_file(st, args.host_file, roster)
    if _unverified_positions:
        print(f"\n  !! finishers at positions {sorted(_unverified_positions)}: points past 15th are "
              f"UNVERIFIED (scored as {POINTS['floor']}). Get the host's topoutresults.json.", file=sys.stderr)
    rotation = st["rotation"]
    if len(rotation) != 5:
        print(f"\n  WARN: expected 5 maps, found {len(rotation)}", file=sys.stderr)
    print(f"\n  rotation: {rotation}")
    print(f"  scoring rounds: {st['scoring_rounds_count']}")
    print(f"\nWinners ({len(st['winners'])}):")
    for w in st["winners"]:
        print(f"  {w['rank']}. {w['name']:25s} S{w['won_scoring_round']} {w['map'][:30]}  {w['time']}  (qualified S{w['qualified_scoring_round']})")
    print(f"\nFinalists ({len(st['finalists'])}):")
    for f_ in st["finalists"]:
        print(f"  {f_['rank']}. {f_['name']:25s} qualified S{f_['qualified_scoring_round']}")
    print(f"\nOthers ({len(st['others'])}):")
    for o in st["others"]:
        print(f"  {o['rank']:2d}. {o['name']:25s} {o['points']:>4} pts  ({o['rounds_played']}r/{o['finishes']}f)")
    if st["nuisance"]:
        print(f"\nNuisance ({len(st['nuisance'])}):")
        for n in st["nuisance"]:
            print(f"   - {n['name']:25s} {n['points']} pts  ({n['rounds_played']}r/{n['finishes']}f)")
    if st["excluded"]:
        print(f"\nExcluded, <{args.min_rounds}r or 0f ({len(st['excluded'])}):")
        for e in st["excluded"]:
            print(f"   - {e['name']:25s} pts={e['points']:>4} rounds={e['rounds_played']} finishes={e['finishes']}")

    src = st.get("finish_source", "log: present = in lobby at round start")
    print(f"\nRounds finished ({src}; spectators count as present):")
    for r in st["rounds"]:
        print(f"  R{r['n']:<2} {r['map'][:32]:32s} {r['finishers']:>2}/{r['roster']:<2} {r['finish_pct']:>5}%")
    tot_f = sum(r["finishers"] for r in st["rounds"])
    tot_p = sum(r["roster"] for r in st["rounds"])
    print(f"  cup total: {tot_f}/{tot_p} = {100 * tot_f / tot_p:.1f}%")

    if args.dry_run:
        print("\n(dry run, nothing written)")
        return

    cup_date = args.date or _date.today().isoformat()
    os.makedirs(CUPS_DIR, exist_ok=True)
    out = os.path.join(CUPS_DIR, f"bonq_{args.cup}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"cup": args.cup, "date": cup_date, "header": args.header,
                   "points_system": POINTS["name"], **st}, f, indent=2, ensure_ascii=False)
    print(f"\nJSON: {out}")

    # Archive the cup's day of the raw log. The game-side file keeps every lobby
    # since the mod was installed (months, ~15 MB); only the cup belongs in this repo.
    os.makedirs(LOGS_DIR, exist_ok=True)
    arch = os.path.join(LOGS_DIR, f"bonq_{args.cup}.log")
    if os.path.abspath(args.log) != os.path.abspath(arch) and not os.path.exists(arch):
        with open(args.log, encoding="utf-8-sig") as src, \
                open(arch, "w", encoding="utf-8", newline="") as dst:
            dst.writelines(l for l in src if l.startswith(cup_date))
        print(f"Log archived ({cup_date} only): {arch}")
    print("Now run: python build_bonq.py")


if __name__ == "__main__":
    main()
