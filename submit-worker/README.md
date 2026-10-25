# bonq-submit Worker

Receives host Top->Out results from `submit.html` for `submissions_poll.py`, which runs `parse_bonq_from_host.py` and `build_bonq.py`. GitHub Actions processes submissions into pull requests for review and publication.

## One-time setup

1. In this folder run `npm install`, then `npx wrangler login`.
2. Create namespaces and paste the resulting ids into `wrangler.toml`:
   ```powershell
   npx wrangler kv namespace create BONQ_SUBMISSIONS
   npx wrangler kv namespace create BONQ_SUBMISSIONS --preview
   ```
3. Reuse the existing COTD Turnstile widget (its hostnames `aizpunr.github.io` and `localhost` cover this page). Its public site key is already in `submit.html` (`PROD_TURNSTILE_SITE_KEY`). Store its secret as `TURNSTILE_SECRET`, either in the dashboard (Workers & Pages, bonq-submit, Settings, Variables and Secrets, type Secret) or from Python. Do NOT pipe it from PowerShell (`Get-Clipboard | npx wrangler ...`): PowerShell 5 appends a line break that becomes part of the secret (TyO, 2026-10-22, every poller call got 401). The Worker trims both secrets as a safety net.
4. Generate a NEW poller token (never reuse another comp's), store it as `POLLER_TOKEN` the same way, and keep a copy for the GitHub secret. Done 2026-10-25: the copy is in `C:\Users\rafa\.claude\local\bonq_poller_token.txt`.
5. Run `npx wrangler deploy`. Put the printed URL into `submit.html` (`PROD_WORKER_URL`).
6. In GitHub Settings > Secrets and variables > Actions add `BONQ_WORKER_URL` (the Worker URL) and `BONQ_POLLER_TOKEN` (the new token).
7. In Settings > Actions > General > Workflow permissions select **Read and write permissions** and tick **Allow GitHub Actions to create and approve pull requests**.

Never commit secrets. To rotate the poller token, generate a new one, update the Worker secret and update `BONQ_POLLER_TOKEN`.

## Local development

Create a gitignored `.dev.vars` in this folder:

```dotenv
TURNSTILE_SECRET=1x0000000000000000000000000000000AA
POLLER_TOKEN=devtoken
TURNSTILE_HOSTNAMES=example.com,localhost,127.0.0.1
```

This is Cloudflare's public always-pass test secret. Its siteverify answer reports the hostname `example.com`, so the local allowlist must include it or every local submission fails with `hostname-mismatch`. Use the matching public test site key in the local submit page. Run `npx wrangler dev` (local KV), and serve the site on port 9005. The local submit page should target `http://127.0.0.1:8787`.

Run `npm test` for Node's built-in tests, or `npx wrangler tail` for live Worker logs.

## Storage and API

KV binding: `SUBMISSIONS`.

| Key | Value |
|---|---|
| `file:<id>` | Original host file bytes; metadata: cup, sha256, size. |
| `log:<id>` | Optional trimmed LiveLeaderboardLogger log. |
| `sub:<id>` | Authoritative submission JSON. |
| `sha:<sha256>` | Submission id for host-file deduplication. |
| `index` | Newest records first, capped at MAX_INDEX (200). |

Record: `id, cup, date, nuisance, header, submitter, sha, has_log, size, received_at, status, note, preview`. No IP, user agent or Turnstile data is stored. IDs contain the UTC minute and eight random hex digits.

| Endpoint | Access and behavior |
|---|---|
| `GET /` | Public health check, service `bonq-submit`. |
| `POST /submit` | Allowed Origin plus Turnstile. Multipart `file`, optional `log`, `cup`, `date`, `nuisance` (JSON array), optional `header`, `submitter`, `preview` (JSON), `cf-turnstile-response`. |
| `GET /status` | Public last 50 records, no file contents. |
| `GET /pending` | Poller: received and processed records. |
| `GET /file/<id>` | Poller: original host bytes, X-Sha256 header. |
| `GET /log/<id>` | Poller: optional log bytes, 404 if absent. |
| `POST /status/<id>` | Poller: JSON with status and optional note (up to 500 characters). |
| `POST /reindex` | Poller: rebuild index from authoritative records. |

Poller endpoints require `Authorization: Bearer <POLLER_TOKEN>`, compared using hashes and constant-time comparison. Responses are JSON except file/log downloads and OPTIONS. Errors have `ok: false`, `error`, `message` and optional `details`.

Host files must be 1 to 1,000,000 bytes, valid JSON with a settings object, non-empty rounds, level.Name strings, playerResults arrays and 17-digit steamIDs. BOMs are stripped and bare steamIDs are quoted before parsing to preserve precision. Optional logs must be at most 2,000,000 bytes and contain `[LiveLeaderboardLogger]`; the browser trims them to cup-date lines before upload.

Cup numbers are integers 2 to 999. Dates must exist, be no older than 60 days and no later than tomorrow (UTC). Nuisance lists allow at most 20 steamID strings. Header and submitter limits are 160 and 40 characters. Metadata strings cannot start with `--` or contain CR, LF or NUL. Preview JSON is limited to 2048 characters and checked recursively for unsafe strings.

The daily cap defaults to 20 submissions per rolling 24 hours. Identical host bytes return the original id and `duplicate`, without storing another record. Statuses are `received`, `processed`, `failed`, `published`, `duplicate`, `superseded`.

## Known limitation: the index

The index is read, modified and written back. Concurrent writes can drop a record from the index; authoritative `sub:` records remain intact. Updating a submission's status reinserts it, and `/reindex` rebuilds the index from all records. The daily cap also relies on this index. Workers KV is eventually consistent, so deduplication and caps are best effort during concurrent submissions. A Durable Object would be needed for strict serialization.
