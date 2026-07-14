# Build-system notes

Developer notes on the channel build engine — the parts that document this
repo's own files, configs, and operation.

General MATLAB/toolchain/OS compatibility notes that used to live here
(`MATLAB-GCC.md`, `MATLAB-GLIBC.md`, `MEX-RUNTIME-LIBS.md`,
`MACOS-DEPLOYMENT-TARGET.md`, `MATLAB-MINGW.md`) moved to
[mip-org/devnotes](https://github.com/mip-org/devnotes), the mip project's
developer knowledge base.

| Note | What it covers |
|---|---|
| [MEXOPTS.md](MEXOPTS.md) | Catalog of how the `mexopts/` XMLs diverge from the MathWorks stock files, with reasoning |
| [MACOS-MEX-CPP-LINKER.md](MACOS-MEX-CPP-LINKER.md) | Why the macOS `clang++.xml`/`g++.xml` pass `-ld_classic`, and what to do when Apple removes it |
| [LINUX-LIBSTDCXX-ASNEEDED.md](LINUX-LIBSTDCXX-ASNEEDED.md) | Link failure from gcc-toolset's split `libstdc++` with `-Wl,--as-needed` |
| [WINDOWS-TAR-XZ.md](WINDOWS-TAR-XZ.md) | Windows `.tar.xz` extraction hangs with System32 bsdtar; how package `compile.m` scripts should fetch archives |
| [LOCAL-BUILD.md](LOCAL-BUILD.md) | Building/publishing architectures CI can't build (Intel Mac) via `local-build` |
| [MIP-YAML-BUILDS.md](MIP-YAML-BUILDS.md) | Design discussion: per-architecture `builds:` sections in `mip.yaml` |
