# mip-channel-tools

The shared build engine for MIP package channels. A channel repo holds only its
own `packages/` and `site/` plus thin caller workflows; everything else — the
GitHub Actions logic, the Python CLI, the MATLAB build scripts, the MEX configs,
the vcpkg triplets, and the developer notes — lives here so every channel shares
one copy.

This was extracted from the channel repos (it previously lived under their
`tools/` directory and duplicated `scripts/`, `mexopts/`, etc.).

## Layout

- `.github/workflows/` — **reusable workflows** (`workflow_call`) that channels
  invoke: `build-package`, `assemble-index`, `push-build`, `scheduled-build`,
  `build-request`, `submit-package-request`, and `repo-build` (for a package
  published from its own repository; see below).
- `src/mip_channel_tools/` — the `mip-channel` CLI (Python package).
- `scripts/` — MATLAB build helpers (`bundle_one.m`, `test_one.m`, ...).
- `mexopts/` — MEX compiler configs per architecture.
- `vcpkg-triplets/` — shared vcpkg overlay triplets (Windows native-dep builds).
- `notes/` — `LOCAL-BUILD.md`, the local-build guide; all other developer
  notes on the build system live in [mip-org/devnotes](https://github.com/mip-org/devnotes).
- `adding_a_package.md` — guide for adding a package to a channel.

## Usage

A single CLI with subcommands, exposed both as the `mip-channel` console
script and as a runnable module. Workflows use the module form
(`python -m mip_channel_tools ...`) because it avoids console-script PATH and
shebang issues in the Linux build container; locally either works:

```bash
mip-channel --help
python -m mip_channel_tools --help

mip-channel prepare --package-path packages/<name>/<release> --architecture <arch>
mip-channel package-setup --architecture <arch>
mip-channel local-build --package-path packages/<name>/<release> [--architecture <arch>]
mip-channel upload [--mhl build/bundled/<file>.mhl]
mip-channel assemble-index [--repo-root .]
mip-channel build-request validate --output-file <path> [--title-file <path>]
mip-channel build-request apply --dispatch-file <path> [--errors-file <path>]
mip-channel submit-package-request validate --output-file <path>
mip-channel submit-package-request resolve --dispatch-file <path> [--errors-file <path>] [--for-promotion]
mip-channel submit-package-request spec --output-file <path>
mip-channel affected --changed-files <path> --dispatch-file <path>
mip-channel scheduled-check --dispatch-file <path> [--summary-file <path>]

# repo mode (a package published from its own repository)
mip-channel repo-plan --source-dir <checkout> --ref-type tag|branch --ref-name <name> [--index-only]
mip-channel repo-ensure-release --tag <tag>
mip-channel prepare --source-dir <checkout> --version <version> --release-tag <tag> [--commit <sha>] --architecture <arch>
mip-channel assemble-index --repo-mode --package-name <name> [--mip-compatibility-floor <version>] [--no-upload]
```

Commands that read the channel tree (`assemble-index`, `affected`,
`scheduled-check`, `build-request`) take `--repo-root` (default: the current
directory), so they must be run from, or pointed at, the channel checkout.

A channel may place an optional `channel.yaml` at its repo root. The one
recognized key is `mip_compatibility_floor` (a numeric version string, e.g.
`"1.2.0"`); `assemble-index` copies it into `index.json` as a top-level
field, and the mip client tells users an update is required when their
installed mip is older.

For local development against a checkout:

```bash
python -m pip install -e .
```

## Local builds (architectures CI can't build)

CI builds `linux_x86_64`, `macos_arm64`, `windows_x86_64`, and `numbl_wasm`. It
cannot build `macos_x86_64` (Intel Mac): MathWorks dropped Intel-Mac support
from `mpm`, so MATLAB can't be installed on an Intel-macOS runner. A maintainer
with an Intel Mac fills that gap with `local-build`, which runs the same
`prepare → package-setup → bundle_one → test_one` steps the reusable
`build-package` workflow runs — and, **with `--publish`**, uploads the `.mhl` and
triggers the channel's Assemble Index workflow so the new architecture enters the
index, exactly as CI does for the others. Publishing is opt-in: without
`--publish` it only builds and tests. See `notes/LOCAL-BUILD.md`.

The usual entry point is the channel's thin `scripts/local_build.sh`, which (in
the thin-caller spirit of the workflows) just clones this engine into the
channel and delegates here:

```bash
cd mip-core                                            # checkout on the Intel Mac
./scripts/local_build.sh packages/<name>/<release>            # build + test
./scripts/local_build.sh packages/<name>/<release> --publish  # ... and release
```

