// bonq-submit: Cloudflare Worker that receives Bonq host results from submit.html
// and hands them to the operator's PC (submissions_poll.py) for processing.
//
// Storage is Workers KV (binding SUBMISSIONS):
//   file:<id>     raw uploaded host bytes
//   log:<id>      optional log bytes
//   sub:<id>      submission record (JSON), the authoritative copy
//   sha:<sha256>  id of the submission with that exact host file (dedupe)
//   index         JSON array of records, newest first, capped at MAX_INDEX
//
// Public:  POST /submit, GET /status, GET /
// Poller:  GET /pending, GET /file/<id>, GET /log/<id>, POST /status/<id>, POST /reindex
//          (Authorization: Bearer <POLLER_TOKEN>)
//
// See README.md for setup and the endpoint reference.

const COMP = 'bonq';
const MAX_FILE_BYTES = 1_000_000;
const MAX_LOG_BYTES = 2_000_000;
const STATUSES = ['received', 'processed', 'failed', 'published', 'duplicate', 'superseded'];
const ID_RE = /^\d{8}T\d{4}-[0-9a-f]{8}$/;
const STATUS_LIMIT = 50;

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin');
    const cors = corsHeaders(origin, env);
    try {
      return await route(request, env, cors, origin);
    } catch (e) {
      console.error('internal error', e && e.stack ? e.stack : e);
      return err(cors, 500, 'internal', 'Something went wrong on the server. Try again later.');
    }
  },
};

async function route(request, env, cors, origin) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, '') || '/';
  const method = request.method.toUpperCase();

  if (method === 'OPTIONS') {
    if (origin && !cors['Access-Control-Allow-Origin']) {
      return err({}, 403, 'bad_origin', 'This origin may not call the submit service.');
    }
    return new Response(null, { status: 204, headers: cors });
  }

  if (method === 'GET' && path === '/') {
    return json(cors, 200, { ok: true, service: COMP + '-submit' });
  }
  if (method === 'POST' && path === '/submit') return submit(request, env, cors, origin);
  if (method === 'GET' && path === '/status') return publicStatus(env, cors);

  // Everything below is for the poller only.
  if (method === 'GET' && path === '/pending') {
    const denied = await authorize(request, env, cors);
    if (denied) return denied;
    const index = await readIndex(env);
    return json(cors, 200, { ok: true, submissions: index.filter(r => r.status === 'received' || r.status === 'processed') });
  }
  let m = path.match(/^\/(file|log)\/([^/]+)$/);
  if (method === 'GET' && m) {
    const denied = await authorize(request, env, cors);
    if (denied) return denied;
    if (!ID_RE.test(m[2])) return err(cors, 404, 'not_found', 'No such submission.');
    const got = await env.SUBMISSIONS.getWithMetadata(m[1] + ':' + m[2], 'arrayBuffer');
    if (!got || got.value === null) return err(cors, 404, 'not_found', 'No such submission.');
    const headers = { ...cors, 'Content-Type': 'application/octet-stream', 'Cache-Control': 'no-store' };
    if (got.metadata && got.metadata.sha256) headers['X-Sha256'] = got.metadata.sha256;
    return new Response(got.value, { status: 200, headers });
  }
  m = path.match(/^\/status\/([^/]+)$/);
  if (method === 'POST' && m) {
    const denied = await authorize(request, env, cors);
    if (denied) return denied;
    return updateStatus(request, env, cors, m[1]);
  }
  if (method === 'POST' && path === '/reindex') {
    const denied = await authorize(request, env, cors);
    if (denied) return denied;
    const n = await reindex(env);
    return json(cors, 200, { ok: true, records: n });
  }
  return err(cors, 404, 'not_found', 'Unknown endpoint.');
}

// ── POST /submit ──────────────────────────────────────────────────────

