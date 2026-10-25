"""Parse a Bonq Cup from the host's Top->Out topoutresults.json -> cups/bonq_N.json.

Usage:
    python parse_bonq_from_host.py 2 --date 2026-10-25 --host-file topoutresults.json --dry-run
    python parse_bonq_from_host.py 2 --date 2026-10-25 --host-file topoutresults.json \
        [--nuisance sid,sid] [--header "..."] [--log LiveLeaderboardLogger.log] [--report r.json]

This is the submit-a-cup path: it needs no LiveLeaderboardLogger log. The host
file is the mod's own bookkeeping, so its flags are AUTHORITATIVE:
- winners = first round with winnerPosition >= 0 (0-based), ordered by it;
- qualified = first round with finalist: true. The mod sets it in the crossing
  round, so crossing in the LAST round gives no finalist tag (Kerki/Bonq rule);
- nuisance = anyone flagged nuisance in any round, unless --nuisance overrides;
- Others' points = their last pointsTotal (the mod's total, quirks and all);
- present = has a time or was paid points (DNF 20 = in the lobby at round close).
The log parser's compute_standings is then re-run on the same rounds as a
cross-check; any disagreement is printed and lands in --report, it never wins.

Output schema matches parse_bonq_from_log.py, except rounds[].ts is null (the
host file has no timestamps) and rounds_played counts presence at round CLOSE.
"""
import argparse
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import date as _date, timedelta

import parse_bonq_from_log as P

HOST_DIR = os.path.join(P.PROJECT_DIR, "host")
ANCHOR = _date(2026, 9, 27)          # Bonq #1; cups run every 14 days


def _f(t):
    return f"{t:.3f}"


def load_host(path):
    with open(path, encoding="utf-8-sig") as f:
        host = json.load(f)
    if not isinstance(host, dict) or "rounds" not in host or "settings" not in host:
        sys.exit("  ERROR: not a Top->Out results file (no settings/rounds)")
    if not host["rounds"]:
        sys.exit("  ERROR: the host file has no rounds")
    return host


def host_rounds(host):
    """Host rounds -> the round dicts compute_standings consumes, plus name maps."""
    rounds, roster, roster_names = [], {}, defaultdict(set)
    for h in host["rounds"]:
        lb, present = [], set()
        for p in h["playerResults"]:
            sid = str(p["steamID"])
            roster[sid] = p["username"]
            roster_names[sid].add(p["username"])
            done = not isinstance(p["time"], str)
            if done:
                lb.append((p["time"], sid))
            if done or p["gainedPoints"] > 0:
                present.add(sid)
        lb.sort()
        rounds.append({
            "map": h["level"]["Name"], "author": h["level"].get("Author") or "",
            "ts": None, "roster": present, "close_roster": present,
            "last_lb": [{"pos": i, "sid": s, "time": _f(t)} for i, (t, s) in enumerate(lb)],
        })
    return rounds, roster, roster_names


def mod_standings(host, rounds, roster, nuisance, min_rounds):
    """Standings from the mod's own flags."""
    hr = host["rounds"]
    last = len(hr)
    won, qual, wpos = {}, {}, {}
    nuis_pts, last_total = defaultdict(int), {}
    present, finished = defaultdict(int), defaultdict(int)
    for s_idx, (h, r) in enumerate(zip(hr, rounds), start=1):
        for p in h["playerResults"]:
            sid = str(p["steamID"])
            if p.get("winnerPosition", -1) >= 0 and sid not in won:
                won[sid], wpos[sid] = s_idx, p["winnerPosition"]
            if p.get("finalist") and sid not in qual:
                qual[sid] = s_idx
            if sid in nuisance:
                nuis_pts[sid] += p["gainedPoints"]
            last_total[sid] = p["pointsTotal"]
        for sid in r["roster"]:
            present[sid] += 1
        for e in r["last_lb"]:
            finished[e["sid"]] += 1

    name = lambda s: roster.get(s, f"<{s}>")
    order = sorted(won, key=lambda s: (wpos[s], won[s]))
    winners = []
    for i, sid in enumerate(order, 1):
        r = rounds[won[sid] - 1]
        e = next((x for x in r["last_lb"] if x["sid"] == sid), None)
        winners.append({"rank": i, "sid": sid, "name": name(sid),
                        "won_scoring_round": won[sid],
                        "qualified_scoring_round": qual.get(sid),
                        "map": r["map"], "time": e["time"] if e else "?"})
    fin = {s: q for s, q in qual.items() if s not in won and s not in nuisance and q < last}
    finalists = [{"rank": i, "sid": s, "name": name(s), "qualified_scoring_round": fin[s]}
                 for i, s in enumerate(sorted(fin, key=lambda s: (fin[s], name(s).lower())), 1)]

    everyone = set(present) | set(last_total)
    rest, excluded = [], []
    for sid in everyone:
        if sid in won or sid in fin or sid in nuisance:
            continue
        (rest if present[sid] >= min_rounds and finished[sid] >= 1 else excluded).append(sid)
    rest.sort(key=lambda s: (-last_total.get(s, 0), name(s).lower()))
    others = [{"rank": i, "sid": s, "name": name(s), "points": last_total.get(s, 0),
               "rounds_played": present[s], "finishes": finished[s]}
              for i, s in enumerate(rest, 1)]
    nuis = [{"sid": s, "name": name(s), "points": nuis_pts[s],
             "rounds_played": present[s], "finishes": finished[s]}
            for s in sorted(nuisance, key=lambda s: -nuis_pts[s]) if s in everyone]
    excl = [{"sid": s, "name": name(s), "points": last_total.get(s, 0),
             "rounds_played": present[s], "finishes": finished[s]}
            for s in sorted(excluded, key=lambda s: -present[s])]
    return winners, finalists, others, nuis, excl


