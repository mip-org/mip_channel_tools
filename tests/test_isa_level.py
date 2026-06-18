"""Tests for mip_channel_tools.isa_level (run with `pytest`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mip_channel_tools.isa_level import binary_tier, target_level  # noqa: E402


def _disasm(*insns):
    # Mimic `objdump -d --no-show-raw-insn`: "  <addr>:\t<insn>".
    return "".join(f"  4011a{i}:\t{insn}\n" for i, insn in enumerate(insns))


class TestTargetLevel:
    def test_base_is_v1(self):
        assert target_level("linux_x86_64") == 1

    def test_simd_levels(self):
        assert target_level("linux_x86_64_v2") == 2
        assert target_level("linux_x86_64_v3") == 3
        assert target_level("linux_x86_64_v4") == 4

    def test_non_linux_x86_is_none(self):
        for arch in ("macos_arm64", "windows_x86_64", "windows_x86_64_v4",
                     "any", "", None):
            assert target_level(arch) is None


class TestBinaryTier:
    def test_sse_only_is_tier_1(self):
        # SSE2 + SSE4.2 (popcnt) must not register as AVX.
        assert binary_tier(_disasm("movsd %xmm0,(%rax)",
                                   "popcnt %rax,%rbx")) == 1

    def test_avx2_ymm_is_tier_3(self):
        assert binary_tier(_disasm("vaddpd %ymm0,%ymm1,%ymm2")) == 3

    def test_avx_scalar_xmm_is_tier_3(self):
        # VEX-encoded scalar FMA on xmm (no ymm) is still AVX.
        assert binary_tier(_disasm("vfmadd213sd %xmm0,%xmm1,%xmm2")) == 3

    def test_avx512_zmm_is_tier_4(self):
        assert binary_tier(_disasm("vaddpd %zmm0,%zmm1,%zmm2")) == 4

    def test_avx512_opmask_on_ymm_is_tier_4(self):
        assert binary_tier(_disasm("vmovupd %ymm0,(%rax){%k1}")) == 4

    def test_v_in_symbols_does_not_false_positive(self):
        # A 'v' only in a label/call-target name must not count as AVX.
        text = ("0000000000401136 <vtable_thunk>:\n"
                "  401136:\tcall   401050 <vprintf>\n")
        assert binary_tier(text) == 1

    def test_empty_disasm_is_tier_1(self):
        assert binary_tier("") == 1
