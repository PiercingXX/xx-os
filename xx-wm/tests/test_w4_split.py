"""W4 split contract: window.py's page/call logic lives in cohesive
helper modules, composed (not mixed in) by ShellWindow.

Pure refactor guard: every moved method keeps its name on ShellWindow as
a thin delegator, old `window.X` paths keep working, helpers hold only a
window reference, and no module imports another at import time.
"""
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import call_wiring  # noqa: E402
import drawer_page  # noqa: E402
import home_page  # noqa: E402
import settings_page  # noqa: E402

SRC = Path(__file__).parent.parent / 'launcher' / 'src'


@pytest.fixture(scope='module')
def window():
    """Fresh real-gi window module, restored afterwards.

    Imported here (not at collection) so fake-gi fixtures in other files
    keep getting their own bindings: importing window at collection would
    pin one binding in sys.modules for every fixture that runs later.
    """
    saved = sys.modules.pop('window', None)
    try:
        import window as fresh_window  # noqa: E402
        yield fresh_window
    finally:
        if saved is None:
            sys.modules.pop('window', None)
        else:
            sys.modules['window'] = saved

_HOME_METHODS = [
    '_build_home_page', '_launch_slot', '_build_widget_block',
    '_current_tap', '_wire_widget_tap', '_on_clock_tapped',
    '_dispatch_widget_tap', '_launch_first_matching', '_init_weather',
    '_tick_weather', '_refresh_weather', '_refresh_clock', '_tick_clock',
    '_refresh_status', 'set_privacy_snapshot', '_tick_status',
    '_on_home_long_press', '_on_edit_action', '_add_app_slot',
    '_create_folder_slot', '_move_slot', '_remove_slot', '_rename_slot',
]

_DRAWER_METHODS = [
    '_build_apps_page', '_toggle_sort_mode', '_home_slot_app_ids',
    '_drawer_folder_slots', '_install_time', '_on_alpha_jump',
    '_populate_apps', '_scroll_apps_to_top', '_make_drawer_folder_row',
    '_make_drawer_member_row', '_toggle_drawer_folder',
    '_center_drawer_folder', '_add_long_press', '_make_settings_row',
    '_replace_rows', '_make_app_row', '_launch_entry',
    '_on_apps_search_changed', '_on_apps_search_activate',
    '_open_web_search',
]

_SETTINGS_METHODS = [
    'open_settings', '_scroll_settings_to', '_build_settings_page',
    '_gesture_binding_text', '_refresh_gesture_labels',
    '_build_gestures_card',
    '_pick_system_gesture', '_set_system_gesture', '_reset_system_gesture',
    '_pick_gesture_app', '_clear_gesture_app', '_build_developer_card',
    '_refresh_xxdb_section', '_on_xxdb_switch_flipped',
    '_on_xxdb_pair_clicked', '_on_xxdb_forget', '_on_xxdb_confirm',
    '_run_bg', '_replace_box_children', '_section_placeholder',
    '_refresh_system_sections', '_show_battery', '_show_sinks',
    '_on_sink_selected', '_refresh_wifi', '_show_wifi', '_on_wifi_selected',
    '_connect_wifi', '_refresh_bluetooth', '_show_bt_devices',
    '_on_bt_selected', '_on_apn_save',
    '_gsm_connection_name', '_apply_apn', '_on_update_clicked',
    '_on_update_now', '_on_dots_update', '_refresh_apps_section',
    '_on_check_updates_clicked', '_on_manual_check_result',
    '_on_updates_available', '_snooze_updates', '_on_export_backup',
    '_on_restore_backup', '_confirm_restore_backup', '_restore_from_path',
    '_get_device_name',
]

_CALL_METHODS = [
    '_ensure_call_surfaces', 'silence_ringer', '_on_call_incoming',
    '_sync_call_inhibit', '_on_calls_changed', '_on_call_answered',
    '_on_call_ended', '_on_sms_received', '_expand_call_ui', '_call_live',
    '_reassert_call_ui_above_lock',
]


def test_helpers_hold_only_a_window_reference():
    sentinel = object()
    assert home_page.HomePage(sentinel)._win is sentinel
    assert drawer_page.DrawerPage(sentinel)._win is sentinel
    assert settings_page.SettingsPage(sentinel)._win is sentinel
    assert call_wiring.CallWiring(sentinel)._win is sentinel


