"""PC catch-up after merging. Harvest is additive and dry-run unless --write."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
COTD = HERE.parent / 'zeepkist cotd elo'
HOLISTIC = HERE.parent / 'zeepkist holistic'


def strip_tag(name):
    return re.sub(r'^(?:\[[^\]]*\]\s*)+', '', name or '').strip()


def harvest(registry, write=False):
    reg = json.loads(registry.read_text(encoding='utf-8-sig'))
    known_sids = {str(v) for v in reg.values() if v}
    known_names = {k.casefold() for k in reg}
    seen = defaultdict(lambda: defaultdict(set))
    for path in sorted((HERE / 'host').glob('bonq_*_topoutresults.json')):
        host = json.loads(path.read_text(encoding='utf-8-sig'))
        for rnd in host.get('rounds', []):
            for player in rnd.get('playerResults', []):
                sid = str(player.get('steamID', ''))
                name = strip_tag(player.get('username', ''))
                if re.fullmatch(r'[0-9]{17}', sid) and any(c.isalnum() for c in name):
                    seen[sid][name].add(path.name)
    additions = {}
    for sid, names in sorted(seen.items()):
        if sid in known_sids:
            continue
        name = min(names, key=lambda n: (-len(names[n]), -len(n), n))
        if name.casefold() in known_names:
            print(f'SKIPPED name conflict: {name} ({sid})')
            continue
        additions[name] = sid
        known_names.add(name.casefold())
        known_sids.add(sid)
        print(f'{"Adding" if write else "Would add"}: {name} = {sid}')
    if write and additions:
        reg.update(additions)
        tmp = registry.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(dict(sorted(reg.items(), key=lambda x: x[0].lower())), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        tmp.replace(registry)
        return True
    print(f'{len(additions)} additions; ' + ('applied' if write else 'dry run, use --write to apply'))
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--no-pull', action='store_true')
    args = ap.parse_args()
    if not args.no_pull:
        branch = subprocess.run(['git', 'branch', '--show-current'], cwd=HERE, capture_output=True, text=True, check=True).stdout.strip()
        if branch != 'main':
            print('ERROR: switch to main before catch-up')
            return 1
        subprocess.run(['git', 'pull', '--ff-only'], cwd=HERE, check=True)
    if harvest(COTD / 'steam_ids.json', args.write):
        subprocess.run([sys.executable, 'build_players_master.py'], cwd=COTD, check=True)
    builder = HOLISTIC / 'build_allsofdata.py'
    if builder.exists() and 'extract_bonq' in builder.read_text(encoding='utf-8-sig'):
        subprocess.run([sys.executable, 'refresh.py'], cwd=HOLISTIC, check=True)
    else:
        print('cross-comp: Bonq not wired in yet, skipped')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
