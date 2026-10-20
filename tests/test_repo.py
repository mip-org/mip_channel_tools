"""Unit tests for repo-mode planning (MEP 10)."""

import json
import subprocess
import textwrap

import pytest

from mip_channel_tools import cli
from mip_channel_tools import repo
from mip_channel_tools.repo import (
    INDEX_RELEASE_TAG, PlanError, plan, release_tag_for_ref,
    version_from_ref)


def _write_mip_yaml(root, text):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'mip.yaml').write_text(textwrap.dedent(text), encoding='utf-8')
    return str(root)


BASIC = """
    name: chunkie
    version: "1.2.0"
    builds:
      - architectures: [linux_x86_64, macos_arm64, macos_x86_64]
      - architectures: [any]
"""


# --- version_from_ref -------------------------------------------------------

@pytest.mark.parametrize('ref_type, ref_name, expected', [
    ('tag', 'v1.2.0', '1.2.0'),
    ('tag', '1.2.0', '1.2.0'),
    ('tag', 'v2', '2'),
    ('tag', 'v1.2.0-beta', 'v1.2.0-beta'),
    ('tag', 'release', 'release'),
    ('tag', 'v', 'v'),
    ('tag', 'vv1.0', 'vv1.0'),
    ('tag', 'v1.', 'v1.'),
    ('branch', 'main', 'main'),
    ('branch', 'v1.2.0', 'v1.2.0'),
])
def test_version_from_ref(ref_type, ref_name, expected):
    assert version_from_ref(ref_type, ref_name) == expected


@pytest.mark.parametrize('ref_type, ref_name', [
    ('branch', 'feature/x'),
    ('tag', 'a b'),
    ('branch', ''),
    ('tag', '..'),
    ('pull_request', '12/merge'),
])
def test_version_from_ref_rejects(ref_type, ref_name):
    with pytest.raises(PlanError):
        version_from_ref(ref_type, ref_name)


def test_release_tag_for_ref():
    assert release_tag_for_ref('tag', 'v1.2.0') == 'v1.2.0'
    assert release_tag_for_ref('branch', 'main') == INDEX_RELEASE_TAG
    with pytest.raises(PlanError, match='reserved'):
        release_tag_for_ref('tag', INDEX_RELEASE_TAG)


# --- plan ---------------------------------------------------------------------

def test_plan_tag_build(tmp_path):
    src = _write_mip_yaml(tmp_path / 'pkgsrc', BASIC)
    result = plan(src, 'fastalgorithms/chunkie', 'tag', 'v1.2.0')
    assert result == {
        'package_name': 'chunkie',
        'build': True,
        # Intersected with the CI set (macos_x86_64 dropped) and ordered.
        'matrix': ['any', 'linux_x86_64', 'macos_arm64'],
        'version': '1.2.0',
        'release_tag': 'v1.2.0',
    }


def test_plan_branch_build(tmp_path):
    src = _write_mip_yaml(tmp_path / 'pkgsrc', BASIC)
    result = plan(src, 'fastalgorithms/chunkie', 'branch', 'main')
    assert result['version'] == 'main'
    assert result['release_tag'] == INDEX_RELEASE_TAG


def test_plan_tag_version_mismatch_fails(tmp_path):
    src = _write_mip_yaml(tmp_path / 'pkgsrc', BASIC)
    with pytest.raises(PlanError, match="must equal mip.yaml version '1.2.0'"):
        plan(src, 'fastalgorithms/chunkie', 'tag', 'v1.3.0')


def test_plan_non_numeric_tag_ignores_yaml_version(tmp_path):
    src = _write_mip_yaml(tmp_path / 'pkgsrc', BASIC)
    result = plan(src, 'fastalgorithms/chunkie', 'tag', 'v1.3.0-beta')
    assert result['version'] == 'v1.3.0-beta'
    assert result['release_tag'] == 'v1.3.0-beta'


