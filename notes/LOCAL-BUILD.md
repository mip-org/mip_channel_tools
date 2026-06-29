# Local builds — publishing an architecture CI can't build

CI builds four architectures: `linux_x86_64`, `macos_arm64`, `windows_x86_64`,
and `numbl_wasm`. It cannot build `macos_x86_64` (Intel Mac). The reason is
upstream: MathWorks removed Intel-Mac support from
[`mpm`](https://github.com/mathworks-ref-arch/matlab-dockerfile/blob/main/MPM.md),
the installer `matlab-actions/setup-matlab` uses, so MATLAB can no longer be
installed on an Intel-macOS GitHub runner. Apple Silicon (`macos_arm64`) is the
only macOS target CI can produce.

`mip-channel local-build` closes that gap. A maintainer with an Intel Mac (which
has a real MATLAB install) runs it from a channel checkout; it produces and
publishes the `macos_x86_64` `.mhl` using the **same engine** as CI, so the
result is indistinguishable from a CI build.

## What it does

It mirrors the reusable `build-package` workflow's per-`(package, arch)`
pipeline, calling the identical steps:

1. `mip-channel prepare`        — fetch source, overlay channel files, skip if
                                  already published (unless `--force`).
2. `mip-channel package-setup`  — run the package's `setup.macos` commands.
3. `bundle_one` (MATLAB)        — `mip.bundle` → `build/bundled/<...>.mhl`.
4. `test_one` (MATLAB)          — install / load / test the `.mhl`, and assert
                                  every shipped MEX was exercised (issue #16).
5. `mip-channel upload`         — push the `.mhl` + `.mip.json` to the package's
                                  GitHub Release via `gh`.
6. `gh workflow run assemble-index.yml` — rebuild the channel index + Pages.

Step 6 is why nothing else needs to change: `assemble-index` ingests **every**
`.mhl.mip.json` asset on each release regardless of architecture, so the
Intel-Mac `.mhl` enters the index automatically once the workflow reruns.

## Layering (no per-channel duplication)

Same thin-caller pattern as the workflows:

- **`mip-channel local-build`** (this repo) — all the pipeline logic.
- **`scripts/local_build.sh`** (this repo) — installs the tooling, then invokes
  the subcommand. The shared half.
- **`<channel>/scripts/local_build.sh`** — a ~15-line bootstrap that only clones
  this repo into the channel (gitignored, like CI's checkout) and delegates to
  the shared script above. Identical across channels.

So the per-channel committed code is just the irreducible bootstrap; everything
else lives here once.

## Two deliberate differences from CI

- **No self-containment "strip".** CI wipes the runner's entire toolchain
  (Xcode/Homebrew on macOS) before `test_one`, proving the `.mhl` carries its
  own runtime libraries. That is destructive and inappropriate on a developer's
  machine, so `local-build` skips it. Consequence: a MEX that links a
  non-bundled dev library can still pass the local test. Keep the package's
  `setup`/`compile` honest (static-link or bundle deps, per
  `MEX-RUNTIME-LIBS.md`); the strip gate remains a CI check on the
  architectures CI builds.
- **`mip` runtime comes from the machine's MATLAB path**, not a fresh clone.
  Pass `--mip-dir <checkout>` to `addpath` a specific `mip` (matching CI's
  pinned `mip-org/mip`) if you need exact parity.

`macos_x86_64` is intentionally **not** in `build_request.py`'s
`SUPPORTED_ARCHITECTURES`: issue-driven and scheduled CI builds must keep
skipping it (no runner can build it). `local-build` is the only producer of that
architecture, and it bypasses CI dispatch entirely.

## Usage

```bash
cd mip-core                                   # channel checkout on the Intel Mac
./scripts/local_build.sh packages/fmm2d/main  # arch auto-detected: macos_x86_64
```

Useful flags (forwarded to `mip-channel local-build`):

| Flag | Effect |
|---|---|
| `--architecture <a>` | Override the auto-detected host arch. |
| `--force`            | Rebuild even if a matching `.mhl` is already published. |
| `--no-test`          | Skip `test_one`. |
| `--no-publish`       | Build (and test) only; leave the `.mhl` in `build/bundled/`. |
| `--no-reindex`       | Don't trigger the Assemble Index workflow after upload. |
| `--matlab <path>`    | MATLAB executable (else `$MATLAB`, `matlab` on PATH, newest `/Applications/MATLAB_R*.app`). |
| `--mip-dir <path>`   | `addpath` a specific `mip` checkout in MATLAB. |

Prerequisites on the Intel Mac: MATLAB, `git`, `gh` (authenticated with push
access to the channel), and Python 3.8+.
