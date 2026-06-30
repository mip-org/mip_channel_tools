"""Unit tests for upload's .mip.json sha256 injection (UTF-8 safety)."""

import json
import subprocess
import sys

from mip_channel_tools.upload import _inject_sha256


def _write_json_utf8(path, obj):
    # Deterministic UTF-8 bytes with non-ASCII kept literal, as MATLAB writes
    # the .mip.json on the build runner.
    path.write_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def test_inject_sha256_preserves_non_ascii(tmp_path):
    mip = tmp_path / "x.mhl.mip.json"
    _write_json_utf8(mip, {"description": "A — B"})  # em-dash U+2014
    mhl = tmp_path / "x.mhl"
    mhl.write_bytes(b"hello")

    sha = _inject_sha256(str(mip), str(mhl))

    assert len(sha) == 64
    # The em-dash must still be a real em-dash when read back as UTF-8.
    data = json.loads(mip.read_bytes().decode("utf-8"))
    assert data["description"] == "A — B"
    assert data["mhl_sha256"] == sha


def test_inject_sha256_does_not_rely_on_default_encoding(tmp_path):
    # Regression for the windows_x86_64 false rebuild: on Windows, Python's
    # default open() encoding is windows-1252, which silently corrupts the
    # UTF-8 .mip.json. Run the code path under -X warn_default_encoding with
    # EncodingWarning promoted to an error, so any unencoded text open() in
    # _inject_sha256 fails deterministically on every platform.
    mip = tmp_path / "x.mhl.mip.json"
    _write_json_utf8(mip, {"description": "A — B"})
    mhl = tmp_path / "x.mhl"
    mhl.write_bytes(b"hello")

    script = (
        "from mip_channel_tools.upload import _inject_sha256\n"
        f"_inject_sha256({str(mip)!r}, {str(mhl)!r})\n"
    )
    result = subprocess.run(
        [sys.executable, "-X", "warn_default_encoding",
         "-W", "error::EncodingWarning", "-c", script],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
