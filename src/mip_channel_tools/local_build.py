#!/usr/bin/env python3
"""
Build, test, and publish a single package release locally.

This mirrors the CI `build-package` pipeline for ONE (package, architecture)
pair, calling the exact same steps the reusable workflow runs:

    prepare -> package-setup -> bundle_one (MATLAB) -> test_one (MATLAB)
            -> upload -> trigger the channel's Assemble Index workflow

It exists for architectures GitHub Actions cannot build — chiefly
`macos_x86_64` (Intel Mac): MathWorks dropped Intel-Mac support from `mpm` (the
installer the CI uses), so MATLAB can no longer be installed on an Intel-macOS
runner. A maintainer with an Intel Mac runs this from a channel checkout to
produce and publish that architecture's `.mhl` the same way CI does for the
others.

Run from the channel checkout root (the directory holding `packages/`):

    mip-channel local-build --package-path packages/<name>/<release>

Architecture defaults to the host's native MIP arch (Intel Mac ->
`macos_x86_64`, Apple Silicon -> `macos_arm64`, Linux -> `linux_x86_64`).

Differences from CI, by design:
  * The destructive self-containment "strip" step (CI wipes the runner's whole
    toolchain to prove the .mhl carries its own runtime libs) is skipped — it
    is unsafe on a development machine. A MEX that links a non-bundled dev
    library can therefore still pass `test_one` here; the strip gate stays a
    CI-only check on the architectures CI builds.
  * The MATLAB runtime (`mip`) is taken from the MATLAB path as installed on the
    machine, not a fresh clone. Pass `--mip-dir` to `addpath` a specific
    checkout instead (matching CI's pinned `mip-org/mip`).
"""

import glob
import os
import platform
import shutil
import subprocess
import sys

from .config import get_github_repo


