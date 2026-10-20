#!/usr/bin/env python3
"""Helpers for packages published from their own GitHub repository (MEP 10).

In repo mode the package repository holds `mip.yaml` at its root and calls
the reusable `repo-build.yml` workflow. The version of a build comes from the
triggering ref rather than from a `packages/<name>/<release>/` directory:

  - a tag `v1.2.0` or `1.2.0` builds version `1.2.0` (a leading `v` is
    stripped only when the remainder is numeric);
  - a tag that is not numeric after stripping `v` (e.g. `v1.2.0-beta`) is
    published under its literal name;
  - a branch push builds a version named after the branch (e.g. `main`).

Tag builds attach their assets to the GitHub release for the tag. Branch
builds, and the repository's `index.json`, live on the `mip-index` release,
which is kept a prerelease so it never becomes the repository's "Latest".

Subcommands:

    repo-plan --source-dir DIR --ref-type tag|branch --ref-name NAME
              [--repository OWNER/REPO] [--index-only] [--github-output PATH]
        Determine version, release tag, and build matrix; write them as
        step outputs (key=value lines) to --github-output.

    repo-ensure-release --tag TAG [--repository OWNER/REPO]
        Make sure the GitHub release for TAG exists. The `mip-index` release
        is created as a prerelease; any other tag must already exist.

    repo-prune-branch-assets --package-name NAME --version VERSION
                             --architectures JSON [--repository OWNER/REPO]
        After a branch build, delete that branch's assets on the `mip-index`
        release for architectures no longer built, so a stale build (e.g.
        an `any` build left over after mip.yaml switched to native
        architectures) does not stay in the index.
"""

import json
import os
import re
import subprocess
import sys

import yaml

from .build_request import arches_from_mip_config
from .config import get_github_repo
from .prepare import check_mip_yaml_version, is_numeric_version


INDEX_RELEASE_TAG = 'mip-index'

# Same character class as channel release directories; the version becomes
# part of the .mhl filename and of `name@version` arguments, so slashes and
# other separators are rejected.
_VERSION_RE = re.compile(r'^[A-Za-z0-9._+\-]+$')


class PlanError(Exception):
    pass


def normalize_name(name):
    """mip name equivalence: case-insensitive, '-' equivalent to '_'."""
    return str(name).lower().replace('-', '_')


def version_from_ref(ref_type, ref_name):
    if not ref_name:
        raise PlanError("empty ref name")
    if ref_type == 'tag':
        if ref_name.startswith('v') and is_numeric_version(ref_name[1:]):
            version = ref_name[1:]
        else:
            version = ref_name
    elif ref_type == 'branch':
        version = ref_name
    else:
        raise PlanError(
            f"unsupported ref type {ref_type!r} (expected 'tag' or 'branch')")
    if not _VERSION_RE.match(version) or version in ('.', '..'):
        raise PlanError(
            f"{ref_type} {ref_name!r} cannot be used as a mip version "
            f"(allowed characters: letters, digits, '.', '_', '+', '-')")
    return version


def release_tag_for_ref(ref_type, ref_name):
    if ref_type == 'tag':
        if ref_name == INDEX_RELEASE_TAG:
            raise PlanError(
                f"tag {INDEX_RELEASE_TAG!r} is reserved for the mip index "
                f"release and cannot be built")
        return ref_name
    return INDEX_RELEASE_TAG


def check_package_name(mip_yaml, repository):
    name = mip_yaml.get('name')
    if not name:
        raise PlanError("mip.yaml has no 'name'")
    repo_name = repository.split('/')[-1]
    if normalize_name(name) != normalize_name(repo_name):
        raise PlanError(
            f"mip.yaml name {name!r} does not match repository name "
            f"{repo_name!r} (names are compared case-insensitively, with "
            f"'-' and '_' equivalent)")
    return str(name)


