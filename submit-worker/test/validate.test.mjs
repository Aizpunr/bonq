import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateHostFile, validateLog, validateFields } from '../src/index.js';

const fixture = readFileSync(new URL('../../host/bonq_1_topoutresults.json', import.meta.url));
const host = sid => `{"settings":{},"rounds":[{"level":{"Name":"Track"},"playerResults":[{"steamID":${sid}}]}]}`;
const now = new Date('2026-10-22T12:00:00Z');
const fields = extra => new Map(Object.entries({ cup: '2', date: '2026-10-22', nuisance: '[]', ...extra }));

test('real host fixture passes, including BOM', () => assert.ok(validateHostFile(fixture).data));
test('bare steamID survives exactly', () => assert.equal(validateHostFile(host('76561198842622746')).data.rounds[0].playerResults[0].steamID, '76561198842622746'));
for (const [name, input] of [
  ['non JSON', 'oops'], ['missing rounds', '{"settings":{}}'],
  ['16 digit steamID', host('7656119884262274')], ['oversize host', new Uint8Array(1_000_001)],
  ['missing settings', '{"rounds":[]}'], ['empty rounds', '{"settings":{},"rounds":[]}'],
  ['invalid round', '{"settings":{},"rounds":[null]}'],
]) test(name + ' fails', () => assert.ok(validateHostFile(input).error));
test('valid fields pass', () => assert.equal(validateFields(fields(), now).error, undefined));
for (const [name, changes] of [
  ['flag header', {header:'--log'}], ['flag submitter', {submitter:'--log'}],
  ['raw trailing newline', {header:'name\n'}], ['NUL', {submitter:'a\0b'}],
  ['61 days old', {date:'2026-08-22'}], ['invalid date', {date:'2026-02-30'}],
  ['future date', {date:'2026-10-24'}], ['short nuisance', {nuisance:'["123"]'}],
  ['numeric nuisance', {nuisance:'[76561198842622746]'}], ['long submitter', {submitter:'a'.repeat(41)}],
  ['long header', {header:'a'.repeat(161)}], ['cup 1', {cup:'1'}], ['cup 1000', {cup:'1000'}],
  ['nested preview flag', {preview:'{"winners":["--log"]}'}],
  ['nested preview newline', {preview:JSON.stringify({winner:'a\nb'})}],
]) test(name + ' rejected', () => assert.ok(validateFields(fields(changes), now).error));
test('date boundaries pass', () => {
  for (const date of ['2026-08-23', '2026-10-23']) assert.equal(validateFields(fields({date}), now).error, undefined);
});
test('nuisance strings preserved', () => assert.deepEqual(validateFields(fields({nuisance:'["76561198842622746"]'}), now).nuisance, ['76561198842622746']));
test('log validation', () => {
  assert.equal(validateLog('[LiveLeaderboardLogger] LEADERBOARD|x').error, undefined);
  assert.ok(validateLog('other log').error);
  assert.ok(validateLog(new Uint8Array(2_000_001)).error);
});


test('Worker submit, auth, downloads, dedupe, status and reindex', async () => {
  const { default: worker } = await import('../src/index.js');
  const store = new Map(), metadata = new Map();
  const env = {
    ALLOWED_ORIGINS: 'https://aizpunr.github.io', TURNSTILE_HOSTNAMES: 'aizpunr.github.io',
    TURNSTILE_SECRET: 'test-secret', POLLER_TOKEN: 'test-token',
    SUBMISSIONS: {
      async get(k) { return store.get(k) ?? null; },
      async put(k, v, opts) { store.set(k, v); metadata.set(k, opts?.metadata); },
      async getWithMetadata(k) { return {value:store.get(k) ?? null, metadata:metadata.get(k)}; },
      async list() { return {keys:[...store.keys()].filter(k => k.startsWith('sub:')).map(name => ({name})), list_complete:true}; },
    },
  };
  const call = (path, options = {}, auth = true) => worker.fetch(new Request('https://worker.test' + path, {
    ...options, headers: { ...(auth ? {Authorization:'Bearer test-token'} : {}), ...options.headers },
  }), env);
  assert.equal((await (await call('/')).json()).service, 'bonq-submit');
  assert.equal((await call('/pending', {}, false)).status, 401);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json({success:true, hostname:'aizpunr.github.io'});
  try {
    const upload = async log => {
      const form = new FormData();
      form.set('file', new Blob([fixture]), 'topoutresults.json');
      form.set('cup', '2'); form.set('date', new Date().toISOString().slice(0,10));
      form.set('cf-turnstile-response', 'test-response');
      if (log) form.set('log', '[LiveLeaderboardLogger] LEADERBOARD|x');
      return (await call('/submit', {method:'POST', body:form, headers:{Origin:'https://aizpunr.github.io'}})).json();
    };
    const result = await upload(true);
    assert.equal(result.status, 'received');
    assert.equal((await upload(false)).status, 'duplicate');
    assert.deepEqual(new Uint8Array(await (await call('/file/' + result.id)).arrayBuffer()), new Uint8Array(fixture));
    assert.match(await (await call('/log/' + result.id)).text(), /LiveLeaderboardLogger/);
    assert.equal((await call('/file/' + result.id, {}, false)).status, 401);
    assert.equal((await call('/log/20261022T1200-12345678')).status, 404);
    assert.equal((await (await call('/pending')).json()).submissions.length, 1);
    await call('/status/' + result.id, {method:'POST', body:JSON.stringify({status:'published',note:'done'})});
    assert.equal((await (await call('/pending')).json()).submissions.length, 0);
    store.set('index', '[]');
    assert.equal((await (await call('/reindex', {method:'POST'})).json()).records, 1);
    const publicRows = (await (await call('/status', {}, false)).json()).submissions;
    assert.equal(publicRows[0].status, 'published');
    assert.equal(publicRows[0].note, 'done');
    assert.equal(publicRows[0].has_log, true);
    assert.equal(publicRows[0].size, fixture.length);
  } finally { globalThis.fetch = originalFetch; }
});
