"""Puts the launcher and your prepared Factorio on an Xbox in Developer Mode, through Device Portal.

    python scripts/deploy.py <command> --console <Xbox IP> [--user U --password P]

commands:
  install   install build/FactorioXbox.msix (with its VCLibs dependency and certificate)
  upload    copy the prepared game (staging/Factorio) into the app's LocalState\\Games\\Factorio;
            a rerun resumes, sending only files that are missing or changed in size
  launch    start the app (it starts Factorio by itself)
  stop      close the app
  logs      fetch host.log and factorio-current.log into logs/
  shot      save a screenshot into logs/
  all       install, upload, launch

The console address can also come from the XBOX_IP environment variable (XBOX_USER, XBOX_PASSWORD).
Uninstalling the app deletes its LocalState, game files included: install updates over it instead."""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from devportal import DevicePortal  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = 'FactorioXbox'
GAME_REMOTE = '\\LocalState\\Games\\Factorio'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=['install', 'upload', 'launch', 'stop', 'logs', 'shot', 'all'])
    ap.add_argument('--console', default=os.environ.get('XBOX_IP'), help='the Xbox IP address (Dev Home shows it)')
    ap.add_argument('--user', default=os.environ.get('XBOX_USER'))
    ap.add_argument('--password', default=os.environ.get('XBOX_PASSWORD'))
    ap.add_argument('--build', default=os.path.join(REPO, 'build'), help='folder with the signed package')
    ap.add_argument('--game', default=os.path.join(REPO, 'staging', 'Factorio'), help='the prepared game folder')
    a = ap.parse_args()
    if not a.console:
        sys.exit('pass --console <Xbox IP> or set XBOX_IP')

    wdp = DevicePortal(a.console, a.user, a.password)
    info = wdp.connect()
    print(f'{info.get("ComputerName", "Xbox")} at {a.console}, OS {info.get("OsVersion", "?")}')
    steps = ['install', 'upload', 'launch'] if a.command == 'all' else [a.command]

    for step in steps:
        if step == 'install':
            msix = os.path.join(a.build, f'{NAME}.msix')
            extras = [os.path.join(a.build, n) for n in ('Microsoft.VCLibs.x64.14.00.appx', f'{NAME}.cer')]
            missing = [p for p in [msix, *extras] if not os.path.exists(p)]
            if missing:
                sys.exit(f'missing {missing[0]}: run scripts/build.ps1 first')
            pkg = wdp.find_package(NAME)
            if pkg:
                wdp.stop(pkg)
            print(f'installing {msix} ...')
            wdp.install(msix, extras)
            pkg = wdp.find_package(NAME)
            print(f'  installed {pkg["PackageFullName"] if pkg else "(not listed?)"}')
        elif step == 'upload':
            if not os.path.exists(os.path.join(a.game, 'bin', 'x64', 'factorio.exe')):
                sys.exit(f'no prepared game at {a.game}: run scripts/prepare.py first')
            pkg = wdp.find_package(NAME)
            if not pkg:
                sys.exit('the app is not installed: run the install command first')
            if not wdp.listing(pkg['PackageFullName'], '\\LocalState'):
                # A new app has no data folder until it first runs (Device Portal can't make it): run it once.
                print('first run, to create the app data folder ...')
                wdp.launch(pkg)
                time.sleep(15)
            wdp.stop(pkg)
            time.sleep(3)
            print(f'uploading {a.game} -> {GAME_REMOTE} ...')
            files, sent, skipped, secs = wdp.upload_tree(pkg['PackageFullName'], a.game, GAME_REMOTE)
            print(f'  sent {files} files ({sent / 2**20:.0f} MB), {skipped} already there, {secs:.0f} s')
        elif step == 'launch':
            pkg = wdp.find_package(NAME)
            if not pkg:
                sys.exit('the app is not installed')
            wdp.launch(pkg)
            print('launched; Factorio takes about half a minute to its main menu. Turn the controller on first:')
            print('Factorio only finds a pad that is connected when it starts.')
        elif step == 'stop':
            pkg = wdp.find_package(NAME)
            if pkg:
                wdp.stop(pkg)
        elif step in ('logs', 'shot'):
            os.makedirs(os.path.join(REPO, 'logs'), exist_ok=True)
            stamp = time.strftime('%Y%m%d-%H%M%S')
            if step == 'shot':
                png = wdp.screenshot()
                path = os.path.join(REPO, 'logs', f'screen-{stamp}.png')
                open(path, 'wb').write(png or b'')
                print(path)
                continue
            pkg = wdp.find_package(NAME)
            if not pkg:
                sys.exit('the app is not installed')
            for remote_dir, name in (('\\LocalState', 'host.log'), (GAME_REMOTE, 'factorio-current.log')):
                data = wdp.get_file(pkg['PackageFullName'], remote_dir, name)
                path = os.path.join(REPO, 'logs', f'{stamp}-{name}')
                if data is not None:
                    open(path, 'wb').write(data)
                    print(path)
                else:
                    print(f'  no {name} yet')


if __name__ == '__main__':
    main()
