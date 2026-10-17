"""Now Playing: swiping the stage changes songs in both looks (owner:
"let me swipe on the screen to change music")."""
import inspect
import types

from xx_music.ui import nowplaying


def _np(calls):
    player = types.SimpleNamespace(next=lambda: calls.append('next'),
                                   previous=lambda: calls.append('previous'))
    return types.SimpleNamespace(ui=types.SimpleNamespace(player=player),
                                 toggle=lambda what: calls.append(what))


def test_swipe_directions():
    calls = []
    np = _np(calls)
    nowplaying.NowPlaying._swiped(np, None, -900.0, 10.0)
    nowplaying.NowPlaying._swiped(np, None, 900.0, -10.0)
    nowplaying.NowPlaying._swiped(np, None, 20.0, -900.0)
    nowplaying.NowPlaying._swiped(np, None, 100.0, 50.0)   # too slow: nothing
    assert calls == ['next', 'previous', 'queue']


def test_both_art_views_attach_the_swipe():
    regular = inspect.getsource(nowplaying.NowPlaying._art_view)
    minimal_view = inspect.getsource(nowplaying.NowPlaying._minimal_art_view)
    for src in (regular, minimal_view):
        assert 'GestureSwipe' in src and '_swiped' in src