async function submit(request, env, cors, origin) {
  if (!origin || !cors['Access-Control-Allow-Origin']) {
    return err(cors, 403, 'bad_origin', 'Submissions are only accepted from the Bonq site.');
  }
  const declared = Number(request.headers.get('Content-Length') || 0);
  if (declared > MAX_FILE_BYTES + MAX_LOG_BYTES + 64 * 1024) {
    return err(cors, 413, 'too_large', 'The upload is too big.');
  }
  let form;
  try { form = await request.formData(); }
  catch (e) { return err(cors, 400, 'bad_multipart', 'The upload could not be read.'); }
  const file = form.get('file');
  if (!file || typeof file === 'string' || typeof file.arrayBuffer !== 'function') {
    return err(cors, 400, 'missing_file', 'Attach topoutresults.json.');
  }
  if (file.size < 1 || file.size > MAX_FILE_BYTES) {
    return err(cors, 413, 'too_large', 'The host file must be between 1 and 1,000,000 bytes.');
  }
  const bytes = await file.arrayBuffer();
  const checked = validateHostFile(bytes);
  if (checked.error) return err(cors, 422, 'bad_host_file', checked.error);
  let logBytes = null;
  const log = form.get('log');
  if (log !== null) {
    if (typeof log === 'string') {
      logBytes = new TextEncoder().encode(log);
    } else if (typeof log.arrayBuffer === 'function') {
      if (log.size > MAX_LOG_BYTES) return err(cors, 413, 'too_large', 'The log exceeds 2,000,000 bytes.');
      logBytes = await log.arrayBuffer();
    } else return err(cors, 400, 'bad_log', 'The log could not be read.');
    const checkedLog = validateLog(logBytes);
    if (checkedLog.error) return err(cors, 422, 'bad_log', checkedLog.error);
  }

  const fields = validateFields(form);
  if (fields.error) return err(cors, 400, 'bad_field', fields.error, { field: fields.field });

  const ts = await verifyTurnstile(env, form.get('cf-turnstile-response'), request.headers.get('CF-Connecting-IP'));
  if (!ts.ok) {
    return err(cors, 403, 'turnstile_failed', 'The anti-bot check failed. Complete it again and resubmit.', { codes: ts.codes });
  }

  const index = await readIndex(env);
  const cap = intVar(env.DAILY_SUBMIT_CAP, 20);
  const dayAgo = Date.now() - 24 * 3600 * 1000;
  if (index.filter(r => Date.parse(r.received_at) > dayAgo).length >= cap) {
    return err(cors, 429, 'rate_limited', 'Too many submissions in the last 24 hours. Try again tomorrow.');
  }

  const sha256 = await sha256Hex(bytes);
  const existing = await env.SUBMISSIONS.get('sha:' + sha256);
  const first = existing ? await readJson(env, 'sub:' + existing) : null;

  const now = new Date().toISOString();
  const id = makeId();
  const rec = {
    id,
    cup: fields.cup,
    date: fields.date,
    nuisance: fields.nuisance,
    header: fields.header,
    submitter: fields.submitter,
    sha: sha256,
    has_log: logBytes !== null,
    size: bytes.byteLength,
    received_at: now,
    status: 'received',
    preview: fields.preview,
    note: '',
  };

  if (existing) {
    rec.status = 'duplicate';
    rec.same_file_as = existing;
    rec.note = first && first.cup !== rec.cup
      ? `REVIEW: same file as ${existing}, which was sent as Bonq #${first.cup}`
      : `same file as ${existing}`;
    await env.SUBMISSIONS.put('sub:' + id, JSON.stringify(rec));
    await writeIndexWith(env, rec);
    return json(cors, 200, { ok: true, id, status: 'duplicate', same_file_as: existing, message: 'This exact host file was already sent. It is kept on record.' });
  }

  await env.SUBMISSIONS.put('file:' + id, bytes, { metadata: { cup: rec.cup, sha256, size: rec.size } });
  if (logBytes !== null) await env.SUBMISSIONS.put('log:' + id, logBytes);
  await env.SUBMISSIONS.put('sub:' + id, JSON.stringify(rec));
  await env.SUBMISSIONS.put('sha:' + sha256, id);
  await writeIndexWith(env, rec);

  return json(cors, 200, { ok: true, id, status: 'received' });
}

export function validateHostFile(input) {
  const bytes = typeof input === 'string' ? new TextEncoder().encode(input) : input;
  if (!bytes || bytes.byteLength < 1 || bytes.byteLength > MAX_FILE_BYTES) return { error: 'Host file must be between 1 and 1,000,000 bytes.' };
  let data;
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes).replace(/^\uFEFF/, '');
    data = JSON.parse(text.replace(/("steamID"\s*:\s*)(\d+)/g, '$1"$2"'));
  } catch (e) { return { error: 'Host file must contain valid UTF-8 JSON.' }; }
  const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
  if (!object(data) || !object(data.settings)) return { error: 'Host file needs a settings object.' };
  if (!Array.isArray(data.rounds) || !data.rounds.length) return { error: 'Host file needs a non-empty rounds array.' };
  for (const round of data.rounds) {
    if (!object(round) || !object(round.level) || typeof round.level.Name !== 'string' || !Array.isArray(round.playerResults)) return { error: 'Every round needs level.Name and a playerResults array.' };
    for (const player of round.playerResults) {
      if (!object(player) || typeof player.steamID !== 'string' || !/^\d{17}$/.test(player.steamID)) return { error: 'Every steamID must contain exactly 17 digits.' };
    }
  }
  return { data };
}

