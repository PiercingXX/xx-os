"""First-boot Status bar step (status_bar.py's `status_bar_height`).

The step sits between Theme and Timezone, opens on the device default
(or a height an unfinished run saved), shows a live strip at the real
height, and writes the key: Next saves the slider's clamped height, Use
default writes null so the device default applies. Headless: wizard
methods run against a namespace self, as in test_first_boot_keyboard."""
import re
import sys
import types
from pathlib import Path

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
sys.path.insert(0, str(SRC))


def _first_boot():
    # Imported lazily: sibling test modules stub sys.modules['gi'] during
    # collection; conftest restores the real bindings before tests run.
    import first_boot
    return first_boot


def _status_bar():
    import status_bar
    return status_bar


class _FakeStack:
    def __init__(self):
        self.visible_child_name = None

    def set_visible_child_name(self, name):
        self.visible_child_name = name


class _FakeConfig:
    def __init__(self, data=None):
        self.writes: list[tuple[str, object]] = []
        self.data: dict = dict(data or {})
        self.theme = None

    def set(self, key, value):
        self.writes.append((key, value))
        self.data[key] = value

    def set_theme(self, key):
        self.theme = key


class _FakeWidget:
    def __init__(self):
        self.size = None
        self.text = None

    def set_size_request(self, width, height):
        self.size = (width, height)

    def set_text(self, text):
        self.text = text


def _wizard(data=None, height=49):
    cls = _first_boot().FirstBootWizard
    wiz = types.SimpleNamespace(
        _config=_FakeConfig(data),
        _stack=_FakeStack(),
        _selected_theme='paper',
        _sb_height=height,
        _sb_strip=_FakeWidget(),
        _sb_row=_FakeWidget(),
        _sb_value=_FakeWidget(),
    )
    for name in ('_on_theme_next', '_show_status_bar_height',
                 '_on_status_bar_slider', '_save_status_bar_height',
                 '_on_status_bar_next', '_on_status_bar_use_default'):
        setattr(wiz, name, types.MethodType(getattr(cls, name), wiz))
    return wiz


# ------------------------------------------------------------ placement


class TestStepOrder:
    def _names(self):
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        body = src.split('def __init__')[1].split('\n    def ')[0]
        return re.findall(r"add_named\(self\._build_\w+\(\), '(\w+)'\)", body)

    def test_status_bar_step_sits_between_theme_and_timezone(self):
        names = self._names()
        assert names.count('status_bar') == 1
        assert names.index('theme') < names.index('status_bar') \
            < names.index('timezone')

    def test_theme_next_leads_to_the_status_bar_step(self):
        wiz = _wizard()
        wiz._on_theme_next(None)
        assert wiz._config.theme == 'paper'
        assert wiz._stack.visible_child_name == 'status_bar'

    def test_both_buttons_move_on_to_timezone(self):
        for handler in ('_on_status_bar_next', '_on_status_bar_use_default'):
            wiz = _wizard()
            getattr(wiz, handler)(None)
            assert wiz._stack.visible_child_name == 'timezone', handler

    def test_tour_replay_never_shows_the_step(self):
        # `xx-wm --welcome` builds the wizard with tour_only and starts on
        # the first tour page: setup steps stay unreachable, so a replay
        # writes nothing and adds no second status bar page.
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        tour = src.split('def _build_tour_pages')[1].split('\n    def ')[0]
        assert 'status_bar' not in tour
        advance = src.split('def _advance_tour')[1].split('\n    def ')[0]
        assert 'status_bar' not in advance

    def test_preview_css_rides_the_existing_providers(self):
        # Replays rebuild the wizard: its styles must reload into the two
        # add-once display providers, never a new provider per build.
        fb = _first_boot()
        css = fb.theme_css(fb.THEME_PRESETS['amoled'])
        for cls in ('.sb-preview', '.sb-preview-time', '.sb-preview-battery',
                    '.wizard-scale'):
            assert cls in css
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        assert src.count('Gtk.CssProvider()') == 2


# ------------------------------------------------------------ default