def detect_native_arch():
    """The MIP architecture tag for the host (mirrors mip.build.arch)."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == 'Darwin':
        return 'macos_arm64' if machine in ('arm64', 'aarch64') else 'macos_x86_64'
    if system == 'Linux':
        return 'linux_x86_64'
    if system == 'Windows':
        return 'windows_x86_64'
    sys.exit(f'local-build: unsupported host platform {system!r}; '
             f'pass --architecture explicitly.')


def default_tools_dir():
    """The mip_channel_tools checkout this module was installed from.

    Works for an editable install (`pip install -e`), where __file__ resolves
    to <checkout>/src/mip_channel_tools/local_build.py. Returns None if the
    sibling scripts/ can't be found (e.g. a non-editable install), in which
    case the caller must pass --tools-dir.
    """
    pkg_dir = os.path.dirname(os.path.abspath(__file__))      # src/mip_channel_tools
    checkout = os.path.dirname(os.path.dirname(pkg_dir))       # <checkout>
    if os.path.isfile(os.path.join(checkout, 'scripts', 'bundle_one.m')):
        return checkout
    return None


def resolve_matlab(explicit):
    """Locate the MATLAB executable.

    Order: --matlab, $MATLAB, `matlab` on PATH, then the newest
    /Applications/MATLAB_R*.app (macOS, where MATLAB is usually off PATH).
    """
    if explicit:
        return explicit
    env = os.environ.get('MATLAB')
    if env:
        return env
    found = shutil.which('matlab')
    if found:
        return found
    apps = sorted(glob.glob('/Applications/MATLAB_R*.app/bin/matlab'), reverse=True)
    if apps:
        return apps[0]
    sys.exit('local-build: could not find a MATLAB executable; pass --matlab '
             'or set $MATLAB.')


def _m(path):
    """Quote a filesystem path for a MATLAB single-quoted string literal."""
    return path.replace("'", "''")


def _run_cli(subcmd_args, env, cwd):
    """Invoke another `mip-channel` subcommand — the exact command CI runs."""
    cmd = [sys.executable, '-m', 'mip_channel_tools'] + subcmd_args
    print('+ ' + ' '.join(cmd))
    subprocess.run(cmd, check=True, env=env, cwd=cwd)


def _run_matlab(matlab, command, env, cwd):
    print(f'+ {matlab} -batch "{command}"')
    return subprocess.run([matlab, '-batch', command], env=env, cwd=cwd)


def _fresh_mip_root(mip_root):
    """A clean MIP_ROOT for the test install, so it never touches ~/.mip."""
    if os.path.isdir(mip_root):
        shutil.rmtree(mip_root, ignore_errors=True)
    os.makedirs(os.path.join(mip_root, 'packages'), exist_ok=True)


def run(args):
    arch = args.architecture or detect_native_arch()
    repo_root = os.getcwd()

    if not os.path.isdir(args.package_path):
        sys.exit(f'local-build: package path not found: {args.package_path}\n'
                 f'(run this from the channel checkout root, the dir holding '
                 f'packages/).')

    tools_dir = args.tools_dir or default_tools_dir()
    scripts_dir = os.path.join(tools_dir, 'scripts') if tools_dir else None
    if not scripts_dir or not os.path.isfile(
            os.path.join(scripts_dir, 'bundle_one.m')):
        sys.exit('local-build: cannot locate the MATLAB build scripts; pass '
                 '--tools-dir <mip_channel_tools checkout>.')

    matlab = resolve_matlab(args.matlab)
    repo = get_github_repo()

    mip_root = os.path.abspath(
        args.mip_root or os.path.join(repo_root, 'build', 'local-mip-root'))

    # Mirror CI's environment for the steps below. Setting GITHUB_REPOSITORY
    # makes test_one resolve dependencies from THIS channel (owner/mip-<chan> ->
    # owner/<chan>), exactly as it does in CI, and pins upload/prepare to the
    # same repo. MIP_ROOT isolates the test install from the user's real ~/.mip.
    env = dict(os.environ)
    env['GITHUB_REPOSITORY'] = repo
    env['MIP_ROOT'] = mip_root

    # addpath prefix for the MATLAB steps. mip is taken from the installed
    # MATLAB path by default; --mip-dir prepends a specific checkout (CI parity).
    addpaths = ''
    if args.mip_dir:
        addpaths += f"addpath('{_m(os.path.abspath(args.mip_dir))}'); "
    addpaths += f"addpath('{_m(scripts_dir)}'); "

    print(f'=== local-build: {args.package_path} ({arch}) ===')
    print(f'  channel:  {repo}')
    print(f'  matlab:   {matlab}')
    print(f'  tooling:  {tools_dir}')
    print(f'  mip:      {os.path.abspath(args.mip_dir) if args.mip_dir else "(MATLAB path / global install)"}')
    print(f'  MIP_ROOT: {mip_root}')

    # 1. prepare ------------------------------------------------------------
    prep = ['prepare', '--package-path', args.package_path,
            '--architecture', arch]
    if args.force:
        prep.append('--force')
    _run_cli(prep, env, repo_root)

    prepared = os.path.join(repo_root, 'build', 'prepared')
    if not (os.path.isdir(prepared) and os.listdir(prepared)):
        print(f'\nNothing to build for {arch}: the package does not declare '
              f'this architecture, or a matching .mhl is already published.\n'
              f'Use --force to rebuild anyway.')
        return 0

    # 2. per-OS package setup (brew/apt/... from mip.yaml) ------------------
    _run_cli(['package-setup', '--architecture', arch], env, repo_root)

    # 3. bundle (MATLAB) ----------------------------------------------------
    bundle_env = dict(env)
    bundle_env['BUILD_ARCHITECTURE'] = arch
    r = _run_matlab(matlab, addpaths + 'bundle_one', bundle_env, repo_root)
    if r.returncode != 0:
        sys.exit('local-build: bundle step failed.')

    bundled = os.path.join(repo_root, 'build', 'bundled')
    mhls = sorted(glob.glob(os.path.join(bundled, '*.mhl')))
    if not mhls:
        sys.exit('local-build: bundle produced no .mhl.')
    print(f'  built: {mhls[0]}')

    # 4. install / load / test the .mhl (MATLAB) ----------------------------
    if args.no_test:
        print('  skipping test (--no-test).')
    else:
        _fresh_mip_root(mip_root)
        marker = os.path.join(repo_root, '.tests_passed')
        if os.path.exists(marker):
            os.remove(marker)
        if arch.startswith('macos'):
            # macOS arm64/x86_64: MATLAB R2024b+ SIGSEGVs at shutdown for any
            # MEX linking Homebrew libgomp/libgfortran (cosmetic, after tests
            # finish). Mirror CI's macOS path: a success marker is the real
            # gate, the matlab exit code is tolerated.
            cmd = (addpaths + "try; test_one; fid = fopen('.tests_passed', 'w'); "
                   "fclose(fid); catch err; disp(getReport(err)); exit(1); end")
            _run_matlab(matlab, cmd, env, repo_root)
            if not os.path.exists(marker):
                sys.exit('local-build: tests failed (no .tests_passed marker).')
            os.remove(marker)
            print('  tests passed (matlab exit-time SIGSEGV tolerated).')
        else:
            r = _run_matlab(matlab, addpaths + 'test_one', env, repo_root)
            if r.returncode != 0:
                sys.exit('local-build: tests failed.')
            print('  tests passed.')
        print('  NOTE: the CI self-containment "strip" step is skipped locally; '
              'a MEX linking a non-bundled dev library can still pass here.')

    # 5. upload -------------------------------------------------------------
    if args.no_publish:
        print(f'\nBuilt (not published): {mhls[0]}\n'
              f'Re-run without --no-publish to upload, or: mip-channel upload')
        return 0
    _run_cli(['upload'], env, repo_root)

    # 6. rebuild the channel index ------------------------------------------
    # The .mhl is now on the package's GitHub Release; assemble-index ingests
    # every release asset regardless of architecture, so the new arch appears
    # once the index is rebuilt. Trigger the channel's existing dispatchable
    # Assemble Index workflow (it also redeploys Pages) rather than building
    # the index from this machine.
    reindex_cmd = ['gh', 'workflow', 'run', 'assemble-index.yml', '--repo', repo]
    if args.no_reindex:
        print(f'\nSkipping channel re-index (--no-reindex). Trigger it later '
              f'with:\n  {" ".join(reindex_cmd)}')
        return 0
    print('\nTriggering channel re-index (Assemble Index workflow)...')
    try:
        subprocess.run(reindex_cmd, check=True)
        print('  dispatched; the channel index and Pages will update shortly.')
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f'  WARNING: could not trigger re-index ({e}). Run it manually:\n'
              f'    {" ".join(reindex_cmd)}')
    return 0


def register(subparsers):
    p = subparsers.add_parser(
        'local-build',
        help='Build, test, and publish one package release locally, mirroring '
             'CI (for architectures CI cannot build, e.g. Intel Mac).')
    p.add_argument(
        '--package-path', required=True,
        help='Release dir relative to the channel root, e.g. '
             'packages/<name>/<release>.')
    p.add_argument(
        '--architecture', default=None,
        help='Target architecture (default: the host native arch; '
             'Intel Mac -> macos_x86_64).')
    p.add_argument(
        '--force', action='store_true',
        help='Rebuild even if a matching .mhl is already published.')
    p.add_argument(
        '--no-test', action='store_true',
        help='Skip the install/load/test step.')
    p.add_argument(
        '--no-publish', action='store_true',
        help='Build (and test) only; do not upload to GitHub Releases.')
    p.add_argument(
        '--no-reindex', action='store_true',
        help="Do not trigger the channel's Assemble Index workflow after upload.")
    p.add_argument(
        '--tools-dir', default=None,
        help='mip_channel_tools checkout providing scripts/ and mexopts/ '
             '(default: resolved from this install).')
    p.add_argument(
        '--mip-dir', default=None,
        help='mip checkout to addpath in MATLAB (default: use the mip already '
             'on the MATLAB path).')
    p.add_argument(
        '--matlab', default=None,
        help='MATLAB executable (default: $MATLAB, then matlab on PATH, then '
             'the newest /Applications/MATLAB_R*.app).')
    p.add_argument(
        '--mip-root', default=None,
        help='Isolated MIP_ROOT for the test install '
             '(default: build/local-mip-root).')
    p.set_defaults(func=run)
