/* Browser and Node port of parse_bonq_from_host.py. The Python is the source of truth. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.BonqHostParser = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function stripTag(s) { return (s || '').replace(/^\[[^\]]*\]\s*/, '').trim(); }
  function cmp(a, b) {
    const x = Array.from(a), y = Array.from(b);
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
      if (x[i] !== y[i]) return x[i].codePointAt(0) - y[i].codePointAt(0);
    }
    return x.length - y.length;
  }
  // Python .3f rounds the exact IEEE double. Avoid toFixed's exponent notation.
  function timeString(x) { return x.toFixed(3); }
  function parseHostFile(text, {cup, nuisance, minRounds = 6} = {}) {
    const host = JSON.parse(text.replace(/^\uFEFF/, '').replace(/("steamID"\s*:\s*)(\d+)/g, '$1"$2"'));
    if (!host || !host.settings || !Array.isArray(host.rounds) || !host.rounds.length)
      throw new Error('Not a Top->Out results file with settings and rounds.');
    const roster = new Map(), flagged = new Set();
    const rounds = host.rounds.map(h => {
      const present = new Set(), lb = [];
      for (const p of h.playerResults) {
        const sid = String(p.steamID);
        if (!/^\d{17}$/.test(sid)) throw new Error('Invalid Steam ID in host file.');
        roster.set(sid, stripTag(p.username));
        if (p.nuisance) flagged.add(sid);
        const done = typeof p.time !== 'string';
        if (done) lb.push({sid, value: p.time, time: timeString(p.time)});
        if (done || p.gainedPoints > 0) present.add(sid);
      }
      lb.sort((a,b) => a.value - b.value || cmp(a.sid,b.sid));
      return {map: h.level.Name, author: h.level.Author || '', present, lb};
    });
    const nuis = new Set(nuisance === undefined ? flagged : nuisance);
    const won = new Map(), qual = new Map(), wpos = new Map();
    const totals = new Map(), pts = new Map(), present = new Map(), finished = new Map();
    const inc = (m,s,v) => m.set(s,(m.get(s) || 0)+v);
    host.rounds.forEach((h,i) => {
      for (const p of h.playerResults) {
        const s = String(p.steamID);
        if ((p.winnerPosition ?? -1) >= 0 && !won.has(s)) { won.set(s,i+1); wpos.set(s,p.winnerPosition); }
        if (p.finalist && !qual.has(s)) qual.set(s,i+1);
        if (nuis.has(s)) inc(pts,s,p.gainedPoints);
        totals.set(s,p.pointsTotal);
      }
      rounds[i].present.forEach(s => inc(present,s,1));
      rounds[i].lb.forEach(e => inc(finished,e.sid,1));
    });
    const name = s => roster.get(s) ?? '<'+s+'>';
    const winners = [...won.keys()].sort((a,b) => wpos.get(a)-wpos.get(b) || won.get(a)-won.get(b)).map((s,i) => {
      const r = rounds[won.get(s)-1];
      return {rank:i+1,sid:s,name:name(s),won_scoring_round:won.get(s),qualified_scoring_round:qual.get(s) ?? null,map:r.map,time:r.lb.find(e=>e.sid===s)?.time ?? '?'};
    });
    const fin = [...qual.keys()].filter(s => !won.has(s) && !nuis.has(s) && qual.get(s)<rounds.length);
    const finalists = fin.sort((a,b)=>qual.get(a)-qual.get(b)||cmp(name(a).toLowerCase(),name(b).toLowerCase())).map((s,i)=>({rank:i+1,sid:s,name:name(s),qualified_scoring_round:qual.get(s)}));
    const everyone = new Set([...present.keys(),...totals.keys()]), rest = [], excluded = [];
    everyone.forEach(s => {
      if (won.has(s)||fin.includes(s)||nuis.has(s)) return;
      ((present.get(s)||0)>=minRounds && (finished.get(s)||0)>=1 ? rest : excluded).push(s);
    });
    const stats = (s,points) => ({sid:s,name:name(s),points,rounds_played:present.get(s)||0,finishes:finished.get(s)||0});
    const others = rest.sort((a,b)=>(totals.get(b)||0)-(totals.get(a)||0)||cmp(name(a).toLowerCase(),name(b).toLowerCase())).map((s,i)=>({rank:i+1,...stats(s,totals.get(s)||0)}));
    const nuisanceOut = [...nuis].sort((a,b)=>(pts.get(b)||0)-(pts.get(a)||0)).filter(s=>everyone.has(s)).map(s=>stats(s,pts.get(s)||0));
    const excludedOut = excluded.sort((a,b)=>(present.get(b)||0)-(present.get(a)||0)).map(s=>stats(s,totals.get(s)||0));
    const rotation = [], authors = [];
    rounds.forEach(r=>{ if (!rotation.includes(r.map)) { rotation.push(r.map); authors.push(stripTag(r.author)); } });
    const mappers = [...new Set(authors.filter(Boolean))], warnings = [];
    const settings = host.settings;
    if (settings.threshold !== 1000 || settings.winners !== 5) warnings.push(`mod settings threshold=${settings.threshold} winners=${settings.winners} (Bonq is 1000 / 5)`);
    const untagged = [...new Set(rounds.filter(r=>{ const m = /\bbonq (?:cup )?(\d+) -/i.exec(r.map); return !m || Number(m[1]) !== Number(cup); }).map(r=>r.map))].sort(cmp);
    if (untagged.length) warnings.push(`maps not tagged 'Bonq ${cup} -': ${JSON.stringify(untagged)}`);
    if (winners.length < 5) warnings.push(`only ${winners.length} winners (tournament not finished, or an old file?)`);
    if (rotation.length !== 5) warnings.push(`expected 5 maps, found ${rotation.length}`);
    return {settings, rounds:rounds.map((r,i)=>({n:i+1,map:r.map,author:r.author,finishers:r.lb.length,present:r.present.size})),rotation,mappers,players:[...roster].map(([sid,name])=>({sid,name,flaggedNuisance:flagged.has(sid)})),winners,finalists,others,nuisance:nuisanceOut,excluded:excludedOut,warnings};
  }
  function trimLog(text,dateStr) { return text.replace(/^\uFEFF/,'').split(/\r\n|\r|\n/).filter(l=>l.startsWith(dateStr)).join('\n'); }
  return {parseHostFile,trimLog};
});
