"""Unit tests for prepare's source-hash computation."""

import os

from mip_channel_tools.prepare import compute_directory_hash


def _make_tree(root):
    """A tree with nested subdirectories, so relative paths contain a
    separator (the only thing whose encoding differs across platforms)."""
    os.makedirs(os.path.join(root, "matlab", "numbl", "mex_shim"))
    with open(os.path.join(root, "top.txt"), "wb") as f:
        f.write(b"top")
    with open(os.path.join(root, "matlab", "a.m"), "wb") as f:
        f.write(b"a")
    with open(os.path.join(root, "matlab", "numbl", "mex_shim", "mex.h"), "wb") as f:
        f.write(b"mex")


def test_directory_hash_is_path_separator_independent(tmp_path, monkeypatch):
    # A release folder with subdirectories must hash the same on a Windows
    # build runner and the Linux scheduled-build probe. Regression for the
    # daily false rebuild of windows_x86_64 (fmm3d, fmm2d, finufft, ...).
    root = tmp_path / "pkg"
    _make_tree(str(root))
    baseline = compute_directory_hash(str(root))

    # Simulate a Windows runner: os.path.relpath yields '\\'-separated
    # paths and os.sep is '\\'.
    import mip_channel_tools.prepare as prep
    real_relpath = os.path.relpath
    monkeypatch.setattr(
        prep.os.path, "relpath",
        lambda p, s: real_relpath(p, s).replace("/", "\\"))
    monkeypatch.setattr(prep.os, "sep", "\\")

    simulated_windows = compute_directory_hash(str(root))
    assert simulated_windows == baseline


def test_directory_hash_changes_with_content(tmp_path):
    # Sanity: the hash still reflects file content (the normalisation
    # must not flatten distinct trees to the same digest).
    a = tmp_path / "a"
    b = tmp_path / "b"
    _make_tree(str(a))
    _make_tree(str(b))
    with open(os.path.join(str(b), "matlab", "a.m"), "wb") as f:
        f.write(b"changed")
    assert compute_directory_hash(str(a)) != compute_directory_hash(str(b))


def test_fetch_source_remove_dirs_and_files(tmp_path):
    # fetch_source's removal pass runs after the source is in place; with no
    # fetch key in the recipe it operates on the existing tree, so it can be
    # tested without network. remove_dirs takes directories, remove_files
    # takes individual files (e.g. a large demo dataset inside a kept dir);
    # missing entries and wrong-kind entries are ignored.
    from mip_channel_tools.prepare import fetch_source

    root = tmp_path / "pkg"
    _make_tree(str(root))
    os.makedirs(os.path.join(str(root), "example"))
    with open(os.path.join(str(root), "example", "data.mat"), "wb") as f:
        f.write(b"x" * 100)
    with open(os.path.join(str(root), "matlab", "huge.mat"), "wb") as f:
        f.write(b"x" * 100)

    fetch_source({"source": {
        "remove_dirs": ["example", "no_such_dir", "top.txt"],
        "remove_files": ["matlab/huge.mat", "no/such.file", "matlab"],
    }}, str(root))

    assert not os.path.exists(os.path.join(str(root), "example"))
    assert not os.path.exists(os.path.join(str(root), "matlab", "huge.mat"))
    # untouched: kept files, a file listed in remove_dirs, a dir in remove_files
    assert os.path.exists(os.path.join(str(root), "top.txt"))
    assert os.path.exists(os.path.join(str(root), "matlab", "a.m"))


# --- Channel version rules (mip-org/mip#315) --------------------------------

import pytest

from mip_channel_tools.prepare import (
    is_numeric_version, validate_channel_version_rules)


def _write_mip_yaml(tmp_path, version_line):
    mip_yaml = tmp_path / "mip.yaml"
    mip_yaml.write_text(f"name: mypkg\n{version_line}\n")
    return str(mip_yaml)


def test_is_numeric_version():
    assert is_numeric_version("1")
    assert is_numeric_version("1.2.3")
    assert not is_numeric_version("")
    assert not is_numeric_version("main")
    assert not is_numeric_version("v1.2.3")
    assert not is_numeric_version("1.2.3-beta")
    assert not is_numeric_version("1e3")
    assert not is_numeric_version("1.")


