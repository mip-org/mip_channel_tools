#!/usr/bin/env python3
"""Accumulate GitHub Release asset download counts into a lifetime total.

GitHub tracks a `download_count` per release asset, which is the best proxy
for "number of installs" — every `mip install <pkg>` pulls a .mhl from a
release asset. But that counter is *not* durable: rebuilding a (package,
architecture) pair re-uploads the asset with `gh release upload --clobber`,
which deletes the old asset (and its count) and starts a fresh one at zero.
Branch-tracking packages (master/main) get rebuilt by the daily scheduled
probe whenever upstream advances, so their raw counts reset silently.

This subcommand turns the resettable raw counter into a monotonic lifetime
total. On each run it reads the current raw counts, compares them against the
last snapshot, detects clobbers (the asset id changed, or the raw count
dropped), and carries the pre-reset total forward. The accumulated state is a
small JSON file, intended to live on a dedicated `stats` branch of the
channel repo and be updated by a scheduled workflow.

Per-asset record:
  lifetime  = base + last_raw       (monotonic total across all generations)
  base      = sum of finalized prior generations' final counts
  last_raw  = raw download_count observed for the current asset generation
  asset_id  = GitHub asset id of the current generation (clobber sentinel)

Usage:
  mip-channel download-stats --stats-file download-stats.json
  mip-channel download-stats --dry-run
"""

import json
import sys
import subprocess
from datetime import datetime, timezone

from .config import get_github_repo


def _now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _fetch_mhl_assets(repo):
    """Return a list of {tag, name, id, download_count, created_at} for every
    .mhl release asset in the repo, across all releases (paginated)."""
    jq = (
        '.[] | .tag_name as $t | .assets[] '
        '| select(.name | endswith(".mhl")) '
        '| {tag: $t, name: .name, id: .id, '
        'download_count: .download_count, created_at: .created_at}'
    )
    result = subprocess.run(
        ['gh', 'api', '--paginate', f'repos/{repo}/releases', '--jq', jq],
        capture_output=True, text=True, check=True
    )
    assets = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            assets.append(json.loads(line))
    return assets


def _load_stats(stats_file):
    try:
        with open(stats_file, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    return data.get('assets', {}) or {}


def _accumulate(existing, fetched, now):
    """Fold the fetched raw counts into the existing per-asset records.

    Records for assets that no longer appear (e.g. a deleted release) are
    left untouched so their historical lifetime is preserved.
    """
    assets = dict(existing)
    for asset in fetched:
        key = f"{asset['tag']}/{asset['name']}"
        raw = asset['download_count']
        aid = asset['id']
        entry = assets.get(key)
        if entry is None:
            assets[key] = {
                'lifetime': raw,
                'base': 0,
                'last_raw': raw,
                'asset_id': aid,
                'created_at': asset.get('created_at'),
                'first_seen': now,
                'updated': now,
            }
            continue
        base = entry.get('base', 0)
        # Clobber detection: a re-uploaded asset gets a new id; a dropped raw
        # count is the fallback signal if ids are ever unavailable.
        if aid != entry.get('asset_id') or raw < entry.get('last_raw', 0):
            base += entry.get('last_raw', 0)
        entry['base'] = base
        entry['last_raw'] = raw
        entry['lifetime'] = base + raw
        entry['asset_id'] = aid
        entry['created_at'] = asset.get('created_at')
        entry['updated'] = now
    return assets


def run(args):
    repo = get_github_repo()
    print(f"Fetching release asset download counts for {repo}...")
    try:
        fetched = _fetch_mhl_assets(repo)
    except subprocess.CalledProcessError as e:
        print(f"Error fetching releases: {e.stderr or e}", file=sys.stderr)
        return 1

    now = _now_iso()
    existing = {} if args.dry_run else _load_stats(args.stats_file)
    assets = _accumulate(existing, fetched, now)
    total = sum(a['lifetime'] for a in assets.values())

    output = {
        'generated': now,
        'repo': repo,
        'total_lifetime_downloads': total,
        'assets': dict(sorted(assets.items())),
    }

    print(f"  {len(fetched)} .mhl assets observed, "
          f"{len(assets)} tracked, {total} lifetime downloads")

    if args.dry_run:
        print(json.dumps(output, indent=2))
        return 0

    with open(args.stats_file, 'w') as f:
        json.dump(output, f, indent=2)
        f.write('\n')
    print(f"  Wrote {args.stats_file}")
    return 0


def register(subparsers):
    parser = subparsers.add_parser(
        "download-stats",
        help="Accumulate release asset download counts into a lifetime total.")
    parser.add_argument(
        '--stats-file', default='download-stats.json',
        help='Path to the accumulating stats JSON (default: download-stats.json)')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Print the computed stats without reading or writing the file.')
    parser.set_defaults(func=run)