export function validateLog(input) {
  const bytes = typeof input === 'string' ? new TextEncoder().encode(input) : input;
  if (!bytes || bytes.byteLength > MAX_LOG_BYTES) return { error: 'Log must be at most 2,000,000 bytes.' };
  const text = new TextDecoder().decode(bytes);
  if (!text.includes('[LiveLeaderboardLogger]')) return { error: 'Log must contain [LiveLeaderboardLogger].' };
  return { text };
}

export function validateFields(form, now = new Date()) {
  const unsafe = v => v.trimStart().startsWith('--') || /[\r\n\0]/.test(v);
  // Validate raw metadata before trim, including nested preview strings.
  for (const [key, value] of form.entries()) {
    if (key === 'file' || key === 'log') continue;
    if (typeof value !== 'string' || unsafe(value)) return bad(key, 'Field contains a forbidden flag, line break or NUL.');
  }
  const str = k => typeof form.get(k) === 'string' ? form.get(k).trim() : '';
  const cup = Number(str('cup'));
  if (!Number.isInteger(cup) || cup < 2 || cup > 999) return bad('cup', 'Cup number must be a whole number between 2 and 999.');
  const date = str('date');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return bad('date', 'Date must be YYYY-MM-DD.');
  const dt = Date.parse(date + 'T00:00:00Z');
  if (Number.isNaN(dt) || new Date(dt).toISOString().slice(0, 10) !== date) return bad('date', 'That date does not exist.');
  const today = Date.parse(now.toISOString().slice(0, 10) + 'T00:00:00Z');
  if (dt < today - 60 * 86400000 || dt > today + 86400000) return bad('date', 'Date must be within the last 60 days and no later than tomorrow.');
  let nuisance;
  try { nuisance = JSON.parse(str('nuisance') || '[]'); }
  catch (e) { return bad('nuisance', 'Nuisance must be a JSON array.'); }
  if (!Array.isArray(nuisance) || nuisance.length > 20 || nuisance.some(sid => typeof sid !== 'string' || !/^\d{17}$/.test(sid))) return bad('nuisance', 'At most 20 nuisance steamIDs, each a 17-digit string.');
  const header = str('header'), submitter = str('submitter');
  if (form.get('header')?.length > 160) return bad('header', 'Header must be at most 160 characters.');
  if (form.get('submitter')?.length > 40) return bad('submitter', 'Submitter must be at most 40 characters.');
  let preview = null;
  const rawPreview = str('preview');
  if (rawPreview) {
    if (rawPreview.length > 2048) return bad('preview', 'Preview must be at most 2048 characters.');
    try { preview = JSON.parse(rawPreview); } catch (e) { return bad('preview', 'Preview must be valid JSON.'); }
    const safe = v => typeof v === 'string' ? !unsafe(v) : v && typeof v === 'object' ? Object.entries(v).every(([k, x]) => !unsafe(k) && safe(x)) : true;
    if (!safe(preview)) return bad('preview', 'Preview contains a forbidden string.');
  }
  return { cup, date, nuisance, header, submitter, preview };
}

function bad(field, message) {
  return { error: message, field };
}

async function verifyTurnstile(env, token, ip) {
  // Trimmed so a secret stored with a stray newline or space still works.
  const secret = (env.TURNSTILE_SECRET || '').trim();
  if (!secret) return { ok: false, codes: ['missing-secret'] };
  if (typeof token !== 'string' || !token) return { ok: false, codes: ['missing-input-response'] };
  const body = new URLSearchParams({ secret, response: token });
  if (ip) body.set('remoteip', ip);
  let out;
  try {
    const r = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', { method: 'POST', body });
    out = await r.json();
  } catch (e) {
    return { ok: false, codes: ['siteverify-unreachable'] };
  }
  if (!out || out.success !== true) return { ok: false, codes: (out && out['error-codes']) || [] };
  const allowed = csv(env.TURNSTILE_HOSTNAMES);
  if (allowed.length && !allowed.includes(out.hostname)) return { ok: false, codes: ['hostname-mismatch'] };
  return { ok: true };
}

// ── GET /status ───────────────────────────────────────────────────────

async function publicStatus(env, cors) {
  const index = await readIndex(env);
  const rows = index.slice(0, STATUS_LIMIT);
  return json({ ...cors, 'Cache-Control': 'no-store' }, 200, { ok: true, submissions: rows });
}

// ── POST /status/<id> ─────────────────────────────────────────────────

