# mip-channel-tools

Build, index, and release tooling shared by MIP package channels. A channel's
GitHub Actions workflows call this package to prepare package sources, run
per-OS setup, upload bundled `.mhl` artifacts to GitHub Releases, assemble the
channel index, and parse issue-driven build requests.

This package was extracted from the channel repos (it previously lived under
their `tools/` directory) so every channel can depend on a single shared copy.

## Install

In a channel's CI, install straight from this repo with pip:

```bash
python -m pip install "git+https://github.com/mip-org/mip_channel_tools.git@main"
```

Replace `@main` with a tag (e.g. `@v0.1.0`) or branch to pin a specific
version. Channel workflows centralize this ref in a shared composite action
(see "Use from a channel" below).

For local development against a checkout:

```bash
python -m pip install -e .
```

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
mip-channel affected --changed-files <path> --dispatch-file <path>
mip-channel scheduled-check --dispatch-file <path> [--summary-file <path>]
```

Commands that read the channel tree (`assemble-index`, `affected`,
`scheduled-check`, `build-request`) take `--repo-root` (default: the current
directory), so they must be run from, or pointed at, the channel checkout.

## Use from a channel

Channels install this package in CI through a small local composite action
rather than repeating the install in every workflow. The reference channel
(`mip-org/mip-core`) defines `.github/actions/install-channel-tools/action.yml`
as the single source of truth for the repo URL and the installed ref:

```yaml
inputs:
  ref:
    default: main          # edit to develop against a different tooling branch
runs:
  using: composite
  steps:
    - shell: bash
      env:
        REPO: https://github.com/mip-org/mip_channel_tools.git
        REF: ${{ inputs.ref }}
      run: python -m pip install "git+${REPO}@${REF}"
```

Each workflow then references it after checking out the channel repo:

```yaml
- name: Install channel tooling
  uses: ./.github/actions/install-channel-tools
```

To point a channel at a different tooling branch or tag, edit the action's
`ref` input default — a single in-repo change that every workflow picks up.

> The `git+https` install needs `git` on PATH. GitHub-hosted runners have it;
> the Linux build container (`mathworks/matlab-deps:*-ubi8`) installs it
> (`dnf install -y git`) before checkout, so it is available there too.
