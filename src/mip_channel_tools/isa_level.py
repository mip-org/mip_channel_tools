#!/usr/bin/env python3
"""Verify a built package uses no instructions above its target x86-64 ISA level.

A `linux_x86_64_vN` build must contain only instructions within psABI level N,
or it SIGILLs on the very CPUs that level claims to support. This statically
disassembles every ELF in the bundled `.mhl` (via `objdump`) and fails if any
uses an ISA tier above the target. It is runner-CPU-independent — unlike the
runtime test, which is skipped when the runner can't execute the level — and it
catches a compile script that lets the target `-march` escape (e.g. finufft's
old `FINUFFT_ARCH_FLAGS=native` default, which baked in the build runner's ISA).

Only the SIGILL-prone AVX boundaries are classified:
  - AVX / AVX2 (VEX `v*` mnemonics, 128/256-bit) -> tier 3
  - AVX-512 (a zmm register or an opmask k1..k7) -> tier 4
SSE2 baseline and SSE3/SSE4 read as tier 1; GCC emits only SSE2 for
`-march=x86-64`, so a real regression surfaces as AVX/AVX-512.

  target v1 / v2 -> forbid tier >= 3 (no AVX, no AVX-512)
  target v3      -> forbid tier 4    (no AVX-512)
  target v4      -> unrestricted

Applies to `linux_x86_64` and its `_vN` levels only (it needs `objdump` and an
ELF target); any other architecture is a no-op pass.
"""

import os
import re
import subprocess
import sys
import tempfile
import zipfile


# Operand markers unique to AVX-512 (EVEX) encodings: a 512-bit zmm register or
# an opmask register k1..k7 (k0 means "no mask" and is not a reliable signal).
_AVX512_RE = re.compile(r'%zmm[0-9]|%k[1-7]\b')

# The only x86 mnemonics that start with 'v' but are NOT VEX/AVX: privileged
# segment and virtualization opcodes, which cannot appear in a userspace MEX.
# Any other leading-'v' mnemonic reliably signals AVX.
_NON_AVX_V = frozenset({
    'verr', 'verw', 'vmread', 'vmwrite', 'vmcall', 'vmlaunch', 'vmresume',
    'vmxoff', 'vmxon', 'vmptrld', 'vmptrst', 'vmclear', 'vmfunc', 'vmrun',
    'vmload', 'vmsave', 'vmmcall', 'vmgexit',
})


def target_level(architecture):
    """psABI level an arch must stay within, or None if the gate doesn't apply.

    linux_x86_64 -> 1 (baseline); linux_x86_64_vN -> N. Any other architecture
    (macOS, Windows, `any`, ...) returns None — there is no ELF/objdump gate.
    """
    if architecture == 'linux_x86_64':
        return 1
    m = re.fullmatch(r'linux_x86_64_v([234])', architecture or '')
    return int(m.group(1)) if m else None


def binary_tier(disasm):
    """Highest x86-64 ISA tier used in `objdump -d` output: 4, 3, or 1.

    `disasm` is the text of `objdump -d --no-show-raw-insn`, whose instruction
    lines look like "  <addr>:\\t<mnemonic> <operands>".
    """
    tier = 1
    for line in disasm.splitlines():
        tab = line.find('\t')
        if tab == -1:
            continue
        insn = line[tab + 1:]
        if _AVX512_RE.search(insn):
            return 4  # nothing ranks above v4
        parts = insn.split()
        if parts and parts[0].startswith('v') and parts[0] not in _NON_AVX_V:
            tier = 3
    return tier


def _objdump(path):
    return subprocess.run(
        ['objdump', '-d', '--no-show-raw-insn', path],
        capture_output=True, text=True,
    ).stdout


def _is_elf_binary(name):
    # Mirror the glibc gate's file set: MEX, shared objects, versioned .so.N.
    return name.endswith('.mexa64') or name.endswith('.so') or '.so.' in name


def _find_mhl(bundled_dir):
    mhls = ([f for f in sorted(os.listdir(bundled_dir)) if f.endswith('.mhl')]
            if os.path.isdir(bundled_dir) else [])
    if len(mhls) != 1:
        sys.exit(f"check-isa-level: expected exactly one .mhl in "
                 f"{bundled_dir}, found: {mhls}")
    return os.path.join(bundled_dir, mhls[0])


def run(args):
    level = target_level(args.architecture)
    if level is None:
        print(f"check-isa-level: {args.architecture!r} is not a linux_x86_64 "
              f"variant; nothing to gate.")
        return 0

    mhl = args.mhl or _find_mhl(args.bundled_dir)
    print(f"Gating {mhl} against x86-64-v{level}")

    fail = False
    found = 0
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(mhl) as z:
            z.extractall(tmp)
        for root, _, files in os.walk(tmp):
            for name in sorted(files):
                if not _is_elf_binary(name):
                    continue
                found += 1
                used = binary_tier(_objdump(os.path.join(root, name)))
                if used > level:
                    print(f"::error::{name} uses x86-64-v{used} instructions "
                          f"but targets x86-64-v{level}")
                    fail = True
                else:
                    print(f"  OK   {name}: within x86-64-v{level} "
                          f"(detected tier v{used})")

    if found == 0:
        print(f"::error::No ELF binaries (.mexa64/.so) found in {mhl} to gate")
        return 1
    if fail:
        print("ISA level gate FAILED")
        return 1
    print(f"ISA level gate passed: every shipped binary stays within "
          f"x86-64-v{level}")
    return 0


def register(subparsers):
    p = subparsers.add_parser(
        "check-isa-level",
        help="Fail if a bundled binary uses instructions above its target "
             "x86-64 SIMD level.")
    p.add_argument('--architecture', required=True,
                   help="Target architecture (e.g. linux_x86_64_v3).")
    p.add_argument('--mhl', default=None,
                   help="Path to the .mhl (default: the single .mhl in "
                        "--bundled-dir).")
    p.add_argument('--bundled-dir', default=os.path.join('build', 'bundled'),
                   help="Directory holding the bundled .mhl "
                        "(default: build/bundled).")
    p.set_defaults(func=run)
