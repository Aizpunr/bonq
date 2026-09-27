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

The overall ranking uses the same placement points as Kerki: W1 200, W2 140, W3 100, W4 70, W5 40, finalist 10.

**Rounds finished** counts rounds a player finished out of rounds they were in the lobby for. Spectators and AFK players count as in the lobby, so every rate is a lower bound.

## Updating

```
python parse_bonq_from_log.py N --date YYYY-MM-DD --maps "Bonq N -" --nuisance <mapper steamIDs> --header "Bonq Cup #N - Maps by ..." [--host-file host/bonq_N_topoutresults.json] --dry-run
python parse_bonq_from_log.py N ...same flags without --dry-run
python validate_vs_topout.py N host/bonq_N_topoutresults.json   # when the host file exists
python build_bonq.py
```

Local preview: `python -m http.server 9005`, then open http://localhost:9005.
