#!/usr/bin/env python3
"""
Prepare a single package release for one architecture.

Usage:
  mip-channel prepare \
      --package-path packages/<name>/<release> \
      --architecture <arch>

The command:
  1. Reads source.yaml from the package release directory
  2. Fetches source per source.yaml (git clone or zip download)
  3. Overlays channel-provided files (mip.yaml, compile.m, test scripts, ...)
  4. Validates channel version rules and architecture support
  5. Writes build/prepared/<name>-<release>/ ready for `mip bundle`
  6. Writes .release_version / .source_hash / .commit_hash side files

If the requested architecture is not listed under any `builds:` entry of
the package's mip.yaml, the command exits 0 without producing output
(the calling workflow then short-circuits the bundle/test/upload steps).

If --force is not set and a matching .mhl already exists on the channel's
GitHub Releases (matching source hash and metadata), prepare exits 0
without producing output, signalling "nothing to do".

Repo mode (MEP 10: a package published from its own repository):
  mip-channel prepare \
      --source-dir pkgsrc --version <version> --release-tag <tag> \
      [--commit <sha>] --architecture <arch>

The source is the given checkout (no source.yaml, no overlay). `.git` is
excluded from the copy and the source hash, the commit is recorded as
.commit_hash and mixed into the source hash, and the skip check looks for
an existing sidecar on <tag> rather than on the per-package release.

If the upstream source can't be reached (git ls-remote/clone or a download
fails) but an .mhl for this release and architecture is already published,
prepare exits SOURCE_UNREACHABLE (3) without producing output, so the
scheduled check can report a warning instead of a failure. If nothing is
published yet, it exits 1 as before.
"""

import os
import sys
import stat
import shutil
import hashlib
import subprocess
import requests
import yaml
from .config import get_base_url, release_tag_from_mhl

# Exit code: the source host is unreachable, but the release is published.
SOURCE_UNREACHABLE = 3


def _rmtree_on_error(func, path, exc_info):
    """Handle read-only files on Windows (e.g. .git/objects/pack)."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _run_git(cmd):
    """Run a git command, echoing git's stderr if it fails.

    Output is captured to keep successful runs quiet, but that also swallows
    git's error message on failure -- a bare CalledProcessError ("exit status
    128") is undiagnosable from a CI log. Print stderr before re-raising."""
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b'').decode('utf-8', errors='replace')
        print(stderr, file=sys.stderr)
        raise


def clone_git_repository(url, destination, subdirectory=None, branch=None,
                         submodules=False):
    """Clone a git repository, optionally extracting a subdirectory.

    When `submodules` is set, the clone recurses into git submodules so their
    working trees are populated (needed by sources whose build descends into a
    submodule, e.g. fmm3dbie's vendored FMM3D)."""
    # Git for Windows refuses to check out paths longer than 260 chars unless
    # core.longpaths is set (the GitHub Windows runners don't set it), and
    # some upstream trees are deep enough to breach that under
    # build/prepared/_temp_<name>_<release>/ (e.g. PIVlab's MATLAB-project
    # resources/ folder). No effect on other platforms.
    clone_args = ["-c", "core.longpaths=true"]
    if branch:
        clone_args += ["--branch", branch]
    if submodules:
        clone_args += ["--recurse-submodules"]
    if subdirectory:
        temp_clone_dir = destination + "_temp_clone"
        print(f'  Cloning {url} (subdirectory: {subdirectory})...')
        _run_git(["git", "clone"] + clone_args + [url, temp_clone_dir])
        subdir_path = os.path.join(temp_clone_dir, subdirectory)
        if not os.path.isdir(subdir_path):
            shutil.rmtree(temp_clone_dir, onerror=_rmtree_on_error)
            raise ValueError(
                f"Subdirectory '{subdirectory}' not found in cloned repo")
        if destination == '.':
            for item in os.listdir(subdir_path):
                s = os.path.join(subdir_path, item)
                d = os.path.join('.', item)
                if os.path.isdir(s):
                    shutil.copytree(s, d)
                else:
                    shutil.copy2(s, d)
        else:
            shutil.copytree(subdir_path, destination)
        shutil.rmtree(temp_clone_dir, onerror=_rmtree_on_error)
    else:
        print(f'  Cloning {url}...')
        _run_git(["git", "clone"] + clone_args + [url, destination])

    for root, dirs, files in os.walk(destination):
        if ".git" in dirs:
            shutil.rmtree(os.path.join(root, ".git"),
                          onerror=_rmtree_on_error)
            dirs.remove(".git")
        # Submodule working trees carry a .git *file* (a gitlink pointing into
        # the parent's .git/modules); drop those too so no .git remnants leak
        # into the source hash.
        if ".git" in files:
            os.remove(os.path.join(root, ".git"))


