"""Poll Bonq submissions. PAUSE in the repo suspends processing; --ci opens PRs."""
import argparse
import base64
import datetime
import hashlib
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from urllib.parse import quote
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
NEW_CUP_TIMEOUT_S = 1800

log = logging.getLogger('submissions_poll')


# ── config and paths ─────────────────────────────────────────────────

class Ctx:
    def __init__(self, args):
        cfg_path = os.path.join(HERE, 'submit_config.json')
        cfg = {}
        if not args.ci and os.path.exists(cfg_path):
            with open(cfg_path, encoding='utf-8') as f:
                cfg = json.load(f)
        self.ci = args.ci
        self.worker = (args.worker or os.environ.get('BONQ_WORKER_URL') or cfg.get('worker_url') or '').rstrip('/')
        self.token = os.environ.get('BONQ_POLLER_TOKEN') or cfg.get('poller_token') or ''
        self.repo = os.path.abspath(args.repo or cfg.get('repo_dir') or HERE)
        self.python = cfg.get('python') or sys.executable
        # pythonw.exe (Task Scheduler) has no console; ingestion needs a real
        # interpreter only for its own children, and python.exe works either way.
        if os.path.basename(self.python).lower() == 'pythonw.exe':
            self.python = os.path.join(os.path.dirname(self.python), 'python.exe')
        self.toast = cfg.get('toast', True) and not args.ci and os.name == 'nt'
        self.remote = cfg.get('git_remote', 'origin')
        self.branch = cfg.get('git_branch', 'main')
        self.dry_run = args.dry_run
        self.no_git = args.no_git
        self.only_id = args.id
        self.rehearsal = args.rehearsal or args.ci
        self.exit_code = 0
        self.subdir = self.repo
        os.makedirs(self.subdir, exist_ok=True)
        self.state_path = os.path.join(self.subdir, '.submissions_state.json')
        self.state = load_json(self.state_path, {})

    def save_state(self):
        if self.dry_run:
            return
        tmp = self.state_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def setup_logging(subdir, verbose):
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s', '%Y-%m-%d %H:%M:%S')
    fh = logging.handlers.RotatingFileHandler(os.path.join(subdir, 'poll.log'), maxBytes=1_000_000,
                                              backupCount=3, encoding='utf-8')
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.addHandler(fh)
    if sys.stdout is not None:  # None under pythonw.exe
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        sh.setLevel(logging.DEBUG if verbose else logging.INFO)
        log.addHandler(sh)


# ── lock ─────────────────────────────────────────────────────────────

def acquire_lock(path):
    handle = open(path, 'a+b')
    handle.seek(0)
    if not handle.read(1):
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


class WorkerError(Exception):
    pass


