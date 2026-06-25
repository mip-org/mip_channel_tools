#!/usr/bin/env python3
"""
Upload a single bundled .mhl (and its .mip.json) to GitHub Releases.

The release tag is derived from the filename:
  {name}-{version}-{arch}.mhl  ->  release tag {name}-{version}

The .mip.json gets its mhl_sha256 field populated before upload so the
client can verify integrity after download.

Usage:
  mip-channel upload --mhl build/bundled/foo-1.0-any.mhl
  mip-channel upload        # auto-discovers the single .mhl
                            # under build/bundled/
  mip-channel upload --release-tag _test-builds --prerelease
                            # test build: publish to the rolling _test-builds
                            # prerelease instead of the package's real release
"""

import os
import sys
import json
import hashlib
import subprocess
from .config import get_github_repo, get_base_url, release_tag_from_mhl


def _sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def _ensure_release_exists(repo, release_tag, prerelease=False):
    result = subprocess.run(
        ['gh', 'release', 'view', release_tag, '--repo', repo],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  Creating release '{release_tag}'...")
        cmd = ['gh', 'release', 'create', release_tag,
               '--repo', repo,
               '--title', release_tag,
               '--notes', f'Package assets for {release_tag}.']
        if prerelease:
            cmd.append('--prerelease')
        subprocess.run(cmd, check=True)


def _upload_file(repo, release_tag, file_path):
    filename = os.path.basename(file_path)
    subprocess.run(
        ['gh', 'release', 'upload', release_tag, file_path,
         '--repo', repo, '--clobber'],
        check=True
    )
    print(f"  Uploaded {filename}")


def upload_mhl(mhl_path, release_tag=None, prerelease=False):
    mhl_filename = os.path.basename(mhl_path)
    mip_json_path = f"{mhl_path}.mip.json"
    if not os.path.exists(mip_json_path):
        print(f"Error: {mhl_filename}.mip.json not found", file=sys.stderr)
        return False

    with open(mip_json_path, 'r') as f:
        mip_json = json.load(f)
    mip_json['mhl_sha256'] = _sha256_of_file(mhl_path)
    with open(mip_json_path, 'w') as f:
        json.dump(mip_json, f, indent=2)
    print(f"  SHA-256: {mip_json['mhl_sha256']}")

    # A caller-supplied release_tag (e.g. the rolling `_test-builds` prerelease)
    # overrides the per-package tag derived from the filename. Test builds
    # publish there so the .mhl gets a direct download URL without entering the
    # channel index (assemble-index only indexes tags backed by a packages/
    # folder in this channel, which `_test-builds` is not).
    if release_tag is None:
        release_tag = release_tag_from_mhl(mhl_filename)
    repo = get_github_repo()
    print(f"Uploading {mhl_filename} -> {repo} release '{release_tag}'")

    _ensure_release_exists(repo, release_tag, prerelease=prerelease)
    _upload_file(repo, release_tag, mhl_path)
    _upload_file(repo, release_tag, mip_json_path)
    print(f"  URL: {get_base_url(release_tag)}/{mhl_filename}")
    return True


def _discover_single_mhl(bundled_dir):
    if not os.path.isdir(bundled_dir):
        return None
    mhls = sorted(
        os.path.join(bundled_dir, f) for f in os.listdir(bundled_dir)
        if f.endswith('.mhl')
    )
    if len(mhls) != 1:
        return None
    return mhls[0]


def run(args):
    mhl_path = args.mhl
    if not mhl_path:
        bundled_dir = os.path.join(os.curdir, 'build', 'bundled')
        mhl_path = _discover_single_mhl(bundled_dir)
        if not mhl_path:
            print(f"Error: expected exactly one .mhl in {bundled_dir}",
                  file=sys.stderr)
            return 2
        print(f"Auto-discovered: {mhl_path}")

    if not os.path.exists(mhl_path):
        print(f"Error: {mhl_path} not found", file=sys.stderr)
        return 2

    try:
        ok = upload_mhl(mhl_path, release_tag=args.release_tag,
                        prerelease=args.prerelease)
    except subprocess.CalledProcessError as e:
        print(f"Upload failed: {e}", file=sys.stderr)
        return 1
    return 0 if ok else 1


def register(subparsers):
    parser = subparsers.add_parser(
        "upload",
        help="Upload a single bundled .mhl to its GitHub Release.")
    parser.add_argument(
        '--mhl', help='Path to .mhl (default: auto-discover in build/bundled)')
    parser.add_argument(
        '--release-tag',
        help='Upload to this release tag instead of the per-package tag '
             'derived from the filename. Test builds pass the rolling '
             '`_test-builds` prerelease so the .mhl gets a direct download URL '
             'without entering the channel index.')
    parser.add_argument(
        '--prerelease', action='store_true',
        help='If the release has to be created, mark it as a prerelease '
             '(used with --release-tag for the test-builds release).')
    parser.set_defaults(func=run)