def download_and_extract_zip(url, destination):
    import zipfile
    download_file = "temp_download.zip"
    print(f'  Downloading {url}...')
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    with open(download_file, 'wb') as f:
        f.write(response.content)
    print(f"  Extracting to {destination}...")
    with zipfile.ZipFile(download_file, 'r') as z:
        z.extractall(destination)
    os.remove(download_file)


def download_and_extract_tarball(url, destination):
    """Download and extract a (optionally gzip/bzip2/xz-compressed) tarball.

    Useful for sources distributed only as tarballs, e.g. Octave-Forge packages.
    """
    import tarfile
    download_file = "temp_download.tar"
    print(f'  Downloading {url}...')
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    with open(download_file, 'wb') as f:
        f.write(response.content)
    print(f"  Extracting to {destination}...")
    with tarfile.open(download_file, 'r:*') as t:
        members = t.getmembers()
        # Source tarballs conventionally wrap everything in a single top-level
        # directory (e.g. nurbs-1.4.4/). Strip it so the package content lands
        # directly in `destination`.
        tops = {m.name.split('/', 1)[0] for m in members if m.name}
        if len(tops) == 1:
            prefix = tops.pop() + '/'
            stripped = []
            for m in members:
                if m.name == prefix.rstrip('/'):
                    continue  # the top-level directory entry itself
                if m.name.startswith(prefix):
                    m.name = m.name[len(prefix):]
                    stripped.append(m)
            t.extractall(destination, members=stripped)
        else:
            t.extractall(destination)
    os.remove(download_file)


def resolve_git_commit_hash(url, ref):
    result = subprocess.run(
        ["git", "ls-remote", url, ref],
        check=True, capture_output=True, text=True
    )
    for line in result.stdout.strip().splitlines():
        commit_hash, remote_ref = line.split('\t', 1)
        if remote_ref in (f"refs/heads/{ref}", f"refs/tags/{ref}", ref):
            return commit_hash
    raise RuntimeError(f"Could not resolve ref '{ref}' for {url}")