class TestDefault:
    def test_device_default_is_preselected(self):
        fb = _first_boot()
        assert fb.status_bar_start({}, lambda: 49) == 49
        assert fb.status_bar_start(
            {'status_bar_height': None}, lambda: 64) == 64

    def test_an_earlier_saved_height_wins(self):
        assert _first_boot().status_bar_start(
            {'status_bar_height': 57}, lambda: 49) == 57

    def test_flx1_default_follows_the_live_scale(self, monkeypatch):
        # The owner's FLX1 at scale 1.6: 1000 logical px over the 129 mm
        # panel phoc reports puts the hole's lower edge at 46.5 px.
        sb = _status_bar()
        monkeypatch.setattr(sb, 'detect_device', lambda: 'furiphone-flx1')
        monkeypatch.setattr(sb, 'monitor_panel', lambda _d: (1000, 129))
        wiz = types.SimpleNamespace(get_display=lambda: None)
        default = _first_boot().FirstBootWizard._status_bar_default(wiz)
        assert default == sb.cutout_bar_height(*sb.FLX1_HOLE_MM, 129, 1000)
        assert default == 49

    def test_generic_device_default(self, monkeypatch):
        sb = _status_bar()
        monkeypatch.setattr(sb, 'detect_device', lambda: None)
        monkeypatch.setattr(sb, 'monitor_panel', lambda _d: None)
        wiz = types.SimpleNamespace(get_display=lambda: None)
        assert (_first_boot().FirstBootWizard._status_bar_default(wiz)
                == sb.DEFAULT_HEIGHT)

    def test_use_default_writes_null(self):
        wiz = _wizard({'status_bar_height': 70}, height=70)
        wiz._on_status_bar_use_default(None)
        assert wiz._config.writes == [('status_bar_height', None)]


# ------------------------------------------------------------ slider


class TestSlider:
    def test_slider_moves_the_live_strip(self):
        wiz = _wizard()
        wiz._on_status_bar_slider(57.4)
        assert wiz._sb_height == 57
        assert wiz._sb_strip.size == (-1, 57)
        assert wiz._sb_row.size == (-1, 57)
        assert wiz._sb_value.text == '57 px'

    def test_next_writes_the_slider_height(self):
        wiz = _wizard()
        wiz._on_status_bar_slider(62)
        wiz._on_status_bar_next(None)
        assert wiz._config.writes == [('status_bar_height', 62)]

    def test_untouched_default_is_written_on_next(self):
        wiz = _wizard(height=49)
        wiz._on_status_bar_next(None)
        assert wiz._config.writes == [('status_bar_height', 49)]

    def test_clamp_range(self):
        sb = _status_bar()
        assert (sb.MIN_HEIGHT, sb.MAX_HEIGHT) == (20, 96)
        wiz = _wizard()
        wiz._on_status_bar_slider(3)
        assert wiz._sb_height == sb.MIN_HEIGHT
        wiz._on_status_bar_slider(400)
        assert wiz._sb_height == sb.MAX_HEIGHT
        wiz._on_status_bar_next(None)
        assert wiz._config.writes == [('status_bar_height', sb.MAX_HEIGHT)]

    def test_scale_spans_the_clamp_range(self):
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        body = src.split('def _build_status_bar_step')[1].split(
            '\n    def ')[0]
        assert re.search(r'new_with_range\(\s*Gtk\.Orientation\.HORIZONTAL,'
                         r'\s*status_bar\.MIN_HEIGHT,\s*status_bar\.MAX_HEIGHT,'
                         r'\s*1\)', body)


# ------------------------------------------------------------ marker


class TestSetupMarker:
    def test_first_write_marks_setup_unfinished(self):
        wiz = _wizard()
        wiz._on_status_bar_next(None)
        assert wiz._config.data['setup_complete'] is False

    def test_marker_semantics_untouched_once_set(self):
        wiz = _wizard({'setup_complete': False})
        wiz._on_status_bar_use_default(None)
        assert wiz._config.data['setup_complete'] is False
        done = _wizard({'setup_complete': True})
        done._on_status_bar_next(None)
        assert done._config.data['setup_complete'] is True
