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
  `build-request`, `submit-package-request`.
- `src/mip_channel_tools/` — the `mip-channel` CLI (Python package).
- `scripts/` — MATLAB build helpers (`bundle_one.m`, `test_one.m`, ...).
- `mexopts/` — MEX compiler configs per architecture.
- `vcpkg-triplets/` — shared vcpkg overlay triplets (Windows native-dep builds).
- `notes/` — developer notes on the build system.
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
mip-channel upload [--mhl build/bundled/<file>.mhl]
mip-channel assemble-index [--repo-root .]
mip-channel build-request validate --output-file <path> [--title-file <path>]
mip-channel build-request apply --dispatch-file <path> [--errors-file <path>]
mip-channel submit-package-request validate --output-file <path> [--title-file <path>]
mip-channel submit-package-request resolve --dispatch-file <path> [--errors-file <path>] [--for-promotion]
mip-channel submit-package-request spec --output-file <path>
mip-channel affected --changed-files <path> --dispatch-file <path>
mip-channel scheduled-check --dispatch-file <path> [--summary-file <path>]
```

Commands that read the channel tree (`assemble-index`, `affected`,
`scheduled-check`, `build-request`) take `--repo-root` (default: the current
directory), so they must be run from, or pointed at, the channel checkout.

For local development against a checkout:

```bash
python -m pip install -e .
```

## Use from a channel

A channel's `.github/workflows/*.yml` are thin **callers**: each owns only its
event triggers (and concurrency) and delegates to the matching reusable workflow
here with `secrets: inherit`. For example a channel's `build-package.yml`:

```yaml
on:
  workflow_dispatch:
    inputs:
      package_path: { required: true, type: string }
      architecture: { required: true, type: choice, options: [any, linux_x86_64, macos_arm64, windows_x86_64] }
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