def test_plan_blank_yaml_version_takes_tag(tmp_path):
    src = _write_mip_yaml(tmp_path / 'pkgsrc', """
        name: chunkie
        builds:
          - architectures: [any]
    """)
    result = plan(src, 'fastalgorithms/chunkie', 'tag', '3.0')
    assert result['version'] == '3.0'


def test_plan_non_numeric_yaml_version_fails(tmp_path):
    src = _write_mip_yaml(tmp_path / 'pkgsrc', """
        name: chunkie
        version: main
        builds:
          - architectures: [any]
    """)
    with pytest.raises(PlanError, match='blank or numeric'):
        plan(src, 'fastalgorithms/chunkie', 'branch', 'main')


@pytest.mark.parametrize('name, repository', [
    ('chunk_ie', 'o/chunk-ie'),
    ('Chunk-IE', 'o/chunk_ie'),
    ('chunkie', 'o/ChunkIE'),
])
def test_plan_name_equivalence(tmp_path, name, repository):
    src = _write_mip_yaml(tmp_path / 'pkgsrc', f"""
        name: {name}
        builds:
          - architectures: [any]
    """)
    assert plan(src, repository, 'branch', 'main')['package_name'] == name


def test_plan_name_mismatch_fails(tmp_path):
    src = _write_mip_yaml(tmp_path / 'pkgsrc', BASIC)
    with pytest.raises(PlanError, match='does not match repository'):
        plan(src, 'fastalgorithms/chunkie-matlab', 'tag', 'v1.2.0')


def test_plan_missing_mip_yaml_fails(tmp_path):
    (tmp_path / 'pkgsrc').mkdir()
    with pytest.raises(PlanError, match='no mip.yaml'):
        plan(str(tmp_path / 'pkgsrc'), 'o/chunkie', 'branch', 'main')


def test_plan_no_buildable_arch_fails(tmp_path):
    src = _write_mip_yaml(tmp_path / 'pkgsrc', """
        name: chunkie
        builds:
          - architectures: [macos_x86_64]
    """)
    with pytest.raises(PlanError, match='no architecture CI can build'):
        plan(src, 'o/chunkie', 'branch', 'main')


def test_plan_index_only_skips_version_checks(tmp_path):
    # Index-only runs build nothing, so a ref that could not be a version
    # (or a mismatching tag) does not matter.
    src = _write_mip_yaml(tmp_path / 'pkgsrc', BASIC)
    result = plan(src, 'o/chunkie', 'branch', 'feature/x', index_only=True)
    assert result == {
        'package_name': 'chunkie', 'build': False, 'matrix': [],
        'version': '', 'release_tag': '',
    }


def test_repo_plan_cli_writes_outputs(tmp_path):
    src = _write_mip_yaml(tmp_path / 'pkgsrc', BASIC)
    out = tmp_path / 'github_output'
    rc = cli.main([
        'repo-plan', '--source-dir', src, '--ref-type', 'tag',
        '--ref-name', 'v1.2.0', '--repository', 'fastalgorithms/chunkie',
        '--github-output', str(out)])
    assert rc == 0
    lines = dict(line.split('=', 1)
                 for line in out.read_text().splitlines())
    assert lines['build'] == 'true'
    assert json.loads(lines['matrix']) == ['any', 'linux_x86_64', 'macos_arm64']
    assert lines['version'] == '1.2.0'
    assert lines['release_tag'] == 'v1.2.0'
    assert lines['package_name'] == 'chunkie'


def test_repo_plan_cli_fails_on_error(tmp_path):
    src = _write_mip_yaml(tmp_path / 'pkgsrc', BASIC)
    out = tmp_path / 'github_output'
    rc = cli.main([
        'repo-plan', '--source-dir', src, '--ref-type', 'tag',
        '--ref-name', 'v9.9.9', '--repository', 'fastalgorithms/chunkie',
        '--github-output', str(out)])
    assert rc == 1
    assert not out.exists()


# --- ensure_release -------------------------------------------------------------

