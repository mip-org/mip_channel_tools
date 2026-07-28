"""Unit tests for the queue-channel prune decision logic."""

import textwrap

from mip_channel_tools import prune_promoted as pp


MIP_YAML = textwrap.dedent("""
    name: foo-bar
    builds:
      - architectures: [any]
      - architectures: [linux_x86_64, macos_arm64]
""")


def _make_package(root, name, release, mip_yaml=MIP_YAML, extra=None):
    pkg = root / 'packages' / name / release
    pkg.mkdir(parents=True)
    (pkg / 'source.yaml').write_text('url: https://example.com\n')
    if mip_yaml is not None:
        (pkg / 'mip.yaml').write_text(mip_yaml)
    for rel, content in (extra or {}).items():
        (pkg / rel).write_text(content)
    return pkg


def _index(*entries):
    return {'packages': [
        {'name': n, 'version': v, 'architecture': a} for n, v, a in entries
    ]}


FULL_INDEX = _index(
    ('foo-bar', '1.0', 'any'),
    ('foo-bar', '1.0', 'linux_x86_64'),
    ('foo-bar', '1.0', 'macos_arm64'),
)


def _roots(tmp_path):
    staging = tmp_path / 'staging'
    channel = tmp_path / 'channel'
    staging.mkdir()
    channel.mkdir()
    return staging, channel


def test_identical_and_fully_indexed_is_pruned(tmp_path):
    staging, channel = _roots(tmp_path)
    _make_package(staging, 'foo-bar', '1.0')
    _make_package(channel, 'foo-bar', '1.0')
    prunable, kept = pp.find_prunable(
        str(staging), str(channel), 'mip-org/mip-core', FULL_INDEX)
    assert kept == []
    assert prunable == [{
        'package_path': 'packages/foo-bar/1.0',
        'release_tag': 'foo_bar-1.0',
    }]


def test_missing_architecture_is_kept(tmp_path):
    staging, channel = _roots(tmp_path)
    _make_package(staging, 'foo-bar', '1.0')
    _make_package(channel, 'foo-bar', '1.0')
    partial = _index(('foo-bar', '1.0', 'any'),
                     ('foo-bar', '1.0', 'linux_x86_64'))
    prunable, kept = pp.find_prunable(
        str(staging), str(channel), 'mip-org/mip-core', partial)
    assert prunable == []
    [(path, reason)] = kept
    assert path == 'packages/foo-bar/1.0'
    assert 'macos_arm64' in reason


def test_differing_content_is_kept(tmp_path):
    staging, channel = _roots(tmp_path)
    _make_package(staging, 'foo-bar', '1.0',
                  extra={'compile.m': 'disp(2)\n'})
    _make_package(channel, 'foo-bar', '1.0',
                  extra={'compile.m': 'disp(1)\n'})
    prunable, kept = pp.find_prunable(
        str(staging), str(channel), 'mip-org/mip-core', FULL_INDEX)
    assert prunable == []
    assert kept == [('packages/foo-bar/1.0', 'differs from mip-org/mip-core')]


def test_extra_file_here_is_kept(tmp_path):
    staging, channel = _roots(tmp_path)
    _make_package(staging, 'foo-bar', '1.0', extra={'new.m': 'x = 1;\n'})
    _make_package(channel, 'foo-bar', '1.0')
    prunable, kept = pp.find_prunable(
        str(staging), str(channel), 'mip-org/mip-core', FULL_INDEX)
    assert prunable == []
    assert kept == [('packages/foo-bar/1.0', 'differs from mip-org/mip-core')]


def test_not_on_channel_is_kept(tmp_path):
    staging, channel = _roots(tmp_path)
    _make_package(staging, 'foo-bar', '1.0')
    prunable, kept = pp.find_prunable(
        str(staging), str(channel), 'mip-org/mip-core', FULL_INDEX)
    assert prunable == []
    assert kept == [('packages/foo-bar/1.0', 'not on mip-org/mip-core')]


def test_no_mip_yaml_is_kept(tmp_path):
    staging, channel = _roots(tmp_path)
    _make_package(staging, 'foo-bar', '1.0', mip_yaml=None)
    _make_package(channel, 'foo-bar', '1.0', mip_yaml=None)
    prunable, kept = pp.find_prunable(
        str(staging), str(channel), 'mip-org/mip-core', FULL_INDEX)
    assert prunable == []
    assert kept == [('packages/foo-bar/1.0', 'no mip.yaml')]


def test_no_declared_architectures_is_kept(tmp_path):
    staging, channel = _roots(tmp_path)
    yaml_no_builds = 'name: foo-bar\n'
    _make_package(staging, 'foo-bar', '1.0', mip_yaml=yaml_no_builds)
    _make_package(channel, 'foo-bar', '1.0', mip_yaml=yaml_no_builds)
    prunable, kept = pp.find_prunable(
        str(staging), str(channel), 'mip-org/mip-core', FULL_INDEX)
    assert prunable == []
    assert kept == [('packages/foo-bar/1.0', 'declares no architectures')]


def test_index_name_falls_back_to_folder_name(tmp_path):
    staging, channel = _roots(tmp_path)
    yaml_nameless = 'builds:\n  - architectures: [any]\n'
    _make_package(staging, 'plain', 'main', mip_yaml=yaml_nameless)
    _make_package(channel, 'plain', 'main', mip_yaml=yaml_nameless)
    prunable, kept = pp.find_prunable(
        str(staging), str(channel), 'mip-org/mip-core',
        _index(('plain', 'main', 'any')))
    assert kept == []
    assert prunable == [{
        'package_path': 'packages/plain/main',
        'release_tag': 'plain-main',
    }]


def test_mixed_repo_classifies_each_release(tmp_path):
    staging, channel = _roots(tmp_path)
    _make_package(staging, 'foo-bar', '1.0')
    _make_package(channel, 'foo-bar', '1.0')
    _make_package(staging, 'foo-bar', '2.0')  # not promoted
    _make_package(staging, 'other', 'main',
                  mip_yaml='name: other\nbuilds:\n  - architectures: [any]\n',
                  extra={'a.m': 'x\n'})
    _make_package(channel, 'other', 'main',
                  mip_yaml='name: other\nbuilds:\n  - architectures: [any]\n',
                  extra={'a.m': 'y\n'})
    prunable, kept = pp.find_prunable(
        str(staging), str(channel), 'mip-org/mip-core', FULL_INDEX)
    assert [e['package_path'] for e in prunable] == ['packages/foo-bar/1.0']
    assert sorted(k[0] for k in kept) == [
        'packages/foo-bar/2.0', 'packages/other/main']


def test_dirs_identical_symlink_targets(tmp_path):
    a = tmp_path / 'a'
    b = tmp_path / 'b'
    for d in (a, b):
        d.mkdir()
        (d / 'f.m').write_text('same\n')
    (a / 'link').symlink_to('f.m')
    (b / 'link').symlink_to('f.m')
    assert pp.dirs_identical(str(a), str(b))
    (b / 'link').unlink()
    (b / 'link').symlink_to('other')
    assert not pp.dirs_identical(str(a), str(b))
