"""Check a parsed Bonq cup against the host's Top->Out results file.

Usage:
    python validate_vs_topout.py 1 "C:/Users/rafa/Downloads/topoutresults.json" [--date 2026-09-27]

The host file is %APPDATA%\\Zeepkist\\Mods\\TopOutTournament\\topoutresults.json on
the HOST's machine (topoutresults_bckp.json once they end the tournament). It
holds the mod's own per-round gainedPoints / finalist / nuisance / winnerPosition,
so it is ground truth for the rules; our log reconstruction must reproduce it.
"""
import argparse
import json
import sys

import parse_bonq_from_log as P


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("cup", type=int)
    ap.add_argument("host_file")
    ap.add_argument("--date", default=None)
    ap.add_argument("--log", default=None, help="default: logs/bonq_N.log")
    args = ap.parse_args()

    with open(args.host_file, encoding="utf-8-sig") as f:
        host = json.load(f)
    with open(f"{P.CUPS_DIR}/bonq_{args.cup}.json", encoding="utf-8") as f:
        ours = json.load(f)

    log = args.log or f"{P.LOGS_DIR}/bonq_{args.cup}.log"
    rounds, roster, _ = P.parse_log(log, restrict_date=args.date or ours["date"])
    selected = P.select_rounds(rounds, [f"Bonq {args.cup} -"])
    scoring = [r for r in selected if r["last_lb"]]
    nxt = {id(r): (selected[i + 1]["roster"] if i + 1 < len(selected) else r["roster"])
           for i, r in enumerate(selected)}
    hr = host["rounds"]
    print(f"settings: {host['settings']}")
    print(f"rounds: host {len(hr)}  ours {len(scoring)}")
    if len(hr) != len(scoring):
        print("  !! round count differs, per-round comparison is misaligned")

    mism = 0
    for i, (h, r) in enumerate(zip(hr, scoring), start=1):
        if h["level"]["Name"] != r["map"]:
            print(f"  S{i}: map differs: host {h['level']['Name']!r} vs log {r['map']!r}")
        ours_gain = {}
        lb = sorted(r["last_lb"], key=lambda x: x["pos"])
        for rank0, e in enumerate(lb):
            ours_gain[e["sid"]] = P.points_for_pos(rank0 + 1)
        for sid in nxt[id(r)]:
            ours_gain.setdefault(sid, P.POINTS["dnf"])
        host_gain = {str(p["steamID"]): p["gainedPoints"] for p in h["playerResults"]}
        for sid in sorted(set(host_gain) | set(ours_gain)):
            a, b = host_gain.get(sid), ours_gain.get(sid)
            if a != b and not (a == 0 and b is None):   # host keeps leavers listed at 0
                mism += 1
                name = roster.get(sid) or next((p["username"] for p in h["playerResults"]
                                                if str(p["steamID"]) == sid), sid)
                print(f"  S{i:<2} {name:24s} host {a!s:>4}  ours {b!s:>4}")
    print(f"per-round gained-points mismatches: {mism}")

    # Final categories from the last host round.
    last = hr[-1]["playerResults"]
    h_w = [str(p["steamID"]) for p in sorted((p for p in last if p["winnerPosition"] >= 0),
                                              key=lambda p: p["winnerPosition"])]
    h_f = {str(p["steamID"]) for p in last if p["finalist"] and p["winnerPosition"] < 0}
    h_n = {str(p["steamID"]) for p in last if p["nuisance"]}
    o_w = [w["sid"] for w in ours["winners"]]
    o_f = {f["sid"] for f in ours["finalists"]}
    o_n = {n["sid"] for n in ours["nuisance"]}
    print(f"winners (in order) match:  {h_w == o_w}")
    if h_w != o_w:
        print(f"   host {[roster.get(s, s) for s in h_w]}\n   ours {[roster.get(s, s) for s in o_w]}")
    print(f"finalists match:           {h_f == o_f}")
    if h_f != o_f:
        print(f"   host-only {[roster.get(s, s) for s in h_f - o_f]}  ours-only {[roster.get(s, s) for s in o_f - h_f]}")
    print(f"nuisance match:            {h_n == o_n}")
    # Others: host pointsTotal vs ours (host keeps accumulating nothing past the cap).
    h_tot = {str(p["steamID"]): p["pointsTotal"] for p in last}
    diffs = [(o["name"], h_tot.get(o["sid"]), o["points"]) for o in ours["others"]
             if h_tot.get(o["sid"]) != o["points"]]
    print(f"others' final points match: {not diffs}")
    for n, a, b in diffs:
        print(f"   {n:24s} host {a}  ours {b}")


if __name__ == "__main__":
    main()