def test_recipe_must_not_carry_version(tmp_path):
    path = _write_mip_yaml(tmp_path, 'version: "1.2.3"')
    with pytest.raises(ValueError, match="must not contain a 'version'"):
        validate_channel_version_rules(path, {"version": "1.2.3"}, "1.2.3")


def test_mip_yaml_version_must_be_numeric(tmp_path):
    path = _write_mip_yaml(tmp_path, "version: main")
    with pytest.raises(ValueError, match="blank or numeric"):
        validate_channel_version_rules(path, {}, "main")


def test_numeric_release_dir_must_match_yaml_version(tmp_path):
    path = _write_mip_yaml(tmp_path, 'version: "1.2.3"')
    with pytest.raises(ValueError, match="must equal"):
        validate_channel_version_rules(path, {}, "2.0.0")


def test_numeric_release_dir_matching_yaml_version_ok(tmp_path):
    path = _write_mip_yaml(tmp_path, 'version: "1.2.3"')
    validate_channel_version_rules(path, {}, "1.2.3")


def test_non_numeric_release_dir_takes_precedence(tmp_path):
    # A branch-named release dir is allowed regardless of the yaml version
    # and regardless of what source.branch says.
    path = _write_mip_yaml(tmp_path, 'version: "1.2.3"')
    validate_channel_version_rules(
        path, {"source": {"branch": "develop"}}, "main")


def test_blank_yaml_version_allows_any_release_dir(tmp_path):
    path = _write_mip_yaml(tmp_path, "version:")
    validate_channel_version_rules(path, {}, "3.1.4")
    validate_channel_version_rules(path, {}, "master")


def test_unquoted_numeric_yaml_version_ok(tmp_path):
    # YAML coerces an unquoted 1.5 to a float; the rules still apply to
    # its string form.
    path = _write_mip_yaml(tmp_path, "version: 1.5")
    validate_channel_version_rules(path, {}, "1.5")
    with pytest.raises(ValueError, match="must equal"):
        validate_channel_version_rules(path, {}, "1.6")


# --- Repo mode (MEP 10) -------------------------------------------------------

from mip_channel_tools import cli
from mip_channel_tools import prepare as prepare_mod


def _make_repo(root):
    """A package checkout with a .git directory and a submodule gitlink."""
    os.makedirs(os.path.join(root, ".git", "objects"))
    with open(os.path.join(root, ".git", "HEAD"), "w") as f:
        f.write("ref: refs/heads/main\n")
    with open(os.path.join(root, "mip.yaml"), "w") as f:
        f.write('name: chunkie\nversion: "1.2.0"\n'
                'builds:\n  - architectures: [any]\n')
    os.makedirs(os.path.join(root, "src", "sub"))
    with open(os.path.join(root, "src", "a.m"), "w") as f:
        f.write("a")
    with open(os.path.join(root, "src", "sub", ".git"), "w") as f:
        f.write("gitdir: ../../.git/modules/sub\n")


def _prepare_repo(tmp_path, *extra):
    return cli.main([
        "prepare", "--source-dir", str(tmp_path / "pkgsrc"),
        "--version", "1.2.0", "--release-tag", "v1.2.0",
        "--commit", "abc123", "--architecture", "any",
        "--output-dir", str(tmp_path / "prepared"), *extra])


def test_repo_mode_prepare(tmp_path, monkeypatch):
    _make_repo(str(tmp_path / "pkgsrc"))
    seen = {}

    def fake_check(mhl_filename, source_hash, mip_yaml, release_version,
                   release_tag=None):
        seen.update(mhl=mhl_filename, tag=release_tag,
                    version=release_version)
        return False

    monkeypatch.setattr(prepare_mod, "check_existing_package", fake_check)
    assert _prepare_repo(tmp_path) == 0
    assert seen == {"mhl": "chunkie-1.2.0-any.mhl", "tag": "v1.2.0",
                    "version": "1.2.0"}

    out = tmp_path / "prepared" / "chunkie-1.2.0"
    assert (out / "mip.yaml").is_file()
    assert (out / "src" / "a.m").is_file()
    assert not (out / ".git").exists()
    assert not (out / "src" / "sub" / ".git").exists()
    assert (out / ".release_version").read_text() == "1.2.0"
    assert (out / ".commit_hash").read_text() == "abc123"
    tree_hash = compute_directory_hash(
        str(tmp_path / "pkgsrc"), exclude=(".git",))
    assert (out / ".source_hash").read_text() == \
        prepare_mod.combine_hashes(tree_hash, ["abc123"])