async function updateStatus(request, env, cors, id) {
  if (!ID_RE.test(id)) return err(cors, 404, 'not_found', 'No such submission.');
  let body;
  try { body = await request.json(); } catch (e) { return err(cors, 400, 'bad_json', 'Body must be JSON.'); }
  if (!body || !STATUSES.includes(body.status)) {
    return err(cors, 400, 'bad_status', 'status must be one of ' + STATUSES.join(', ') + '.');
  }
  const raw = await env.SUBMISSIONS.get('sub:' + id);
  if (!raw) return err(cors, 404, 'not_found', 'No such submission.');
  const rec = JSON.parse(raw);
  rec.status = body.status;
  if (typeof body.note === 'string') rec.note = body.note.slice(0, 500);
  await env.SUBMISSIONS.put('sub:' + id, JSON.stringify(rec));
  await writeIndexWith(env, rec);
  return json(cors, 200, { ok: true, id, status: rec.status });
}

// ── index helpers ─────────────────────────────────────────────────────
// The index is read-modify-written, so two writes landing together can drop
// one record from it. sub:<id> stays authoritative: /status/<id> re-inserts a
// missing record and /reindex rebuilds the whole index from sub: keys.

async function readJson(env, key) {
  const raw = await env.SUBMISSIONS.get(key);
  return raw ? JSON.parse(raw) : null;
}

async function readIndex(env) {
  const raw = await env.SUBMISSIONS.get('index');
  if (!raw) return [];
  try {
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v : [];
  } catch (e) {
    return [];
  }
}

async function writeIndexWith(env, rec) {
  const index = (await readIndex(env)).filter(r => r.id !== rec.id);
  index.push(rec);
  index.sort((a, b) => (a.received_at < b.received_at ? 1 : a.received_at > b.received_at ? -1 : 0));
  const cap = intVar(env.MAX_INDEX, 200);
  await env.SUBMISSIONS.put('index', JSON.stringify(index.slice(0, cap)));
}

async function reindex(env) {
  const records = [];
  let cursor;
  do {
    const page = await env.SUBMISSIONS.list({ prefix: 'sub:', cursor });
    for (const k of page.keys) {
      const raw = await env.SUBMISSIONS.get(k.name);
      if (raw) {
        try { records.push(JSON.parse(raw)); } catch (e) { /* skip a corrupt record */ }
      }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  records.sort((a, b) => (a.received_at < b.received_at ? 1 : a.received_at > b.received_at ? -1 : 0));
  await env.SUBMISSIONS.put('index', JSON.stringify(records.slice(0, intVar(env.MAX_INDEX, 200))));
  return records.length;
}

// ── auth, CORS, responses ─────────────────────────────────────────────

async function authorize(request, env, cors) {
  const h = request.headers.get('Authorization') || '';
  const m = h.match(/^Bearer (.+)$/);
  // Trimmed like TURNSTILE_SECRET: a secret stored with a stray line break must still match.
  const token = (env.POLLER_TOKEN || '').trim();
  if (!token || !m || !(await safeEqual(m[1].trim(), token))) {
    return err(cors, 401, 'unauthorized', 'Missing or wrong poller token.');
  }
  return null;
}

async function safeEqual(a, b) {
  const enc = new TextEncoder();
  // Hash both sides first so the comparison length never depends on input.
  const [ha, hb] = await Promise.all([
    crypto.subtle.digest('SHA-256', enc.encode(a)),
    crypto.subtle.digest('SHA-256', enc.encode(b)),
  ]);
  if (crypto.subtle.timingSafeEqual) return crypto.subtle.timingSafeEqual(ha, hb);
  const x = new Uint8Array(ha), y = new Uint8Array(hb);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

function corsHeaders(origin, env) {
  const h = { Vary: 'Origin' };
  if (origin && csv(env.ALLOWED_ORIGINS).includes(origin)) {
    h['Access-Control-Allow-Origin'] = origin;
    h['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS';
    h['Access-Control-Allow-Headers'] = 'Content-Type, Authorization';
    h['Access-Control-Max-Age'] = '86400';
  }
  return h;
}

function json(headers, status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...headers, 'Content-Type': 'application/json; charset=utf-8' },
  });
}

function err(headers, status, code, message, details) {
  const body = { ok: false, error: code, message };
  if (details) body.details = details;
  return json(headers, status, body);
}

// ── small utilities ───────────────────────────────────────────────────

function csv(v) {
  return String(v || '').split(',').map(s => s.trim()).filter(Boolean);
}

function intVar(v, dflt) {
  const n = parseInt(v, 10);
  return Number.isFinite(n) && n > 0 ? n : dflt;
}

function hex(buf) {
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
}

async function sha256Hex(bytes) {
  return hex(await crypto.subtle.digest('SHA-256', bytes));
}

function makeId() {
  const t = new Date().toISOString(); // 2026-09-27T21:01:33.123Z
  const stamp = t.slice(0, 4) + t.slice(5, 7) + t.slice(8, 10) + 'T' + t.slice(11, 13) + t.slice(14, 16);
  return stamp + '-' + hex(crypto.getRandomValues(new Uint8Array(4)));
}
