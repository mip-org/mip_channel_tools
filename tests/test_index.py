"""Unit tests for the optional channel.yaml mip_compatibility_floor index field."""

from mip_channel_tools.index import IndexAssembler, _is_numeric_version


def _make_assembler(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "someorg/mip-core")
    return IndexAssembler(repo_root=str(tmp_path))


def test_is_numeric_version():
    assert _is_numeric_version("1")
    assert _is_numeric_version("1.2.3")
    assert not _is_numeric_version("main")
    assert not _is_numeric_version("1.2.3-rc1")
    assert not _is_numeric_version("")
    assert not _is_numeric_version(None)


def test_no_channel_yaml_returns_none(tmp_path, monkeypatch):
    assembler = _make_assembler(tmp_path, monkeypatch)
    assert assembler._read_mip_compatibility_floor() is None


def test_reads_mip_compatibility_floor(tmp_path, monkeypatch):
    (tmp_path / "channel.yaml").write_text('mip_compatibility_floor: "1.2.0"\n',
                                           encoding="utf-8")
    assembler = _make_assembler(tmp_path, monkeypatch)
    assert assembler._read_mip_compatibility_floor() == "1.2.0"


def test_unquoted_yaml_number_is_stringified(tmp_path, monkeypatch):
    # YAML parses an unquoted 1.2 as a float; the index field must still be
    # a version string.
    (tmp_path / "channel.yaml").write_text("mip_compatibility_floor: 1.2\n",
                                           encoding="utf-8")
    assembler = _make_assembler(tmp_path, monkeypatch)
    assert assembler._read_mip_compatibility_floor() == "1.2"


def test_missing_key_returns_none(tmp_path, monkeypatch):
    (tmp_path / "channel.yaml").write_text("description: a channel\n",
                                           encoding="utf-8")
    assembler = _make_assembler(tmp_path, monkeypatch)
    assert assembler._read_mip_compatibility_floor() is None


def test_non_numeric_value_ignored(tmp_path, monkeypatch):
    (tmp_path / "channel.yaml").write_text("mip_compatibility_floor: main\n",
                                           encoding="utf-8")
    assembler = _make_assembler(tmp_path, monkeypatch)
    assert assembler._read_mip_compatibility_floor() is None


def test_malformed_yaml_ignored(tmp_path, monkeypatch):
    (tmp_path / "channel.yaml").write_text("mip_compatibility_floor: [unclosed\n",
                                           encoding="utf-8")
    assembler = _make_assembler(tmp_path, monkeypatch)
    assert assembler._read_mip_compatibility_floor() is None