def check_version(mip_yaml, version, ref_type, ref_name):
    """Channel version rules (mip spec §11.2.1) applied to the ref version."""
    try:
        check_mip_yaml_version(mip_yaml, version)
    except ValueError as e:
        raise PlanError(f"{ref_type} {ref_name!r}: {e}") from e


def plan(source_dir, repository, ref_type, ref_name, index_only=False):
    """Return the plan dict for a repo-mode run. Raises PlanError."""
    mip_yaml_path = os.path.join(source_dir, 'mip.yaml')
    if not os.path.isfile(mip_yaml_path):
        raise PlanError(f"no mip.yaml at the repository root ({source_dir})")
    with open(mip_yaml_path, 'r', encoding='utf-8') as f:
        mip_yaml = yaml.safe_load(f) or {}
    if not isinstance(mip_yaml, dict):
        raise PlanError("mip.yaml is not a mapping")

    package_name = check_package_name(mip_yaml, repository)

    result = {
        'package_name': package_name,
        'build': False,
        'matrix': [],
        'version': '',
        'release_tag': '',
    }
    if index_only:
        return result

    version = version_from_ref(ref_type, ref_name)
    release_tag = release_tag_for_ref(ref_type, ref_name)
    check_version(mip_yaml, version, ref_type, ref_name)

    matrix = arches_from_mip_config(mip_yaml)
    if not matrix:
        declared = sorted({
            a for b in (mip_yaml.get('builds') or [])
            for a in (b.get('architectures') or [])})
        raise PlanError(
            f"mip.yaml declares no architecture CI can build "
            f"(declared: {declared or 'none'})")

    result.update({
        'build': True,
        'matrix': matrix,
        'version': version,
        'release_tag': release_tag,
    })
    return result


