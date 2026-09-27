# Bonq Cup Rankings

Results and rankings for **Bonq Cup**, the MMM's biweekly Zeepkist Top->Out competition (Sundays, on the weeks Kerki doesn't run).

## What's here

| Path | What it is |
|---|---|
| `index.html` | The page: overall ranking and per-cup results, including rounds finished per player and per map |
| `bonq.json` | Built data the page reads |
| `cups/bonq_N.json` | One file per cup: winners, finalists, others, nuisance, per-round results |
| `logs/bonq_N.log` | The cup night's LiveLeaderboardLogger log (source data) |
| `host/bonq_N_topoutresults.json` | The host's Top->Out results file, when available (ground truth) |

## Scoring

Points per round follow the Top->Out mod settings used by the host, checked against the host's results file for Bonq #1:

- Finishing position: 150, 125, 110, 100, 92, 85, 79, 73, 68, 63, 59, 55, 51, 48, 45 (1st to 15th)
- No finish while in the lobby at round end: 20
- 1000 points makes you a finalist. A finalist who then finishes 1st in a later round wins. Five winners per cup.
- Map authors race as nuisance: their points don't count and a nuisance 1st place blocks that round's win.

## Page tabs

The page uses the same ranking systems as the Kerki Comp page:

- **Ranking**: rolling placement points (W1 200, W2 140, W3 100, W4 70, W5 40, finalist 10), best 18 of the last 26 cups.
- **Skill**: a convergent Glicko-style rating that pulls toward a performance value per placement (W1 2100 down to finalist 1700, others 1500), with 2% decay per missed cup.
- **Records**: career wins, placements and finalist appearances, plus **Rounds**: rounds finished out of rounds in the lobby. Spectators and AFK players count as in the lobby, so every rate is a lower bound.
- **Events**: every cup's full standings, rounds finished per map, and a round-by-round table.

Rank arrows compare each ranking with the same build minus the latest cup.

## Updating

```
python parse_bonq_from_log.py N --date YYYY-MM-DD --maps "Bonq N -" --nuisance <mapper steamIDs> --header "Bonq Cup #N - Maps by ..." [--host-file host/bonq_N_topoutresults.json] --dry-run
python parse_bonq_from_log.py N ...same flags without --dry-run
python validate_vs_topout.py N host/bonq_N_topoutresults.json   # when the host file exists
python build_bonq.py
```

Local preview: `python -m http.server 9005`, then open http://localhost:9005.