def test_repo_mode_prepare_skips_when_published(tmp_path, monkeypatch):
    _make_repo(str(tmp_path / "pkgsrc"))
    monkeypatch.setattr(prepare_mod, "check_existing_package",
                        lambda *a, **k: True)
    assert _prepare_repo(tmp_path) == 0
    assert not (tmp_path / "prepared" / "chunkie-1.2.0").exists()

    # --force bypasses the check.
    assert _prepare_repo(tmp_path, "--force") == 0
    assert (tmp_path / "prepared" / "chunkie-1.2.0").is_dir()


def test_repo_mode_prepare_arch_not_declared(tmp_path, monkeypatch):
    _make_repo(str(tmp_path / "pkgsrc"))
    monkeypatch.setattr(prepare_mod, "check_existing_package",
                        lambda *a, **k: False)
    rc = cli.main([
        "prepare", "--source-dir", str(tmp_path / "pkgsrc"),
        "--version", "1.2.0", "--release-tag", "v1.2.0",
        "--architecture", "linux_x86_64",
        "--output-dir", str(tmp_path / "prepared")])
    assert rc == 0
    assert not (tmp_path / "prepared").exists()


def test_repo_mode_prepare_version_mismatch(tmp_path):
    _make_repo(str(tmp_path / "pkgsrc"))
    with pytest.raises(ValueError, match="must equal"):
        cli.main([
            "prepare", "--source-dir", str(tmp_path / "pkgsrc"),
            "--version", "2.0.0", "--release-tag", "v2.0.0",
            "--architecture", "any",
            "--output-dir", str(tmp_path / "prepared")])


def test_repo_mode_prepare_requires_version_and_tag(tmp_path):
    _make_repo(str(tmp_path / "pkgsrc"))
    rc = cli.main([
        "prepare", "--source-dir", str(tmp_path / "pkgsrc"),
        "--architecture", "any"])
    assert rc == 2


def test_repo_mode_prepare_rejects_output_inside_source(tmp_path):
    _make_repo(str(tmp_path / "pkgsrc"))
    rc = cli.main([
        "prepare", "--source-dir", str(tmp_path / "pkgsrc"),
        "--version", "1.2.0", "--release-tag", "v1.2.0",
        "--architecture", "any",
        "--output-dir", str(tmp_path / "pkgsrc" / "build" / "prepared")])
    assert rc == 2


def test_directory_hash_exclude_ignores_git(tmp_path):
    root = tmp_path / "pkgsrc"
    _make_repo(str(root))
    before = compute_directory_hash(str(root), exclude=(".git",))
    with open(os.path.join(str(root), ".git", "HEAD"), "w") as f:
        f.write("changed\n")
    with open(os.path.join(str(root), "src", "sub", ".git"), "w") as f:
        f.write("changed\n")
    assert compute_directory_hash(str(root), exclude=(".git",)) == before
    # Without the exclusion (channel mode) .git content still counts.
    assert compute_directory_hash(str(root)) != before


def test_channel_mode_requires_package_path():
    assert cli.main(["prepare", "--architecture", "any"]) == 2


def test_check_existing_package_uses_given_release_tag(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/chunkie")
    urls = []

    class Resp:
        status_code = 404

    def fake_get(url, timeout):
        urls.append(url)
        return Resp()

    monkeypatch.setattr(prepare_mod.requests, "get", fake_get)
    prepare_mod.check_existing_package(
        "chunkie-main-any.mhl", "h", {}, "main", release_tag="mip-index")
    prepare_mod.check_existing_package(
        "chunkie-main-any.mhl", "h", {}, "main")
    assert urls == [
        "https://github.com/o/chunkie/releases/download/mip-index/"
        "chunkie-main-any.mhl.mip.json",
        "https://github.com/o/chunkie/releases/download/chunkie-main/"
        "chunkie-main-any.mhl.mip.json",
    ]