def _write_outputs(result, path):
    lines = [
        f"package_name={result['package_name']}",
        f"build={'true' if result['build'] else 'false'}",
        f"matrix={json.dumps(result['matrix'], separators=(',', ':'))}",
        f"version={result['version']}",
        f"release_tag={result['release_tag']}",
    ]
    with open(path, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def run_plan(args):
    repository = args.repository or get_github_repo()
    try:
        result = plan(args.source_dir, repository, args.ref_type,
                      args.ref_name, index_only=args.index_only)
    except PlanError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Package: {result['package_name']}")
    if result['build']:
        print(f"Version: {result['version']}")
        print(f"Release tag: {result['release_tag']}")
        print(f"Architectures: {', '.join(result['matrix'])}")
    else:
        print("Index-only run: nothing to build.")

    if args.github_output:
        _write_outputs(result, args.github_output)
    return 0


def ensure_release(repository, tag):
    """Create the release for `tag` if it does not exist.

    The `mip-index` release is created as a prerelease (and switched to one
    if someone has edited it), so it never becomes "Latest". Any other tag
    must already exist; its release is created with --verify-tag so this
    never creates a tag."""
    def _view():
        r = subprocess.run(
            ['gh', 'release', 'view', tag, '--repo', repository,
             '--json', 'isPrerelease,isDraft'],
            capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)

    info = _view()
    if info is None:
        print(f"Creating release '{tag}' in {repository}...")
        if tag == INDEX_RELEASE_TAG:
            cmd = ['gh', 'release', 'create', tag, '--repo', repository,
                   '--prerelease', '--title', 'mip index',
                   '--notes', 'mip package index and branch builds. '
                              'Assets are replaced on every build.']
        else:
            cmd = ['gh', 'release', 'create', tag, '--repo', repository,
                   '--verify-tag', '--title', tag, '--notes', '']
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            # A concurrent run may have created it in the meantime.
            info = _view()
            if info is None:
                print(r.stderr, file=sys.stderr)
                raise subprocess.CalledProcessError(r.returncode, cmd)
        return

    if info.get('isDraft'):
        print(f"Warning: release '{tag}' is a draft; its assets are not "
              f"publicly downloadable and it is left out of the index.")
    if tag == INDEX_RELEASE_TAG and not info.get('isPrerelease'):
        print(f"Marking release '{tag}' as a prerelease...")
        subprocess.run(
            ['gh', 'release', 'edit', tag, '--repo', repository,
             '--prerelease'],
            check=True)


def run_ensure_release(args):
    repository = args.repository or get_github_repo()
    try:
        ensure_release(repository, args.tag)
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


def stale_branch_assets(asset_names, package_name, version, architectures):
    """Asset names of the branch build `version` whose architecture is not
    in `architectures`. Asset names are <name>-<version>-<arch>.mhl and
    <name>-<version>-<arch>.mhl.mip.json, with '-' in the name written as
    '_'. Architectures contain no '-', which keeps branch `main` from
    matching the assets of a branch such as `main-dev`."""
    prefix = f"{package_name.replace('-', '_')}-{version}-"
    stale = []
    for asset in asset_names:
        if not asset.startswith(prefix):
            continue
        for suffix in ('.mhl.mip.json', '.mhl'):
            if asset.endswith(suffix):
                arch = asset[len(prefix):-len(suffix)]
                if arch and '-' not in arch and arch not in architectures:
                    stale.append(asset)
                break
    return stale


def prune_branch_assets(repository, package_name, version, architectures):
    r = subprocess.run(
        ['gh', 'release', 'view', INDEX_RELEASE_TAG, '--repo', repository,
         '--json', 'assets'],
        capture_output=True, text=True, check=True)
    names = [a['name'] for a in json.loads(r.stdout).get('assets', [])]
    stale = stale_branch_assets(names, package_name, version, architectures)
    for asset in stale:
        print(f"Deleting stale asset {asset} from '{INDEX_RELEASE_TAG}'...")
        subprocess.run(
            ['gh', 'release', 'delete-asset', INDEX_RELEASE_TAG, asset,
             '--repo', repository, '--yes'],
            check=True)
    if not stale:
        print("No stale branch assets.")
    return stale


def run_prune_branch_assets(args):
    repository = args.repository or get_github_repo()
    try:
        architectures = json.loads(args.architectures)
        prune_branch_assets(repository, args.package_name, args.version,
                            architectures)
    except (ValueError, subprocess.CalledProcessError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


def register(subparsers):
    p = subparsers.add_parser(
        "repo-plan",
        help="Plan a build of a package published from its own repository.")
    p.add_argument('--source-dir', required=True,
                   help='Checkout of the package repository')
    p.add_argument('--ref-type', default=os.environ.get('GITHUB_REF_TYPE'),
                   help="'tag' or 'branch' (default: $GITHUB_REF_TYPE)")
    p.add_argument('--ref-name', default=os.environ.get('GITHUB_REF_NAME'),
                   help='Tag or branch name (default: $GITHUB_REF_NAME)')
    p.add_argument('--repository',
                   help='owner/repo (default: $GITHUB_REPOSITORY)')
    p.add_argument('--index-only', action='store_true',
                   help='Build nothing; only regenerate the index')
    p.add_argument('--github-output',
                   default=os.environ.get('GITHUB_OUTPUT'),
                   help='File to append step outputs to (default: '
                        '$GITHUB_OUTPUT)')
    p.set_defaults(func=run_plan)

    e = subparsers.add_parser(
        "repo-ensure-release",
        help="Create the GitHub release for a tag if it does not exist.")
    e.add_argument('--tag', required=True)
    e.add_argument('--repository',
                   help='owner/repo (default: $GITHUB_REPOSITORY)')
    e.set_defaults(func=run_ensure_release)

    b = subparsers.add_parser(
        "repo-prune-branch-assets",
        help="Delete a branch build's assets for architectures no longer "
             "built.")
    b.add_argument('--package-name', required=True)
    b.add_argument('--version', required=True,
                   help='Branch version, e.g. main')
    b.add_argument('--architectures', required=True,
                   help='JSON list of the architectures built')
    b.add_argument('--repository',
                   help='owner/repo (default: $GITHUB_REPOSITORY)')
    b.set_defaults(func=run_prune_branch_assets)