def cross_check(mod, recomputed):
    """Differences between the mod's flags and our rule reimplementation."""
    diffs = []
    mw = [w["sid"] for w in mod["winners"]]
    rw = [w["sid"] for w in recomputed["winners"]]
    if mw != rw:
        diffs.append(f"winners: mod {[w['name'] for w in mod['winners']]} "
                     f"vs rules {[w['name'] for w in recomputed['winners']]}")
    mf = {f["sid"]: f["name"] for f in mod["finalists"]}
    rf = {f["sid"]: f["name"] for f in recomputed["finalists"]}
    if set(mf) != set(rf):
        diffs.append(f"finalists: only mod {sorted(mf[s] for s in set(mf) - set(rf))}, "
                     f"only rules {sorted(rf[s] for s in set(rf) - set(mf))}")
    return diffs


def finish_stats(rounds, roster):
    pres, fin = defaultdict(int), defaultdict(int)
    for r in rounds:
        for sid in r["roster"]:
            pres[sid] += 1
        for e in r["last_lb"]:
            fin[e["sid"]] += 1
    return sorted(({"sid": s, "name": roster.get(s, f"<{s}>"), "present": pres[s],
                    "finished": fin[s],
                    "pct": round(100 * fin[s] / pres[s], 1) if pres[s] else None}
                   for s in pres),
                  key=lambda x: (-x["finished"], -(x["pct"] or 0), x["name"].lower()))


def rounds_out(rounds, roster, winners):
    out = []
    for s_idx, r in enumerate(rounds, start=1):
        lb = r["last_lb"]
        n = len(r["roster"])
        out.append({
            "n": s_idx, "map": r["map"], "ts": None, "roster": n, "finishers": len(lb),
            "finish_pct": round(100 * len(lb) / n, 1) if n else None,
            "top": [{"sid": e["sid"], "name": roster.get(e["sid"], e["sid"]), "time": e["time"]}
                    for e in lb[:3]],
            "winner": next((w["name"] for w in winners if w["won_scoring_round"] == s_idx), None),
        })
    return out