def _ssl_context():
    """Prefer certifi's CA bundle: this PC's Windows store only has an expired
    cross-signed path to Let's Encrypt's newer chains, which workers.dev uses,
    so the default context fails with 'certificate has expired'."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CTX = _ssl_context()


def api(ctx, method, path, body=None, raw=False, timeout=30):
    url = ctx.worker + path
    data = None
    headers = {'Authorization': 'Bearer ' + ctx.token, 'User-Agent': 'bonq-submissions-poll'}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
            payload = r.read()
            if raw:
                return payload, dict(r.headers)
            return json.loads(payload.decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        raise WorkerError(f'{method} {path}: HTTP {e.code} {detail}')
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise WorkerError(f'{method} {path}: {e}')


def post_status(ctx, sid, status, note='', summary=None):
    """Record the outcome locally first, then tell the Worker. A failed POST
    stays in state.json with posted=False and is retried next pass."""
    entry = ctx.state.setdefault(sid, {})
    entry.update({'status': status, 'note': note, 'summary': summary, 'posted': False,
                  'at': datetime.datetime.now().isoformat(timespec='seconds')})
    if ctx.dry_run:
        log.info('[dry-run] would POST /status/%s %s %s', sid, status, note)
        return
    ctx.save_state()
    body = {'status': status, 'note': note}
    if summary is not None:
        body['summary'] = summary
    try:
        api(ctx, 'POST', '/status/' + sid, body)
        entry['posted'] = True
        ctx.save_state()
        log.info('status %s -> %s %s', sid, status, note)
    except WorkerError as e:
        log.warning('could not post status for %s (will retry): %s', sid, e)


def retry_unposted(ctx):
    for sid, entry in ctx.state.items():
        if sid.startswith('_'):
            continue
        if entry.get('posted') is False and entry.get('status'):
            try:
                body = {'status': entry['status'], 'note': entry.get('note', '')}
                if entry.get('summary') is not None:
                    body['summary'] = entry['summary']
                if ctx.dry_run:
                    log.info('[dry-run] would retry status %s', sid)
                    continue
                api(ctx, 'POST', '/status/' + sid, body)
                entry['posted'] = True
                log.info('retried status %s -> %s', sid, entry['status'])
            except WorkerError as e:
                log.warning('retry failed for %s: %s', sid, e)
    ctx.save_state()


# ── git: which cups are published ────────────────────────────────────

def toast(ctx, title, body):
    if not ctx.toast or ctx.dry_run:
        return
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
        "$t = [System.Security.SecurityElement]::Escape('" + title.replace("'", "''") + "');"
        "$b = [System.Security.SecurityElement]::Escape('" + body.replace("'", "''") + "');"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$x.LoadXml(\"<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual></toast>\");"
        "$n = [Windows.UI.Notifications.ToastNotification]::new($x);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($n);"
    )
    try:
        enc = base64.b64encode(ps.encode('utf-16-le')).decode('ascii')
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', enc],
                       timeout=20, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (subprocess.SubprocessError, OSError) as e:
        log.debug('toast failed: %s', e)


# ── CI: a branch and a pull request per cup ─────────────────────────

def git(ctx, *a, check=True):
    r = subprocess.run(['git', *a], cwd=ctx.repo, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def cup_branch(cup):
    return f'cup-{cup}'


def remote_branch_exists(ctx, cup):
    return bool(git(ctx, 'ls-remote', '--heads', ctx.remote, cup_branch(cup), check=False))



def unsafe_args(sub):
    if type(sub.get('cup')) is not int or not 2 <= sub['cup'] <= 999:
        return 'bad cup number'
    try:
        value = sub['date']
        if not isinstance(value, str) or datetime.date.fromisoformat(value).isoformat() != value:
            return 'bad date'
    except (KeyError, ValueError, TypeError):
        return 'bad date'
    nuisance = sub.get('nuisance', [])
    if not isinstance(nuisance, list) or len(nuisance) > 20:
        return 'bad nuisance list'
    for sid in nuisance:
        if not isinstance(sid, str) or not re.fullmatch(r'[0-9]{17}', sid):
            return 'bad nuisance steamID'
    for key, limit in [('header', 160), ('submitter', 40), ('id', 100)]:
        value = sub.get(key, '')
        if not isinstance(value, str) or len(value) > limit or value.startswith('--') or re.search(r'[\r\n\0]', value):
            return f'unsafe argument {key}'
    if not re.fullmatch(r'[A-Za-z0-9_-]+', sub.get('id', '')):
        return 'bad submission id'
    return None


def host_score(path):
    with open(path, encoding='utf-8-sig') as f:
        rounds = json.load(f)['rounds']
    if not isinstance(rounds, list) or any(not isinstance(r, dict) or not isinstance(r.get('playerResults'), list) for r in rounds):
        raise ValueError('invalid host rounds/playerResults')
    return len(rounds), sum(len(r['playerResults']) for r in rounds)


def best_submission(subs, scores):
    return max(subs, key=lambda s: (*scores[s['id']], s.get('received_at', '')))


def download(ctx, sub, directory):
    data, _ = api(ctx, 'GET', '/file/' + quote(sub['id'], safe=''), raw=True, timeout=120)
    if hashlib.sha256(data).hexdigest() != sub.get('sha'):
        raise WorkerError('host file SHA-256 mismatch')
    host = Path(directory) / (sub['id'] + '.json')
    host.write_bytes(data)
    logfile = None
    if sub.get('has_log'):
        data, _ = api(ctx, 'GET', '/log/' + quote(sub['id'], safe=''), raw=True, timeout=120)
        logfile = Path(directory) / (sub['id'] + '.log')
        logfile.write_bytes(data)
    return host, logfile


def pr_body(sub, report, validation=None):
    def names(key):
        return ', '.join(str(x.get('name', x)) if isinstance(x, dict) else str(x) for x in report.get(key, [])) or 'none'
    lines = [report.get('header') or f"Bonq Cup #{sub['cup']}", '',
             f"- Date: {report.get('date', sub['date'])}",
             '- Winners (in order): ' + names('winners'), '- Finalists: ' + names('finalists'),
             '- Nuisance: ' + names('nuisance'),
             f"- Players: {report.get('players', 0)}; rounds: {report.get('rounds', 0)}",
             '', '## Warnings', '']
    lines += ['- ' + str(w) for w in report.get('warnings', [])] or ['- none']
    lines += ['', '## Cross-check (mod flags vs rules)', '']
    lines += ['- ' + str(w) for w in report.get('cross_check', [])] or ['agree']
    lines += ['', '## Identity', '']
    lines += [f"- `{x['sid']}`: {x['seen']} -> {x['as']}" for x in report.get('renamed', [])] or ['No renamed players.']
    lines += ['', '### Not in the registry', '']
    lines += [f"- `{x['sid']}`: {x['seen']}" for x in report.get('unknown', [])] or ['none']
    if validation is not None:
        import html
        lines += ['', '<details><summary>validate_vs_topout output</summary>', '', '<pre>' + html.escape(validation) + '</pre>', '</details>']
    lines += ['', f"Submitter: {sub.get('submitter') or 'someone'}", f"Submission id: `{sub['id']}`", '',
              '\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)']
    return '\n'.join(lines)


def gh(ctx, *args):
    result = subprocess.run(['gh', *args], cwd=ctx.repo, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def open_pr(ctx, sub, report, validation=None):
    title = f"Bonq Cup #{sub['cup']} (submitted)"
    git(ctx, 'config', 'user.name', 'github-actions[bot]')
    git(ctx, 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    branch = cup_branch(sub['cup'])
    git(ctx, 'checkout', '-b', branch)
    paths = [p for p in ['cups', 'host', 'logs', 'bonq.json'] if (Path(ctx.repo) / p).exists()]
    git(ctx, 'add', '--', *paths)
    git(ctx, 'commit', '-m', title)
    git(ctx, 'push', ctx.remote, branch)
    body = Path(ctx.workdir) / 'pr-body.md'
    body.write_text(pr_body(sub, report, validation), encoding='utf-8')
    return gh(ctx, 'pr', 'create', '--base', 'main', '--head', branch, '--title', title, '--body-file', str(body))


def failure(ctx, sub, note):
    post_status(ctx, sub['id'], 'failed', note[:500])
    ctx.exit_code = 1
    toast(ctx, f"Bonq #{sub['cup']} FAILED", note[:200])
    if ctx.ci and not ctx.dry_run:
        title = f"Bonq #{sub['cup']}: submission failed"
        try:
            issues = json.loads(gh(ctx, 'issue', 'list', '--state', 'open', '--search', title + ' in:title', '--json', 'title'))
            if any(i['title'] == title for i in issues):
                return
            run = '/'.join([os.environ.get('GITHUB_SERVER_URL', 'https://github.com'),
                            os.environ.get('GITHUB_REPOSITORY', 'Aizpunr/bonq'), 'actions/runs', os.environ.get('GITHUB_RUN_ID', '')])
            body = Path(ctx.workdir) / 'issue-body.md'
            body.write_text(f"Submission `{sub['id']}` failed.\n\n{note}\n\nWorkflow run: {run}\n", encoding='utf-8')
            gh(ctx, 'issue', 'create', '--title', title, '--body-file', str(body))
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            log.error('could not open failure issue: %s', exc)


def run_cup(ctx, sub, paths):
    host, logfile = paths
    report_path = Path(ctx.workdir) / 'report.json'
    cmd = [ctx.python, 'parse_bonq_from_host.py', str(sub['cup']), '--date', sub['date'],
           '--host-file', str(host), '--report', str(report_path)]
    if 'nuisance' in sub:
        cmd += ['--nuisance', ','.join(sub['nuisance'])]
    if sub.get('header'):
        cmd += ['--header', sub['header']]
    if logfile:
        cmd += ['--log', str(logfile)]
    if ctx.dry_run:
        log.info('[dry-run] would run %s', cmd)
        return
    def run(argv):
        return subprocess.run(argv, cwd=ctx.repo, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=NEW_CUP_TIMEOUT_S,
                              env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    try:
        result = run(cmd)
        output = result.stdout + result.stderr
        if result.returncode or f"BONQ {sub['cup']} COMPLETE" not in result.stdout:
            errors = [line.strip() for line in output.splitlines() if line.strip().startswith('ERROR')]
            raise RuntimeError(errors[-1] if errors else f'parser failed (exit {result.returncode})')
        result = run([ctx.python, 'build_bonq.py'])
        if result.returncode:
            errors = [line.strip() for line in (result.stdout + result.stderr).splitlines() if line.strip().startswith('ERROR')]
            raise RuntimeError(errors[-1] if errors else 'build_bonq.py failed')
        validation = None
        if logfile:
            result = run([ctx.python, 'validate_vs_topout.py', str(sub['cup']), f"host/bonq_{sub['cup']}_topoutresults.json"])
            validation = result.stdout + result.stderr
        report = load_json(report_path, {})
        note = 'awaiting publish'
        if ctx.ci:
            note += ': ' + open_pr(ctx, sub, report, validation)
        post_status(ctx, sub['id'], 'processed', note)
        toast(ctx, f"Bonq #{sub['cup']} processed", 'Verify the results, then publish.')
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        failure(ctx, sub, str(exc))


def published(ctx, cup):
    return subprocess.run(['git', 'cat-file', '-e', f'{ctx.remote}/main:cups/bonq_{cup}.json'],
                          cwd=ctx.repo, capture_output=True, timeout=30).returncode == 0


def _pass(ctx, args):
    meta = ctx.state.setdefault('_meta', {})
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date().isoformat()
    if args.reindex or (meta.get('last_reindex') != today and now.hour >= 4):
        if not ctx.dry_run:
            try:
                api(ctx, 'POST', '/reindex')
                meta['last_reindex'] = today
                ctx.save_state()
            except WorkerError as exc:
                log.warning('reindex failed (will retry): %s', exc)
    retry_unposted(ctx)
    pending = api(ctx, 'GET', '/pending').get('submissions', [])
    # Reconcile all cups before applying --id, so another processed cup still blocks CI.
    if not ctx.no_git:
        git(ctx, 'fetch', ctx.remote, '--quiet')
    for sub in pending:
        if sub['status'] == 'processed' and not ctx.no_git and published(ctx, sub['cup']):
            post_status(ctx, sub['id'], 'published')
            sub['status'] = 'published'
        local = ctx.state.get(sub['id'], {})
        if local.get('status'):
            sub['status'] = local['status']
    by_cup = {}
    for sub in pending:
        if sub['status'] == 'received' and (not ctx.only_id or ctx.only_id == sub['id']):
            by_cup.setdefault(sub['cup'], []).append(sub)
    ran = False
    for cup, subs in sorted(by_cup.items()):
        if not ctx.no_git and published(ctx, cup):
            for sub in subs:
                post_status(ctx, sub['id'], 'duplicate', 'cup already published on origin/main')
            continue
        paths, scores = {}, {}
        for sub in subs:
            problem = unsafe_args(sub)
            if problem:
                failure(ctx, sub, problem)
                continue
            try:
                paths[sub['id']] = download(ctx, sub, ctx.workdir)
                scores[sub['id']] = host_score(paths[sub['id']][0])
            except (WorkerError, OSError, ValueError, KeyError, TypeError) as exc:
                failure(ctx, sub, str(exc))
        valid = [s for s in subs if s['id'] in scores]
        if not valid:
            continue
        best = best_submission(valid, scores)
        for sub in valid:
            if sub is not best:
                post_status(ctx, sub['id'], 'superseded', 'superseded by ' + best['id'])
        if ran:
            continue
        if ctx.ci and (any(s['status'] == 'processed' for s in pending) or remote_branch_exists(ctx, cup)):
            log.info('cup %s waits for an existing processed cup or branch', cup)
            continue
        if (Path(ctx.repo) / 'cups' / f'bonq_{cup}.json').exists():
            log.info('cup %s already exists locally; awaiting publication', cup)
            continue
        run_cup(ctx, best, paths[best['id']])
        ran = True
    return ctx.exit_code


def one_pass(ctx, args):
    if (Path(ctx.repo) / 'PAUSE').exists():
        log.info('PAUSE marker present')
        return 0
    if not ctx.worker or not ctx.token:
        log.error('worker_url / poller_token missing')
        return 2
    lock = None if ctx.dry_run else acquire_lock(str(Path(ctx.repo) / '.submissions.lock'))
    if not ctx.dry_run and lock is None:
        log.info('another poller is running')
        return 0
    try:
        # Downloads and review bodies live outside the checkout. Retained for diagnosis.
        ctx.workdir = tempfile.mkdtemp(prefix='bonq-submissions-')
        return _pass(ctx, args)
    except (WorkerError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        log.error('poll failed: %s', exc)
        return 1
    finally:
        if lock:
            lock.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for flag in ['once', 'ci', 'dry-run', 'no-git', 'reindex', 'rehearsal']:
        ap.add_argument('--' + flag, action='store_true')
    for flag in ['repo', 'worker', 'id']:
        ap.add_argument('--' + flag)
    ap.add_argument('--loop', type=int)
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()
    ctx = Ctx(args)
    setup_logging(ctx.subdir, args.verbose)
    while True:
        result = one_pass(ctx, args)
        if not args.loop:
            return result
        time.sleep(args.loop)


if __name__ == '__main__':
    sys.exit(main())
