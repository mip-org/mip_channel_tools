#!/usr/bin/env python3
"""Decide which promoted packages a queue-style channel can prune.

A queue-style channel (e.g. mip-staging) holds packages/<name>/<release>
folders that get promoted into a real channel via `accept` on a submission
issue. After promotion, the folder here — and this channel's own published
builds of it — are leftovers. A leftover may be pruned iff BOTH:

  1. Its folder is byte-identical to packages/<name>/<release> on the
     promoted channel's main (compared against a local checkout). Anything
     that differs, or that the channel lacks, is work in progress for a
     (re-)promotion and is kept.
  2. The promoted channel finished publishing it: the channel's index
     lists an artifact for name@release for EVERY architecture the
     package's mip.yaml declares — i.e. each .mhl built and was indexed.

Emits a TSV of prunable packages (package_path <TAB> release_tag, where
release_tag is THIS channel's release for the package) for the calling
workflow to act on: delete the folder, delete this channel's release, and
reassemble the index. This command only decides; it deletes nothing.
"""

import filecmp
import os

import requests

from .prepare import architectures_from_mip_yaml, read_mip_yaml


def dirs_identical(dir_a, dir_b):
    """True iff the two directories hold the same relative paths with
    byte-identical contents (symlinks must have identical targets)."""

    def walk(root):
        entries = {}
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                path = os.path.join(dirpath, fname)
                entries[os.path.relpath(path, root)] = path
        return entries

    a_entries = walk(dir_a)
    b_entries = walk(dir_b)
    if set(a_entries) != set(b_entries):
        return False
    for rel, a_path in a_entries.items():
        b_path = b_entries[rel]
        a_link, b_link = os.path.islink(a_path), os.path.islink(b_path)
        if a_link != b_link:
            return False
        if a_link:
            if os.readlink(a_path) != os.readlink(b_path):
                return False
        elif not filecmp.cmp(a_path, b_path, shallow=False):
            return False
    return True


def fetch_channel_index(channel_repo):
    """Fetch the published index.json of a channel repo (owner/repo).

    Returns the parsed index dict, or None on any failure — callers must
    treat None as "cannot verify anything, prune nothing".
    """
    owner, repo_name = channel_repo.split('/', 1)
    url = f"https://{owner}.github.io/{repo_name}/index.json"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as e:
        print(f"Error fetching channel index {url}: {e}")
        return None


def indexed_architectures(index_data, name, release):
    """Architectures the channel index lists for name@release."""
    return {
        p.get('architecture')
        for p in index_data.get('packages', [])
        if p.get('name') == name and p.get('version') == release
    }


def release_tag_for(folder_name, release):
    """This channel's release tag for a package folder — same encoding as
    upload/assemble-index: '-' in the name becomes '_' in the tag."""
    return f"{folder_name.replace('-', '_')}-{release}"


def find_prunable(repo_root, channel_root, channel_repo, index_data):
    """Classify every packages/<name>/<release> under repo_root.

    Returns (prunable, kept): prunable is a list of
    {package_path, release_tag} dicts; kept is a list of
    (package_path, reason) tuples.
    """
    prunable = []
    kept = []
    packages_dir = os.path.join(repo_root, 'packages')
    if not os.path.isdir(packages_dir):
        return prunable, kept

    for name in sorted(os.listdir(packages_dir)):
        package_dir = os.path.join(packages_dir, name)
        if not os.path.isdir(package_dir):
            continue
        for release in sorted(os.listdir(package_dir)):
            release_dir = os.path.join(package_dir, release)
            if not os.path.isdir(release_dir):
                continue
            package_path = f"packages/{name}/{release}"

            channel_dir = os.path.join(channel_root, 'packages', name,
                                       release)
            if not os.path.isdir(channel_dir):
                kept.append((package_path, f"not on {channel_repo}"))
                continue
            if not dirs_identical(release_dir, channel_dir):
                kept.append((package_path,
                             f"differs from {channel_repo}"))
                continue

            mip_yaml_path = os.path.join(release_dir, 'mip.yaml')
            if not os.path.isfile(mip_yaml_path):
                kept.append((package_path, "no mip.yaml"))
                continue
            try:
                mip_yaml = read_mip_yaml(mip_yaml_path)
            except Exception as e:
                kept.append((package_path, f"unreadable mip.yaml: {e}"))
                continue
            declared = architectures_from_mip_yaml(mip_yaml)
            if not declared:
                kept.append((package_path, "declares no architectures"))
                continue

            index_name = mip_yaml.get('name') or name
            indexed = indexed_architectures(index_data, index_name, release)
            missing = sorted(declared - indexed)
            if missing:
                kept.append((
                    package_path,
                    f"not fully published on {channel_repo} "
                    f"(missing: {', '.join(missing)})"))
                continue

            prunable.append({
                'package_path': package_path,
                'release_tag': release_tag_for(name, release),
            })
    return prunable, kept


def run(args):
    index_data = fetch_channel_index(args.channel_repo)
    if index_data is None:
        print("Cannot verify published packages — pruning nothing.")
        return 1

    prunable, kept = find_prunable(
        args.repo_root, args.channel_root, args.channel_repo, index_data)

    summary_lines = []
    for entry in prunable:
        line = (f"prune: {entry['package_path']} "
                f"(release {entry['release_tag']})")
        print(line)
        summary_lines.append(f"- `{entry['package_path']}` — promoted and "
                             f"fully published on `{args.channel_repo}`")
    for package_path, reason in kept:
        print(f"keep:  {package_path} ({reason})")

    with open(args.prune_file, 'w') as f:
        for entry in prunable:
            f.write(f"{entry['package_path']}\t{entry['release_tag']}\n")
    if args.summary_file:
        with open(args.summary_file, 'w') as f:
            f.write('\n'.join(summary_lines) + ('\n' if summary_lines else ''))

    print(f"{len(prunable)} prunable, {len(kept)} kept.")
    return 0


def register(subparsers):
    parser = subparsers.add_parser(
        "prune-promoted",
        help="List packages promoted to (and fully published on) another "
             "channel, safe to prune from this queue channel.")
    parser.add_argument(
        '--channel-repo', required=True,
        help='Promoted channel repo, e.g. mip-org/mip-core.')
    parser.add_argument(
        '--channel-root', required=True,
        help='Path to a checkout of the promoted channel (for the '
             'byte-identity comparison).')
    parser.add_argument(
        '--repo-root', default='.',
        help='This channel checkout, holding packages/ (default: cwd).')
    parser.add_argument(
        '--prune-file', required=True,
        help='Output TSV: package_path <TAB> this channel\'s release tag.')
    parser.add_argument(
        '--summary-file', default=None,
        help='Optional markdown summary of prunable packages.')
    parser.set_defaults(func=run)