def sequence_warnings(cup, cup_date):
    """Cup number / date sanity against what is already in cups/. Warnings only."""
    warns, errors = [], []
    have = {}
    for fn in os.listdir(P.CUPS_DIR) if os.path.isdir(P.CUPS_DIR) else []:
        if fn.startswith("bonq_") and fn.endswith(".json"):
            with open(os.path.join(P.CUPS_DIR, fn), encoding="utf-8") as f:
                c = json.load(f)
            have[c["cup"]] = c.get("date")
    if cup in have:
        errors.append(f"Bonq #{cup} already exists (cups/bonq_{cup}.json)")
    prev = max((n for n in have if n < cup), default=None)
    if prev is not None and cup != prev + 1:
        warns.append(f"cup number jumps from #{prev} to #{cup}")
    d = _date.fromisoformat(cup_date)
    if d.weekday() != 6:
        warns.append(f"{cup_date} is not a Sunday")
    if (d - ANCHOR).days % 14:
        warns.append(f"{cup_date} is off the 14-day Bonq cadence from {ANCHOR}")
    if prev is not None and have.get(prev):
        gap = (d - _date.fromisoformat(have[prev])).days
        if gap != 14:
            warns.append(f"{gap} days since Bonq #{prev} (expected 14)")
    return warns, errors


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("cup", type=int)
    ap.add_argument("--host-file", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--nuisance", default=None,
                    help="Comma-separated steamIDs; replaces the host file's nuisance flags")
    ap.add_argument("--header", default=None)
    ap.add_argument("--log", default=None, help="Optional LiveLeaderboardLogger log to archive")
    ap.add_argument("--min-rounds", type=int, default=P.MIN_ROUNDS_PLAYED)
    ap.add_argument("--accept-names", action="store_true")
    ap.add_argument("--report", default=None, help="Write warnings/identity/diffs as JSON here")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--compare", action="store_true",
                    help="Resend of a published cup: dry run, the cup may already exist")
    args = ap.parse_args()
    if args.compare:
        args.dry_run = True

    try:
        _date.fromisoformat(args.date)
    except ValueError:
        sys.exit(f"  ERROR: bad --date {args.date!r}")

    host = load_host(args.host_file)
    rounds, roster, roster_names = host_rounds(host)
    warnings, errors = sequence_warnings(args.cup, args.date)
    if args.compare:
        errors = [e for e in errors if "already exists" not in e]

    s = host["settings"]
    if (s.get("threshold"), s.get("winners")) != (P.POINTS["threshold"], P.POINTS["winners"]):
        warnings.append(f"mod settings threshold={s.get('threshold')} winners={s.get('winners')} "
                        f"(Bonq is 1000 / 5)")
    # Maps are named "Bonq 1 - ..." (#1) or "Bonq Cup 2 - ..." (#2).
    tag = re.compile(rf"\bbonq (cup )?{args.cup} -", re.I)
    untagged = sorted({r["map"] for r in rounds if not tag.search(r["map"])})
    if untagged:
        warnings.append(f"maps not tagged 'Bonq {args.cup} -': {untagged}")

    if args.nuisance is not None:
        nuisance = {x.strip() for x in args.nuisance.split(",") if x.strip()}
    else:
        nuisance = {str(p["steamID"]) for h in host["rounds"] for p in h["playerResults"]
                    if p.get("nuisance")}

    print(f"== Bonq Cup #{args.cup} (host file) ==")
    print(f"  file: {args.host_file}   date: {args.date}   rounds: {len(rounds)}")
    print(f"  settings: {s}")

    registry = P.load_registry()
    subs, unknown, exempt = P.resolve_roster(roster, roster_names, registry,
                                             apply_fix=not args.accept_names)
    P.report_identity(subs, unknown, exempt, bool(registry), not args.accept_names)

    winners, finalists, others, nuis, excl = mod_standings(host, rounds, roster, nuisance,
                                                           args.min_rounds)
    recomputed = P.compute_standings(rounds, roster, nuisance, args.min_rounds)
    diffs = cross_check({"winners": winners, "finalists": finalists}, recomputed)
    if not winners:
        errors.append("no winners in the host file")
    elif len(winners) < P.POINTS["winners"]:
        warnings.append(f"only {len(winners)} winners (tournament not finished, or an old file?)")

    # The mod's payout vs our table: a difference means the host changed the settings
    # (or we read a position wrong), and the log parser would score that cup wrongly.
    off = defaultdict(set)
    for h in host["rounds"]:
        done = sorted((p["time"], p["gainedPoints"]) for p in h["playerResults"]
                      if not isinstance(p["time"], str))
        for pos, (_t, g) in enumerate(done, 1):
            if g != P.points_for_pos(pos):
                off[pos].add(g)
    if off:
        warnings.append("points differ from the Bonq table: "
                        + ", ".join(f"pos {p} paid {sorted(g)} (table {P.points_for_pos(p)})"
                                    for p, g in sorted(off.items())))

    rotation = []
    authors = {}
    for r in rounds:
        if r["map"] not in rotation:
            rotation.append(r["map"])
            authors[r["map"]] = P.strip_tag(r["author"])
    if len(rotation) != 5:
        warnings.append(f"expected 5 maps, found {len(rotation)}")
    mappers = list(dict.fromkeys(a for a in authors.values() if a))
    header = args.header or f"Bonq Cup #{args.cup} - Maps by {', '.join(mappers)}"

    print(f"\n  rotation: {rotation}")
    print(f"  mappers (level Author): {mappers}")
    print(f"\nWinners ({len(winners)}):")
    for w in winners:
        print(f"  {w['rank']}. {w['name']:25s} S{w['won_scoring_round']} {w['map'][:30]}  "
              f"{w['time']}  (qualified S{w['qualified_scoring_round']})")
    print(f"\nFinalists ({len(finalists)}):")
    for f_ in finalists:
        print(f"  {f_['rank']}. {f_['name']:25s} qualified S{f_['qualified_scoring_round']}")
    print(f"\nOthers ({len(others)}):")
    for o in others:
        print(f"  {o['rank']:2d}. {o['name']:25s} {o['points']:>4} pts  "
              f"({o['rounds_played']}r/{o['finishes']}f)")
    if nuis:
        print(f"\nNuisance ({len(nuis)}):")
        for n in nuis:
            print(f"   - {n['name']:25s} {n['points']} pts  ({n['rounds_played']}r/{n['finishes']}f)")
    if excl:
        print(f"\nExcluded, <{args.min_rounds}r or 0f ({len(excl)}): "
              + ", ".join(e["name"] for e in excl))
    print("\nCross-check (mod flags vs our rules): "
          + ("agree" if not diffs else "DISAGREE"))
    for d in diffs:
        print(f"  !! {d}")
    for w in warnings:
        print(f"  WARNING: {w}")
    for e in errors:
        print(f"  ERROR: {e}", file=sys.stderr)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump({"cup": args.cup, "date": args.date, "header": header,
                       "winners": [w["name"] for w in winners],
                       "finalists": [x["name"] for x in finalists],
                       "nuisance": [n["name"] for n in nuis],
                       "winner_sids": [w["sid"] for w in winners],
                       "finalist_sids": [x["sid"] for x in finalists],
                       "nuisance_sids": sorted(n["sid"] for n in nuis),
                       "players": len(others) + len(winners) + len(finalists),
                       "rounds": len(rounds), "warnings": warnings, "errors": errors,
                       "cross_check": diffs,
                       "renamed": [{"sid": s_, "seen": seen, "as": c} for s_, seen, c, _ in subs],
                       "unknown": [{"sid": s_, "seen": seen} for s_, seen in unknown]},
                      f, indent=2, ensure_ascii=False)

    if errors:
        sys.exit(1)
    if args.dry_run:
        print("\n(dry run, nothing written)")
        return

    tot_f = sum(len(r["last_lb"]) for r in rounds)
    tot_p = sum(len(r["roster"]) for r in rounds)
    out = {"cup": args.cup, "date": args.date, "header": header,
           "points_system": P.POINTS["name"], "rotation": rotation,
           "scoring_rounds_count": len(rounds), "winners": winners, "finalists": finalists,
           "others": others, "nuisance": nuis, "excluded": excl,
           "rounds": rounds_out(rounds, roster, winners),
           "finish_stats": finish_stats(rounds, roster),
           "finish_source": "host topoutresults.json",
           "points_source": "host topoutresults.json"}
    os.makedirs(P.CUPS_DIR, exist_ok=True)
    path = os.path.join(P.CUPS_DIR, f"bonq_{args.cup}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nJSON: {path}   (cup finish rate {100 * tot_f / tot_p:.1f}%)")

    os.makedirs(HOST_DIR, exist_ok=True)
    dst = os.path.join(HOST_DIR, f"bonq_{args.cup}_topoutresults.json")
    if os.path.abspath(args.host_file) != os.path.abspath(dst):
        shutil.copyfile(args.host_file, dst)
        print(f"Host file archived: {dst}")
    if args.log:
        os.makedirs(P.LOGS_DIR, exist_ok=True)
        arch = os.path.join(P.LOGS_DIR, f"bonq_{args.cup}.log")
        if os.path.abspath(args.log) != os.path.abspath(arch) and not os.path.exists(arch):
            with open(args.log, encoding="utf-8-sig") as src:
                day = [l for l in src if l.startswith(args.date)]
            if day:
                with open(arch, "w", encoding="utf-8", newline="") as dst_f:
                    dst_f.writelines(day)
                print(f"Log archived ({args.date} only): {arch}")
            else:
                print(f"  WARNING: the log has no lines dated {args.date}, not archived")
    print(f"BONQ {args.cup} COMPLETE")
    print("Now run: python build_bonq.py")


if __name__ == "__main__":
    main()
