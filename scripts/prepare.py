"""Builds the console copy of Factorio from your own installation (staging/Factorio by default).

Nothing of Factorio is part of this project: this script copies what the console needs from a Factorio
you own (the DRM-free zip or the standalone installer, Windows x64) and adjusts only text files.
factorio.exe and the game data are copied unchanged.

    python scripts/prepare.py "C:/Games/Factorio_2.0.77" [--out staging/Factorio] [--saves DIR]
                              [--with-pdb] [--with-docs] [--verbose-log]

What it writes in the copy:
  config-path.cfg   self-contained layout: config, saves and mods live next to the game, not in AppData
  config/config.ini the config.ini your Factorio wrote on this PC, with config/console-settings.ini applied
  host-exe.txt      which exe the loader starts (bin\\x64\\factorio.exe)
  host-args.txt     the command line it passes (--verbose with --verbose-log)
Re-running only copies files that changed."""
import argparse
import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(REPO, 'config', 'console-settings.ini')


def read_settings(path):
    """[section] -> {key: value} from console-settings.ini."""
    out, section = {}, None
    for line in open(path, encoding='utf-8'):
        s = line.strip()
        if not s or s.startswith(';'):
            continue
        if s.startswith('[') and s.endswith(']'):
            section = s[1:-1]
            out.setdefault(section, {})
        elif '=' in s and section:
            k, v = s.split('=', 1)
            out[section][k.strip()] = v.strip()
    return out


def apply_settings(text, settings):
    """Sets keys in Factorio's config.ini text, uncommenting "; key=default" lines where they exist,
    appending keys (and sections) that aren't there. Returns the new text and a count."""
    # Factorio writes its header lines with LF and the rest with CRLF: split on either, write the usual one back.
    nl = '\r\n' if text.count('\r\n') > text.count('\n') / 2 else '\n'
    lines = text.replace('\r\n', '\n').split('\n') if text else []
    changed = 0
    for section, keys in settings.items():
        start = next((i for i, l in enumerate(lines) if l.strip() == f'[{section}]'), None)
        if start is None:
            lines += [f'[{section}]'] + [f'{k}={v}' for k, v in keys.items()] + ['']
            changed += len(keys)
            continue
        end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith('[')), len(lines))
        done = set()
        for i in range(start + 1, end):
            body = lines[i].strip()
            body = body[1:].strip() if body.startswith(';') else body
            key = body.split('=', 1)[0].strip() if '=' in body else None
            if key in keys and key not in done:
                lines[i] = f'{key}={keys[key]}'
                done.add(key)
        missing = [k for k in keys if k not in done]
        insert = end
        while insert > start + 1 and not lines[insert - 1].strip():
            insert -= 1
        lines[insert:insert] = [f'{k}={keys[k]}' for k in missing]
        changed += len(keys)
    return nl.join(lines), changed