def compute_directory_hash(directory, exclude=()):
    """SHA-1 over relative paths and contents. Directories and files whose
    name is in `exclude` (e.g. '.git') are skipped at every level."""
    sha1 = hashlib.sha1()
    for root, dirs, files in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if d not in exclude)
        files = sorted(f for f in files if f not in exclude)
        for filename in files:
            file_path = os.path.join(root, filename)
            # Normalise the path separator to '/' so the hash is identical
            # on a Windows build runner (os.sep == '\\') and the Linux
            # scheduled-build probe. Without this, a release folder with
            # subdirectories hashes differently on Windows, so the daily
            # probe sees a mismatch and rebuilds windows_x86_64 forever.
            rel = os.path.relpath(file_path, directory).replace(os.sep, '/')
            sha1.update(rel.encode('utf-8'))
            sha1.update(b'\0')
            try:
                with open(file_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        sha1.update(chunk)
            except (IOError, OSError) as e:
                sha1.update(f"ERROR:{e}".encode('utf-8'))
            sha1.update(b'\0')
    return sha1.hexdigest()


def overlay_channel_files(release_folder, target_dir):
    """Copy channel-provided files (everything except source.yaml) into target."""
    for item in os.listdir(release_folder):
        if item == 'source.yaml':
            continue
        src = os.path.join(release_folder, item)
        dst = os.path.join(target_dir, item)
        if os.path.isdir(src):
            if os.path.exists(dst):
                for root, _, files in os.walk(src):
                    rel_root = os.path.relpath(root, src)
                    dst_root = os.path.join(dst, rel_root)
                    os.makedirs(dst_root, exist_ok=True)
                    for f in files:
                        shutil.copy2(
                            os.path.join(root, f),
                            os.path.join(dst_root, f))
            else:
                shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def is_numeric_version(s):
    if not s:
        return False
    parts = s.split('.')
    return all(p.isdigit() for p in parts) and len(parts) >= 1


def check_mip_yaml_version(mip_yaml, release_version):
    """mip.yaml version rules (mip's docs/specification.md §11.2.1),
    shared by channel and repo mode."""
    mv = str(mip_yaml.get('version') or '').strip()

    if mv and not is_numeric_version(mv):
        raise ValueError(
            f"mip.yaml 'version' must be blank or numeric, got {mv!r}.")

    # A numeric release version must match mip.yaml's version when one is
    # set. A non-numeric release version (e.g. a branch name like 'main')
    # is always allowed and takes precedence over mip.yaml's version.
    if mv and is_numeric_version(release_version) and release_version != mv:
        raise ValueError(
            f"Numeric release version {release_version!r} must equal "
            f"mip.yaml version {mv!r}.")


def validate_channel_version_rules(mip_yaml_path, recipe, release_version):
    """See mip's docs/specification.md §11.2.1 for the rules."""
    if 'version' in recipe:
        raise ValueError(
            "source.yaml must not contain a 'version' field "
            "(release dir is the version)")
    check_mip_yaml_version(read_mip_yaml(mip_yaml_path), release_version)


def read_mip_yaml(mip_yaml_path):
    with open(mip_yaml_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def architectures_from_mip_yaml(mip_yaml):
    archs = set()
    for build in mip_yaml.get('builds', []):
        for a in build.get('architectures', []):
            archs.add(a)
    return archs


def check_existing_package(mhl_filename, source_hash, mip_yaml,
                           release_version, release_tag=None):
    """Return True if a matching package is already published.

    The sidecar is looked up on `release_tag`, or on the per-package tag
    derived from the filename when none is given."""
    if release_tag is None:
        release_tag = release_tag_from_mhl(mhl_filename)
    base_url = get_base_url(release_tag)
    mip_json_url = f"{base_url}/{mhl_filename}.mip.json"

    try:
        response = requests.get(mip_json_url, timeout=10)
        if response.status_code == 404:
            return False
        response.raise_for_status()
        existing = response.json()
    except requests.RequestException as e:
        print(f"  Error checking existing package: {e}")
        return False

    if existing.get('source_hash') != source_hash:
        return False
    if existing.get('version') != release_version:
        return False
    # Treat None / '' / [] as equivalent ("absent") on both sides — the
    # mip.json writer normalises missing fields to '' or [], while the
    # locally-read yaml leaves them as None.
    def _norm(v):
        return v if v else None
    for field in ('name', 'description', 'dependencies',
                  'homepage', 'repository', 'license'):
        if _norm(existing.get(field)) != _norm(mip_yaml.get(field)):
            return False
    return True


def is_published(mhl_filename):
    """Return True if any .mhl with this filename is on the channel.

    Unlike check_existing_package this ignores the source hash, which
    can't be computed when the upstream source is unreachable."""
    release_tag = release_tag_from_mhl(mhl_filename)
    mip_json_url = f"{get_base_url(release_tag)}/{mhl_filename}.mip.json"
    try:
        response = requests.get(mip_json_url, timeout=10)
    except requests.RequestException as e:
        print(f"  Error checking published package: {e}")
        return False
    return response.status_code == 200


def _source_unreachable(error, release_folder, package_name,
                        release_version, architecture):
    """Exit code for a source fetch that failed with `error`."""
    stderr = getattr(error, 'stderr', None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode('utf-8', errors='replace')
    if stderr:
        print(stderr, file=sys.stderr)
    print(f"  Error: could not reach the upstream source: {error}",
          file=sys.stderr)

    # The channel's mip.yaml (if any) gives the published name; otherwise
    # the package directory name, which prepare requires to match.
    name = package_name
    channel_mip_yaml = os.path.join(release_folder, 'mip.yaml')
    if os.path.exists(channel_mip_yaml):
        name = read_mip_yaml(channel_mip_yaml).get('name') or name
    mhl_filename = (
        f"{name.replace('-', '_')}-{release_version}-{architecture}.mhl")

    if is_published(mhl_filename):
        print(f"  {mhl_filename} is already published; keeping it. "
              f"Could not check it against the upstream source.")
        return SOURCE_UNREACHABLE
    return 1


def fetch_source(recipe, target_dir):
    source = recipe.get('source')
    if not source:
        return
    original_dir = os.getcwd()
    os.chdir(target_dir)
    try:
        if 'git' in source:
            clone_git_repository(
                url=source['git'],
                destination='.',
                subdirectory=source.get('subdirectory'),
                branch=source.get('branch'),
                submodules=source.get('submodules', False),
            )
        elif 'zip' in source:
            download_and_extract_zip(source['zip'], '.')
        elif 'tarball' in source:
            download_and_extract_tarball(source['tarball'], '.')

        for dir_name in source.get('remove_dirs', []):
            dir_path = os.path.join(target_dir, dir_name)
            if os.path.isdir(dir_path):
                shutil.rmtree(dir_path, onerror=_rmtree_on_error)
                print(f"    Removed directory: {dir_name}")

        for file_name in source.get('remove_files', []):
            file_path = os.path.join(target_dir, file_name)
            if os.path.isfile(file_path):
                os.remove(file_path)
                print(f"    Removed file: {file_name}")
    finally:
        os.chdir(original_dir)


def combine_hashes(source_hash, remote_hashes):
    combined = hashlib.sha1()
    combined.update(source_hash.encode('utf-8'))
    for h in sorted(remote_hashes):
        combined.update(h.encode('utf-8'))
    return combined.hexdigest()


def _prepare_release(args, mip_yaml, release_version, source_hash, commit,
                     output_dir, populate, release_tag=None):
    """Shared tail of both modes: architecture and skip checks, then the
    prepared dir, filled by `populate(out_path)` (out_path does not exist
    yet) plus the side files."""
    archs = architectures_from_mip_yaml(mip_yaml)
    if args.architecture not in archs:
        print(f"  Architecture {args.architecture!r} not in {sorted(archs)}; "
              f"nothing to prepare for this arch.")
        return 0

    name_for_filename = mip_yaml['name'].replace('-', '_')
    mhl_filename = (
        f"{name_for_filename}-{release_version}-{args.architecture}.mhl")

    if not args.force and check_existing_package(
            mhl_filename, source_hash, mip_yaml, release_version,
            release_tag=release_tag):
        where = f" on '{release_tag}'" if release_tag else ''
        print(f"  {mhl_filename} already published{where} with matching "
              f"hash; skipping.")
        return 0

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(
        output_dir, f"{mip_yaml['name']}-{release_version}")
    if os.path.exists(out_path):
        shutil.rmtree(out_path, onerror=_rmtree_on_error)
    populate(out_path)

    with open(os.path.join(out_path, '.release_version'), 'w') as f:
        f.write(release_version)
    with open(os.path.join(out_path, '.source_hash'), 'w') as f:
        f.write(source_hash)
    if commit:
        with open(os.path.join(out_path, '.commit_hash'), 'w') as f:
            f.write(commit)

    print(f"  Prepared: {out_path}")
    print(f"  Expected output: {mhl_filename}")
    return 0


def run_repo_mode(args):
    source_dir = os.path.abspath(args.source_dir)
    if not os.path.isdir(source_dir):
        print(f"Error: source dir not found: {source_dir}", file=sys.stderr)
        return 2
    if not args.version or not args.release_tag:
        print("Error: --source-dir requires --version and --release-tag",
              file=sys.stderr)
        return 2
    release_version = args.version

    output_dir = os.path.abspath(args.output_dir or os.path.join(
        os.path.abspath(os.curdir), 'build', 'prepared'))
    if os.path.commonpath([output_dir, source_dir]) == source_dir:
        print(f"Error: output dir {output_dir} is inside the source dir "
              f"{source_dir}", file=sys.stderr)
        return 2

    mip_yaml_path = os.path.join(source_dir, 'mip.yaml')
    if not os.path.exists(mip_yaml_path):
        print(f"Error: no mip.yaml in {source_dir}", file=sys.stderr)
        return 1
    mip_yaml = read_mip_yaml(mip_yaml_path)
    check_mip_yaml_version(mip_yaml, release_version)

    print(f"Package: {mip_yaml.get('name')}")
    print(f"Release: {release_version} (release tag {args.release_tag})")
    print(f"Architecture: {args.architecture}")

    source_hash = compute_directory_hash(source_dir, exclude=('.git',))
    if args.commit:
        print(f"  Commit: {args.commit}")
        source_hash = combine_hashes(source_hash, [args.commit])
    print(f"  Source hash: {source_hash}")

    def populate(out_path):
        shutil.copytree(source_dir, out_path,
                        ignore=shutil.ignore_patterns('.git'))

    return _prepare_release(
        args, mip_yaml, release_version, source_hash, args.commit,
        output_dir, populate, release_tag=args.release_tag)


def run(args):
    if not args.architecture:
        print("Error: --architecture or $BUILD_ARCHITECTURE required",
              file=sys.stderr)
        return 2

    if args.source_dir:
        return run_repo_mode(args)
    if not args.package_path:
        print("Error: --package-path or --source-dir required",
              file=sys.stderr)
        return 2

    release_folder = os.path.abspath(args.package_path)
    if not os.path.isdir(release_folder):
        print(f"Error: package path not found: {release_folder}",
              file=sys.stderr)
        return 2

    recipe_path = os.path.join(release_folder, 'source.yaml')
    if not os.path.exists(recipe_path):
        print(f"Error: no source.yaml in {release_folder}", file=sys.stderr)
        return 2

    package_name = os.path.basename(os.path.dirname(release_folder))
    release_version = os.path.basename(release_folder)

    if package_name != package_name.lower():
        print(f"Error: package name must be lowercase: {package_name}",
              file=sys.stderr)
        return 2

    print(f"Package: {package_name}")
    print(f"Release: {release_version}")
    print(f"Architecture: {args.architecture}")

    with open(recipe_path, 'r') as f:
        recipe = yaml.safe_load(f) or {}

    source_hash = compute_directory_hash(release_folder)
    remote_hashes = []
    source = recipe.get('source') or {}
    if 'git' in source and source.get('branch'):
        try:
            commit_hash = resolve_git_commit_hash(
                source['git'], source['branch'])
        except subprocess.CalledProcessError as e:
            return _source_unreachable(
                e, release_folder, package_name, release_version,
                args.architecture)
        print(f"  Resolved {source['git']} {source['branch']} -> "
              f"{commit_hash[:12]}")
        remote_hashes.append(commit_hash)

    if remote_hashes:
        source_hash = combine_hashes(source_hash, remote_hashes)
    print(f"  Source hash: {source_hash}")

    output_dir = args.output_dir or os.path.join(
        os.path.abspath(os.curdir), 'build', 'prepared')
    os.makedirs(output_dir, exist_ok=True)

    # Probe mip.yaml (it may live in source or be overlaid from channel)
    # by doing a tentative fetch into a temp dir.
    temp_dir = os.path.join(
        output_dir, f"_temp_{package_name}_{release_version}")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, onerror=_rmtree_on_error)
    os.makedirs(temp_dir)

    try:
        try:
            fetch_source(recipe, temp_dir)
        except (subprocess.CalledProcessError,
                requests.RequestException) as e:
            return _source_unreachable(
                e, release_folder, package_name, release_version,
                args.architecture)
        overlay_channel_files(release_folder, temp_dir)
        mip_yaml_path = os.path.join(temp_dir, 'mip.yaml')
        if not os.path.exists(mip_yaml_path):
            print("  Error: no mip.yaml after overlay", file=sys.stderr)
            return 1
        validate_channel_version_rules(
            mip_yaml_path, recipe, release_version)
        mip_yaml = read_mip_yaml(mip_yaml_path)
    finally:
        shutil.rmtree(temp_dir, onerror=_rmtree_on_error)

    def populate(out_path):
        os.makedirs(out_path)
        fetch_source(recipe, out_path)
        overlay_channel_files(release_folder, out_path)
        validate_channel_version_rules(
            os.path.join(out_path, 'mip.yaml'), recipe, release_version)

    return _prepare_release(
        args, mip_yaml, release_version, source_hash,
        remote_hashes[0] if remote_hashes else None, output_dir, populate)


def register(subparsers):
    parser = subparsers.add_parser(
        "prepare",
        help="Prepare a single package release for one architecture.")
    parser.add_argument(
        '--package-path',
        help='Path to the release dir, e.g. packages/<name>/<release>')
    parser.add_argument(
        '--architecture',
        default=os.environ.get('BUILD_ARCHITECTURE'),
        help='Target architecture (or $BUILD_ARCHITECTURE)')
    parser.add_argument('--force', action='store_true')
    parser.add_argument(
        '--output-dir',
        help='Output dir (default: build/prepared)')
    parser.add_argument(
        '--source-dir',
        help='Repo mode: package checkout to build (instead of '
             '--package-path)')
    parser.add_argument(
        '--version',
        help='Repo mode: version to publish (from the triggering ref)')
    parser.add_argument(
        '--release-tag',
        help='Repo mode: release the assets are published to (checked for '
             'an existing sidecar)')
    parser.add_argument(
        '--commit',
        default=os.environ.get('GITHUB_SHA'),
        help='Repo mode: commit being built, recorded as .commit_hash '
             '(default: $GITHUB_SHA)')
    parser.set_defaults(func=run)