Architecture defaults to the host native arch (Intel Mac → `macos_x86_64`).
`macos_x86_64` is **not** added to the CI-dispatch architecture set
(`SUPPORTED_ARCHITECTURES`): issue-driven and scheduled builds still skip it
because no runner can build it. `local-build` is the one path that produces it,
and `assemble-index` ingests every release asset regardless of architecture, so
the locally-built `.mhl` appears in the index with no further changes.

## Use from a channel

A channel's `.github/workflows/*.yml` are thin **callers**: each owns only its
event triggers (and concurrency) and delegates to the matching reusable workflow
here with `secrets: inherit`. For example a channel's `build-package.yml`:

```yaml
on:
  workflow_dispatch:
    inputs:
      package_path: { required: true, type: string }
      architecture: { required: true, type: string }  # any, linux_x86_64, macos_arm64, windows_x86_64, numbl_wasm
      force:        { type: boolean, default: false }
permissions:
  contents: write
  pages: write
  id-token: write
jobs:
  build-package:
    uses: mip-org/mip_channel_tools/.github/workflows/build-package.yml@main
    with:
      package_path: ${{ inputs.package_path }}
      architecture: ${{ inputs.architecture }}
      force: ${{ inputs.force }}
    secrets: inherit
```

Each reusable workflow checks out the **calling channel** by default (for
`packages/` and `site/`) and checks out **this repo at the called ref**
(`repository: ${{ job.workflow_repository }}`, `ref: ${{ job.workflow_sha }}`)
into `mip_channel_tools/` for the scripts and the Python package — so a build
step can `addpath('mip_channel_tools/scripts')` and `pip install` the package.

To point a channel at a different tooling branch or tag, edit the `@<ref>` on
the caller's `uses:` line.

> The tooling checkout needs `git` on PATH. GitHub-hosted runners have it; the
> Linux build container (`mathworks/matlab-deps:*-ubi8`) installs it
> (`dnf install -y git`) before checkout, so it is available there too.

## Use from a package's own repository

A package can also be published from its own GitHub repository, without a channel ([MEP 10](https://github.com/mip-org/meps/blob/main/meps/mep-0010.md)). The repository keeps `mip.yaml` at its root and adds one caller workflow, e.g. `.github/workflows/repo-build.yml`:

```yaml
name: mip
on:
  push:
    tags: ['v*']
    branches: [main]      # optional: publish a rolling "main" build
  workflow_dispatch:
permissions:
  contents: write
jobs:
  mip:
    uses: mip-org/mip_channel_tools/.github/workflows/repo-build.yml@main
    secrets: inherit      # optional: passes MIP_CACHE_TOKEN for the Windows vcpkg cache
```

The repository must be public. `repo-build.yml` runs three jobs:

1. **plan** determines the version from the triggering ref. A tag `v1.2.0` or `1.2.0` builds version `1.2.0` (the leading `v` is stripped only when the rest is numeric); any other tag is published under its literal name; a branch builds a version named after the branch. It fails before building if `mip.yaml`'s `name` does not match the repository name (compared case-insensitively, with `-` and `_` equivalent), or if `mip.yaml` declares a numeric `version` that differs from the tag's. The build matrix is the `builds[].architectures` of `mip.yaml` intersected with the architectures CI can build.
2. **build** runs `build-package.yml` with `mode: repo` once per architecture. The package is checked out into `pkgsrc/` at the triggering commit, which is recorded as the build's `commit_hash`. Tag builds attach their `.mhl` and `.mhl.mip.json` assets to the tag's GitHub release (created if only the tag exists). Branch builds attach them to the `mip-index` prerelease, replacing the previous build's assets. A build whose sidecar is already published with the same source hash and commit is skipped.
3. **index** regenerates `index.json` from the sidecars of all non-draft releases and uploads it to the `mip-index` prerelease, where the mip client fetches it from `https://github.com/<owner>/<repo>/releases/download/mip-index/index.json`. The index has the same schema as a channel index.

A `workflow_dispatch` from a tag rebuilds that tag, and one from a branch rebuilds that branch. To expose the reusable workflow's other inputs, declare them on the caller and pass them through:

```yaml
on:
  workflow_dispatch:
    inputs:
      index_only: { type: boolean, default: false }
      force:      { type: boolean, default: false }
jobs:
  mip:
    uses: mip-org/mip_channel_tools/.github/workflows/repo-build.yml@main
    with:
      index_only: ${{ inputs.index_only == true }}   # only regenerate index.json
      force: ${{ inputs.force == true }}             # rebuild even if published
      mip_compatibility_floor: '1.2.0'               # optional, as in channel.yaml
    secrets: inherit
```

`index_only` is useful after deleting a release, since the index is rebuilt from whatever releases exist. `macos_x86_64` is not built in repo mode, since `local-build` supports only channels.
