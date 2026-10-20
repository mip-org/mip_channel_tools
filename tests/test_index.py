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


# --- Repo mode (MEP 10) -------------------------------------------------------

import json
import os
import subprocess

from mip_channel_tools import index as index_mod


def _sidecar(name, version, arch, timestamp='2026-01-01T00:00:00Z'):
    return {'name': name, 'version': version, 'architecture': arch,
            'timestamp': timestamp}


class FakeGh:
    """Serves `gh release list/view/download/create/upload` from a dict
    {tag: {'isDraft': bool, 'isPrerelease': bool, 'assets': {name: obj}}}."""

    def __init__(self, releases):
        self.releases = releases
        self.uploaded = {}
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        assert cmd[:2] == ['gh', 'release']
        action = cmd[2]
        ok = lambda out='': subprocess.CompletedProcess(cmd, 0, out, '')
        if action == 'list':
            return ok(json.dumps([
                {'tagName': t, 'isDraft': r.get('isDraft', False)}
                for t, r in self.releases.items()]))
        tag = cmd[3]
        if action == 'view':
            if tag not in self.releases:
                if kwargs.get('check'):
                    raise subprocess.CalledProcessError(1, cmd)
                return subprocess.CompletedProcess(cmd, 1, '', 'not found')
            r = self.releases[tag]
            if '--json' in cmd and cmd[cmd.index('--json') + 1] == 'assets':
                return ok(json.dumps({'assets': [
                    {'name': n} for n in r.get('assets', {})]}))
            return ok(json.dumps({'isPrerelease': r.get('isPrerelease', False),
                                  'isDraft': r.get('isDraft', False)}))
        if action == 'download':
            name = cmd[cmd.index('--pattern') + 1]
            out_dir = cmd[cmd.index('--dir') + 1]
            with open(os.path.join(out_dir, name), 'w') as f:
                json.dump(self.releases[tag]['assets'][name], f)
            return ok()
        if action == 'create':
            self.releases[tag] = {'isPrerelease': '--prerelease' in cmd,
                                  'assets': {}}
            return ok()
        if action == 'upload':
            with open(cmd[4]) as f:
                self.uploaded[(tag, os.path.basename(cmd[4]))] = json.load(f)
            return ok()
        raise AssertionError(cmd)


def _repo_releases():
    return {
        'v1.0.0': {'assets': {
            'chunkie-1.0.0-any.mhl': None,
            'chunkie-1.0.0-any.mhl.mip.json':
                _sidecar('chunkie', '1.0.0', 'any'),
        }},
        # Same version under a second tag: newer build wins.
        '1.0.0': {'assets': {
            'chunkie-1.0.0-any.mhl.mip.json':
                _sidecar('chunkie', '1.0.0', 'any', '2026-02-01T00:00:00Z'),
        }},
        'v2.0.0': {'assets': {
            'chunkie-2.0.0-linux_x86_64.mhl.mip.json':
                _sidecar('chunkie', '2.0.0', 'linux_x86_64'),
            # A sidecar for some other package is not indexed.
            'other-2.0.0-any.mhl.mip.json': _sidecar('other', '2.0.0', 'any'),
        }},
        'v3.0.0': {'isDraft': True, 'assets': {
            'chunkie-3.0.0-any.mhl.mip.json':
                _sidecar('chunkie', '3.0.0', 'any'),
        }},
        'mip-index': {'isPrerelease': True, 'assets': {
            'index.json': {},
            'chunkie-main-any.mhl.mip.json': _sidecar('chunkie', 'main', 'any'),
        }},
    }


def test_repo_index_collects_and_uploads(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY', 'fastalgorithms/chunkie')
    gh = FakeGh(_repo_releases())
    monkeypatch.setattr(index_mod.subprocess, 'run', gh)

    assembler = IndexAssembler(repo_root=str(tmp_path))
    assert assembler.assemble_repo_index(
        'chunkie', mip_compatibility_floor='1.2.0')

    index = gh.uploaded[('mip-index', 'index.json')]
    assert set(index) == {'github_repo', 'packages', 'total_packages',
                          'last_updated', 'mip_compatibility_floor'}
    assert index['github_repo'] == 'fastalgorithms/chunkie'
    assert index['mip_compatibility_floor'] == '1.2.0'
    got = [(p['version'], p['architecture'], p['mhl_url'])
           for p in index['packages']]
    base = 'https://github.com/fastalgorithms/chunkie/releases/download'
    assert got == [
        # Non-numeric versions sort first, as in channel indexes.
        ('main', 'any', f'{base}/mip-index/chunkie-main-any.mhl'),
        ('1.0.0', 'any', f'{base}/1.0.0/chunkie-1.0.0-any.mhl'),
        ('2.0.0', 'linux_x86_64',
         f'{base}/v2.0.0/chunkie-2.0.0-linux_x86_64.mhl'),
    ]
    assert index['total_packages'] == 3
    assert index['packages'][0]['mip_json_url'] == \
        f'{base}/mip-index/chunkie-main-any.mhl.mip.json'
    # The existing mip-index release was used, not recreated.
    assert not [c for c in gh.calls if c[2] == 'create']


def test_repo_index_creates_index_release(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY', 'o/chunkie')
    releases = _repo_releases()
    del releases['mip-index']
    gh = FakeGh(releases)
    monkeypatch.setattr(index_mod.subprocess, 'run', gh)

    assert IndexAssembler(repo_root=str(tmp_path)).assemble_repo_index(
        'chunkie')
    assert gh.releases['mip-index']['isPrerelease']
    index = gh.uploaded[('mip-index', 'index.json')]
    assert 'mip_compatibility_floor' not in index
    assert len(index['packages']) == 2


def test_repo_index_no_upload_and_bad_floor(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY', 'o/chunkie')
    gh = FakeGh(_repo_releases())
    monkeypatch.setattr(index_mod.subprocess, 'run', gh)

    from mip_channel_tools import cli
    rc = cli.main(['assemble-index', '--repo-root', str(tmp_path),
                   '--repo-mode', '--package-name', 'chunkie',
                   '--mip-compatibility-floor', 'main', '--no-upload'])
    assert rc == 0
    assert gh.uploaded == {}
    with open(tmp_path / 'build' / 'repo-index' / 'index.json') as f:
        index = json.load(f)
    assert 'mip_compatibility_floor' not in index
    assert len(index['packages']) == 3


def test_repo_index_requires_package_name(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY', 'o/chunkie')
    from mip_channel_tools import cli
    assert cli.main(['assemble-index', '--repo-root', str(tmp_path),
                     '--repo-mode']) == 2
