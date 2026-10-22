# Submitting a Bonq Cup

Usually the host sends the results, but anyone who has the file can help. This is a community effort.

Open [Submit a cup](https://aizpunr.github.io/bonq/submit.html).

## 1. Get the host results

On the host's PC, find:

```
%APPDATA%\Zeepkist\Mods\TopOutTournament\topoutresults.json
```

Copy and send it the same night, before the next tournament overwrites it. Send `topoutresults.json`, not `topoutresults_bckp.json` unless asked.

Optionally, include `BepInEx\LiveLeaderboardLogger.log` from the Zeepkist game folder. The browser keeps only lines starting with the chosen cup date before uploading. A full log can be large; you do not need to trim it yourself. If no lines match, check the date or remove the optional log.

## 2. Check the preview

The page reads the file in your browser. Nothing is uploaded until you press Submit.

Check the five winners, their maps and times, finalists, top 10 Others, round count, map authors and warnings.

- **Cup number:** defaults to the next cup on the site. Check it.
- **Date:** defaults to the most recent Sunday, including today if Sunday. Select the actual cup date.
- **Nuisance players:** the host's flags are preselected. Check the list, using the map authors as a hint.
- **Header:** filled as `Bonq Cup #N - Maps by A, B, C`. You can edit it.
- **Your name:** optional, so we know who to thank.

## 3. Send it

Complete the anti-bot check and press Submit. An automatic check opens a review. Submissions appear on the site once approved.

Sending a cup again is fine: every file is kept on record, compared with the published result, and anything that differs is flagged for review.

The Recent submissions table shows progress. Submissions sent from this browser are marked as yours.

| Status | Meaning |
|---|---|
| received | Waiting for the automatic check. |
| processed | A review is open, waiting for approval. |
| published | Live on the site. |
| duplicate | Kept on record and checked against the published cup. REVIEW means it differs. |
| superseded | Another submission was chosen for this cup. |
| failed | The check stopped. Read the note and contact aizpun. |

## Privacy

The host file contains Steam IDs and in-game names, which the site already publishes per cup. The optional trimmed log contains only the cup day, selected in your browser before upload. No IP address is stored with a submission.