@pytest.mark.parametrize('name', _HOME_METHODS)
def test_shell_window_keeps_home_names(window, name):
    assert callable(getattr(window.ShellWindow, name, None)), name
    assert callable(getattr(home_page.HomePage, name, None)), name


@pytest.mark.parametrize('name', _DRAWER_METHODS)
def test_shell_window_keeps_drawer_names(window, name):
    assert callable(getattr(window.ShellWindow, name, None)), name
    assert callable(getattr(drawer_page.DrawerPage, name, None)), name


@pytest.mark.parametrize('name', _SETTINGS_METHODS)
def test_shell_window_keeps_settings_names(window, name):
    assert callable(getattr(window.ShellWindow, name, None)), name
    assert callable(getattr(settings_page.SettingsPage, name, None)), name


@pytest.mark.parametrize('name', _CALL_METHODS)
def test_shell_window_keeps_call_names(window, name):
    assert callable(getattr(window.ShellWindow, name, None)), name
    assert callable(getattr(call_wiring.CallWiring, name, None)), name


@pytest.mark.parametrize('name,helper', [
    ('_populate_apps', '_drawer(self)'),
    ('_build_home_page', '_home(self)'),
    ('_build_settings_page', '_settings(self)'),
    ('_on_calls_changed', '_calls(self)'),
    ('_on_restore_backup', '_settings(self)'),
    ('_launch_entry', '_drawer(self)'),
    ('_launch_slot', '_home(self)'),
    ('open_settings', '_settings(self)'),
])
def test_delegators_route_through_helpers(window, name, helper):
    src = inspect.getsource(getattr(window.ShellWindow, name))
    assert helper in src


def test_old_module_paths_keep_working(window):
    assert window._GESTURE_TITLES is settings_page._GESTURE_TITLES
    assert window._SYSTEM_GESTURE_TITLES is settings_page._SYSTEM_GESTURE_TITLES
    assert window._ACTION_LABELS is settings_page._ACTION_LABELS
    assert window._VERB_LABELS is settings_page._VERB_LABELS
    assert window._SETTINGS_SECTION_KEYS is settings_page._SETTINGS_SECTION_KEYS
    assert window._UPDATE_NOTIF_ID == settings_page._UPDATE_NOTIF_ID
    assert window._WEB_SEARCH_URL == drawer_page._WEB_SEARCH_URL
    with pytest.raises(AttributeError):
        window._no_such_split_name


def test_no_circular_imports_at_import_time():
    code = (
        "import sys; sys.path.insert(0, 'launcher/src');"
        "import home_page, drawer_page, settings_page, call_wiring;"
        "assert 'window' not in sys.modules, 'split module imported window'"
    )
    subprocess.run([sys.executable, '-c', code], check=True,
                   capture_output=True, text=True)


def test_settings_registry_order_and_shape():
    keys = [key for key, _cls in settings_page.SETTINGS_SECTIONS]
    assert keys == ['system', 'wifi', 'bluetooth', 'sound', 'battery',
                    'charging', 'batteryuse', 'screentime',
                    'backup', 'storage', 'security', 'lock', 'mobile',
                    'services', 'firewall',
                    'apps', 'nope', 'appearance', 'display', 'keyboard',
                    'launcher', 'standby', 'permissions', 'smart_lock',
                    'medical',
                    'recording', 'captions', 'nearby', 'findmy',
                    'about', 'shell', 'developer', 'gestures']
    for key, cls in settings_page.SETTINGS_SECTIONS:
        section = cls()
        assert callable(getattr(section, 'build', None)), key
        assert callable(getattr(section, 'refresh', None)), key


def test_meson_lists_split_modules():
    meson = (SRC.parent / 'meson.build').read_text(encoding='utf-8')
    for name in ('home_page.py', 'drawer_page.py', 'settings_page.py',
                 'call_wiring.py'):
        assert f"'src/{name}'" in meson


def test_window_stays_the_composer():
    text = (SRC / 'window.py').read_text(encoding='utf-8')
    assert 'def decide_layer_policy' in text
    assert 'def resolve_theme' in text
    assert 'def _apply_layer_policy' in text
    assert 'def _retheme_surfaces' in text
    assert 'def on_blank_changed' in text
    assert 'def _apply_config_change' in text
