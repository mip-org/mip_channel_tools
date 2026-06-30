# Windows `.tar.xz` extraction hangs (System32 bsdtar)

Why Windows package builds must **not** extract `.tar.xz` with the system
`tar`, and why we fetch CGAL as `.zip` and call `tar.exe` by absolute path in
gptoolbox's `compile.m` (`fetch_archive`).

## Symptom

The gptoolbox Windows build hung for **6 hours** — the GitHub Actions job
ceiling — and was cancelled. The last log line was:

```
  [extract CGAL-6.0.1]
```

i.e. the CGAL download finished (24.4 MB, fine) and the very next step,
`tar -xf CGAL-6.0.1.tar.xz`, never returned. It looked like a download failure
but is an **extraction hang**.

It started when GitHub's `windows-latest` alias rolled from windows-2025 to
windows-2022 in mid-June 2026 (and is now permanent, because we deliberately
pin `windows-2022` — see [MATLAB-MINGW.md](MATLAB-MINGW.md) and the runner
pin in `build-package.yml`: `windows-latest` moved to VS2026, which MATLAB
can't use as a MEX compiler). Same gptoolbox source, same archive — only the
runner image changed.

## Root cause

`compile.m` extracts via `system('tar -xf …')`. On Windows that runs through
`cmd.exe`, which resolves `tar` to **`C:\Windows\System32\tar.exe`** — Microsoft's
bundled **bsdtar** (libarchive). On the current windows-2022 image its version
banner is:

```
bsdtar 3.8.4 - libarchive 3.8.4 zlib/1.2.5.f-ipp cng/2.0 libb2/bundled
```

Note what's **missing**: no `liblzma`. That build has **no built-in xz**. To
read a `.tar.xz` libarchive falls back to spawning an **external `xz -d -qq`**
and piping the archive through it — and that native-Windows child-process pipe
**deadlocks**, hanging forever. gzip and zip are compiled in (`zlib` is in the
banner), so they decode in-process and are fine.

windows-2025 shipped a bsdtar that could decode xz itself, which is why the
identical code worked there. This is purely an image difference, not an
"old vs new" libarchive thing (3.8.4 is current).

## Evidence (measured on a live windows-2022 runner)

Extracting `CGAL-6.0.1` two ways × two tars, 45 s watchdog:

| tar | `.tar.xz` | `.zip` |
| --- | --- | --- |
| System32 bsdtar (what MATLAB uses) | **HANG** — SIGKILLed at 45 s, 0 files | **OK, ~6 s**, 6788 files |
| MSYS2 GNU tar (`C:\msys64\usr\bin`) | OK, ~14 s | fail — "does not look like a tar archive" |

Isolating the bsdtar hang further:

- `bsdtar -tf foo.tar.xz` (just **listing**, no file writes) also hangs → it's
  the xz decompression, not file I/O.
- With `xz` removed from PATH, bsdtar fails **instantly**:
  `Error opening archive: Can't initialize filter; unable to run program "xz -d -qq"`
  → confirms it relies on an external `xz` and otherwise can't decode xz.
- `bsdtar -tf foo.tar.gz` → works in-process (zlib is built in).

## The fix

In `compile.m` (`fetch_archive`):

1. **Fetch CGAL as `CGAL-6.0.1.zip`, not `.tar.xz`.** The zip expands to the
   same `CGAL-6.0.1/` tree. Boost was already `.tar.gz` (gzip, in-process — no
   change). Never use `.tar.xz` for a Windows fetch.
2. **Extract with System32 bsdtar by absolute path**, not a bare `tar`:

   ```matlab
   tarExe = fullfile(getenv('SystemRoot'), 'System32', 'tar.exe');
   run_or_error(sprintf('"%s" -xf "%s" -C "%s"', tarExe, arc, work), ...);
   ```

   bsdtar handles both `.zip` and `.tar.gz` natively/in-process. The absolute
   path matters: on some setups a bare `tar` resolves to MSYS2's GNU tar first
   (this is the case in an MSYS2 shell, where `C:\msys64\usr\bin` is prepended),
   and **GNU tar cannot read `.zip` at all** — see the table above.

Verified on a live windows-2022 runner: the exact command MATLAB now issues,
`"C:\Windows\System32\tar.exe" -xf "…\src.zip" -C "…"`, extracts in ~6 s and
produces the `CGAL-6.0.1/` directory that `fetch_archive` asserts.

## Why not just use MSYS2 GNU tar?

GNU tar *does* extract `.tar.xz` (it spawns `xz` too, but both are MSYS2
processes sharing the POSIX-pipe layer, so it doesn't deadlock). But routing
MATLAB's `system('tar …')` to it is strictly more fragile — measured, all of
the following are required and the naive forms fail:

- **It needs `xz` on PATH.** GNU tar shells out to `xz.exe`; on CI, MATLAB's
  PATH has System32 but not `C:\msys64\usr\bin`, so a naive
  `C:\msys64\usr\bin\tar.exe -xf …` fails (no `xz`).
- **It needs POSIX-form paths.** Handed MATLAB's native `C:\…` paths, GNU tar
  reads the drive letter as an SSH host:
  `tar: Cannot connect to \C: resolve failed`. It only works with `/c/…` paths
  (or `C:/…` + `--force-local`).
- **It hardcodes an MSYS2 install location** (`C:\msys64`) — exactly the kind of
  host assumption that broke this build when the image changed.

So the MSYS2 route trades one host dependency for three. The `.zip` +
System32-bsdtar route depends only on tools Windows always ships, decoded
in-process. (MSYS2 ≠ MinGW: MSYS2 is the POSIX runtime/distribution that owns
this `tar`; the build's MinGW-w64 toolchain — [MATLAB-MINGW.md](MATLAB-MINGW.md)
— is separate.)

## Rule of thumb

On Windows, extract archives with the native System32 `tar.exe` (bsdtar) called
by absolute path, and prefer **`.zip` or `.tar.gz`** sources. Never `.tar.xz` —
the bundled bsdtar can't decode it in-process and the external-`xz` fallback
deadlocks.