class FakeGh:
    """Records gh calls; `releases` maps tag -> {'isPrerelease', 'isDraft'}."""

    def __init__(self, releases):
        self.releases = dict(releases)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        assert cmd[:2] == ['gh', 'release']
        action, tag = cmd[2], cmd[3]
        if action == 'view':
            if tag not in self.releases:
                return subprocess.CompletedProcess(cmd, 1, '', 'not found')
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps(self.releases[tag]), '')
        if action == 'create':
            self.releases[tag] = {'isPrerelease': '--prerelease' in cmd,
                                  'isDraft': False}
            return subprocess.CompletedProcess(cmd, 0, '', '')
        if action == 'edit':
            self.releases[tag]['isPrerelease'] = True
            return subprocess.CompletedProcess(cmd, 0, '', '')
        raise AssertionError(cmd)


def test_ensure_release_creates_index_prerelease(monkeypatch):
    gh = FakeGh({})
    monkeypatch.setattr(repo.subprocess, 'run', gh)
    repo.ensure_release('o/chunkie', INDEX_RELEASE_TAG)
    create = [c for c in gh.calls if c[2] == 'create']
    assert len(create) == 1
    assert '--prerelease' in create[0]
    assert '--verify-tag' not in create[0]


def test_ensure_release_creates_tag_release_without_new_tag(monkeypatch):
    gh = FakeGh({})
    monkeypatch.setattr(repo.subprocess, 'run', gh)
    repo.ensure_release('o/chunkie', 'v1.2.0')
    [create] = [c for c in gh.calls if c[2] == 'create']
    assert '--verify-tag' in create
    assert '--prerelease' not in create


def test_ensure_release_existing_is_left_alone(monkeypatch):
    gh = FakeGh({'v1.2.0': {'isPrerelease': False, 'isDraft': False}})
    monkeypatch.setattr(repo.subprocess, 'run', gh)
    repo.ensure_release('o/chunkie', 'v1.2.0')
    assert [c[2] for c in gh.calls] == ['view']


def test_ensure_release_restores_index_prerelease(monkeypatch):
    gh = FakeGh({INDEX_RELEASE_TAG: {'isPrerelease': False,
                                     'isDraft': False}})
    monkeypatch.setattr(repo.subprocess, 'run', gh)
    repo.ensure_release('o/chunkie', INDEX_RELEASE_TAG)
    assert [c[2] for c in gh.calls] == ['view', 'edit']
    assert gh.releases[INDEX_RELEASE_TAG]['isPrerelease']


# --- stale branch assets -----------------------------------------------------

def test_stale_branch_assets():
    assets = [
        'my_pkg-main-any.mhl', 'my_pkg-main-any.mhl.mip.json',
        'my_pkg-main-linux_x86_64.mhl', 'my_pkg-main-linux_x86_64.mhl.mip.json',
        'my_pkg-main-dev-any.mhl',            # branch main-dev, not main
        'my_pkg-other-any.mhl',               # another branch
        'index.json',
    ]
    stale = repo.stale_branch_assets(
        assets, 'my-pkg', 'main', ['linux_x86_64'])
    assert stale == ['my_pkg-main-any.mhl', 'my_pkg-main-any.mhl.mip.json']


def test_prune_branch_assets_deletes_stale(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[2] == 'view':
            out = json.dumps({'assets': [
                {'name': 'p-main-any.mhl'}, {'name': 'p-main-any.mhl.mip.json'},
                {'name': 'p-main-macos_arm64.mhl'}, {'name': 'index.json'}]})
            return subprocess.CompletedProcess(cmd, 0, out, '')
        assert cmd[2] == 'delete-asset'
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(repo.subprocess, 'run', fake_run)
    stale = repo.prune_branch_assets('o/p', 'p', 'main', ['macos_arm64'])
    assert stale == ['p-main-any.mhl', 'p-main-any.mhl.mip.json']
    deleted = [c[4] for c in calls if c[2] == 'delete-asset']
    assert deleted == stale