def copy_tree(src, dst, skip=lambda rel: False):
    """Copies files that are new or changed (size or time); returns (copied, total)."""
    copied = total = 0
    for here, dirs, files in os.walk(src):
        rel_dir = os.path.relpath(here, src)
        dirs[:] = [d for d in dirs if not skip(os.path.normpath(os.path.join(rel_dir, d)))]
        for f in files:
            rel = os.path.normpath(os.path.join(rel_dir, f))
            if skip(rel):
                continue
            s, d = os.path.join(here, f), os.path.join(dst, rel)
            total += 1
            st = os.stat(s)
            if os.path.exists(d):
                dt = os.stat(d)
                if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
                    continue
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            copied += 1
    return copied, total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('factorio', help='your Factorio folder (the one with bin, data)')
    ap.add_argument('--out', default=os.path.join(REPO, 'staging', 'Factorio'))
    ap.add_argument('--saves', help='a folder of saves (*.zip) to include')
    ap.add_argument('--with-pdb', action='store_true', help="include factorio.pdb (400 MB; crash symbols)")
    ap.add_argument('--with-docs', action='store_true', help='include doc-html')
    ap.add_argument('--verbose-log', action='store_true', help='start Factorio with --verbose')
    ap.add_argument('--config', help="a config.ini written by your Factorio (default: the game's own, or %%APPDATA%%)")
    a = ap.parse_args()

    src = os.path.abspath(a.factorio)
    exe = os.path.join(src, 'bin', 'x64', 'factorio.exe')
    for need in (exe, os.path.join(src, 'data', 'core'), os.path.join(src, 'data', 'base')):
        if not os.path.exists(need):
            sys.exit(f'not a Factorio folder (missing {os.path.relpath(need, src)}): {src}')
    with open(exe, 'rb') as f:
        head = f.read(4096)
    pe = int.from_bytes(head[0x3c:0x40], 'little')
    if head[:2] != b'MZ' or head[pe:pe + 4] != b'PE\0\0' or int.from_bytes(head[pe + 4:pe + 6], 'little') != 0x8664:
        sys.exit('bin/x64/factorio.exe is not a Windows x64 executable')
    try:
        version = json.load(open(os.path.join(src, 'data', 'base', 'info.json'), encoding='utf-8')).get('version', '?')
    except (OSError, ValueError):
        version = '?'
    print(f'Factorio {version} from {src}')
    if not version.startswith('2.0'):
        print('  note: tested with 2.0.77; other versions may need other loader work')

    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    c1, t1 = copy_tree(os.path.join(src, 'bin', 'x64'), os.path.join(out, 'bin', 'x64'),
                       skip=lambda rel: rel.lower().endswith('.pdb') and not a.with_pdb)
    c2, t2 = copy_tree(os.path.join(src, 'data'), os.path.join(out, 'data'))
    copied, total = c1 + c2, t1 + t2
    if a.with_docs and os.path.isdir(os.path.join(src, 'doc-html')):
        c, t = copy_tree(os.path.join(src, 'doc-html'), os.path.join(out, 'doc-html'))
        copied, total = copied + c, total + t
    if a.saves:
        c, t = copy_tree(a.saves, os.path.join(out, 'saves'), skip=lambda rel: not rel.lower().endswith('.zip'))
        copied, total = copied + c, total + t

    # Self-contained: config, saves, mods and logs next to the game (an installer copy points at AppData).
    with open(os.path.join(out, 'config-path.cfg'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('config-path=__PATH__executable__/../../config\nuse-system-read-write-data-directories=false\n')

    # config.ini: Factorio rejects a partial one ("Configuration file has invalid contents"), so the console's is
    # the complete file your own Factorio wrote on this PC (a zip copy keeps it in its folder, an installed one in
    # %APPDATA%\Factorio), with the paths and console settings applied. Rebuilt on every run.
    cfg_dir = os.path.join(out, 'config')
    os.makedirs(cfg_dir, exist_ok=True)
    cfg = os.path.join(cfg_dir, 'config.ini')
    candidates = [a.config] if a.config else [os.path.join(src, 'config', 'config.ini'),
                                              os.path.join(os.environ.get('APPDATA', ''), 'Factorio', 'config', 'config.ini')]
    base = next((p for p in candidates if p and os.path.isfile(p)), None)
    if base:
        text = open(base, encoding='utf-8', newline='').read()
        print(f'  config.ini from {base}')
    else:
        text = '; version=13\n'
        print('  no config.ini from this Factorio yet: start Factorio once on this PC (to its main menu) and run this')
        print('  again, or pass --config. Without it Factorio on the console offers to reset its settings.')
    settings = read_settings(SETTINGS)
    paths = {'path': {'read-data': '__PATH__executable__\\..\\..\\data', 'write-data': '__PATH__executable__\\..\\..'}}
    text, n = apply_settings(text, {**paths, **settings})
    open(cfg, 'w', encoding='utf-8', newline='').write(text)

    with open(os.path.join(out, 'host-exe.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('bin\\x64\\factorio.exe\n')
    with open(os.path.join(out, 'host-args.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('--verbose\n' if a.verbose_log else '\n')

    size = sum(os.path.getsize(os.path.join(h, f)) for h, _, fs in os.walk(out) for f in fs)
    print(f'{out}: {total} game files ({copied} copied now), {size / 2**20:.0f} MB in all; {n} settings applied')
    print('next: scripts/deploy.py all --console <Xbox IP>')


if __name__ == '__main__':
    main()
