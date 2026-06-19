"""Unit tests for download-stats accumulation across asset rebuilds."""

from mip_channel_tools.download_stats import _accumulate


def _asset(tag, name, aid, count, created='t'):
    return {'tag': tag, 'name': name, 'id': aid,
            'download_count': count, 'created_at': created}


KEY = 'foo-main/foo-main-any.mhl'


def _fold(state, aid, count, now):
    return _accumulate(state, [_asset('foo-main', 'foo-main-any.mhl', aid, count)], now)


def test_new_asset_lifetime_equals_raw():
    s = _fold({}, 1, 10, 'n1')
    assert s[KEY]['lifetime'] == 10
    assert s[KEY]['base'] == 0
    assert s[KEY]['first_seen'] == 'n1'


def test_same_asset_growth_tracks_raw():
    s = _fold({}, 1, 10, 'n1')
    s = _fold(s, 1, 15, 'n2')
    assert s[KEY]['lifetime'] == 15
    assert s[KEY]['base'] == 0
    assert s[KEY]['first_seen'] == 'n1'
    assert s[KEY]['updated'] == 'n2'


def test_clobber_new_id_carries_prior_total_forward():
    s = _fold({}, 1, 15, 'n1')
    # rebuild: new asset id, count reset to 3
    s = _fold(s, 2, 3, 'n2')
    assert s[KEY]['base'] == 15
    assert s[KEY]['last_raw'] == 3
    assert s[KEY]['lifetime'] == 18


def test_clobber_detected_by_dropped_count_without_id_change():
    s = _fold({}, 1, 15, 'n1')
    # same id reported but count dropped — still a reset
    s = _fold(s, 1, 2, 'n2')
    assert s[KEY]['lifetime'] == 17


def test_growth_after_clobber_accumulates_on_new_base():
    s = _fold({}, 1, 15, 'n1')
    s = _fold(s, 2, 3, 'n2')
    s = _fold(s, 2, 7, 'n3')
    assert s[KEY]['lifetime'] == 22


def test_disappeared_asset_is_preserved_untouched():
    s = _fold({}, 1, 15, 'n1')
    s = _fold(s, 2, 7, 'n2')  # lifetime 22
    before = dict(s[KEY])
    s = _accumulate(s, [], 'n3')  # asset absent this run
    assert s[KEY] == before
    assert s[KEY]['lifetime'] == 22


def test_multiple_assets_tracked_independently():
    fetched = [
        _asset('a-1', 'a-1-any.mhl', 1, 5),
        _asset('b-1', 'b-1-linux_x86_64.mhl', 2, 8),
    ]
    s = _accumulate({}, fetched, 'n1')
    assert s['a-1/a-1-any.mhl']['lifetime'] == 5
    assert s['b-1/b-1-linux_x86_64.mhl']['lifetime'] == 8
