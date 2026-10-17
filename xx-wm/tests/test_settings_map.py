"""Settings information architecture (docs/settings-map.md).

One home per setting: Network & internet is connectivity only, what the
phone serves (wireless debugging, services, the incoming-connection
firewall) sits together on System, no row is a second way into another
page, and every deep link (shade tile long-press, IPC settings.open:)
still lands on a page that holds its section."""
import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
DOCS = Path(__file__).parent.parent / 'docs'
sys.path.insert(0, str(SRC))


def _page():
    import settings_page
    return settings_page


def _sections(category: str) -> tuple:
    for key, _title, sections in _page().SETTINGS_CATEGORIES:
        if key == category:
            return sections
    raise KeyError(category)


def test_network_and_internet_is_connectivity_only():
    assert _sections('network') == ('wifi', 'mobile')


def test_what_the_phone_serves_shares_the_system_page():
    sp = _page()
    for key in ('developer', 'services', 'firewall'):
        assert sp.category_for(key) == 'system', key
    system = _sections('system')
    # Wireless debugging, then the daemons, then who may connect in.
    assert system.index('developer') < system.index('services') \
        < system.index('firewall')
    # The shell-preference footnote still ends the page.
    assert system[-1] == 'shell'


def test_service_rows_name_daemons_not_settings_pages():
    # 'Network', 'Bluetooth' and 'Modem' rows on System read as second
    # copies of Wi-Fi, Bluetooth and Mobile data.
    import settings_page  # noqa: F401  (resolves the section imports)
    import settings_services
    titles = {entry.key: entry.title for entry in settings_services.SERVICES}
    assert titles['network'] == 'NetworkManager'
    assert titles['bluetooth'] == 'BlueZ'
    assert titles['modem'] == 'ModemManager'
    menu = {title for _k, title, _s in _page().SETTINGS_CATEGORIES}
    section_titles = {'Wi-Fi', 'Bluetooth', 'Mobile data', 'Network'}
    assert not (set(titles.values()) & (menu | section_titles))


def test_about_has_no_second_way_into_storage():
    src = (SRC / 'settings_page.py').read_text(encoding='utf-8')
    about = src.split('class AboutSection')[1].split('\nclass ')[0]
    assert "open_settings('storage')" not in about
    assert "'More'" not in about


def test_every_deep_link_resolves():
    sp = _page()
    import quick_actions
    for key in sp._SETTINGS_SECTION_KEYS:
        assert sp.category_for(key) is not None, key
    for tile in ('wifi', 'bt', 'data', 'airplane', 'night', 'tailscale'):
        section = quick_actions.tile_settings_section(tile)
        assert sp.category_for(section) is not None, tile
    # The moved sections keep their keys, so settings.open:firewall and
    # settings.open:services land on System.
    assert sp.category_for('firewall') == 'system'
    assert sp.category_for('services') == 'system'


def test_map_doc_lists_every_page_and_section():
    text = (DOCS / 'settings-map.md').read_text(encoding='utf-8')
    for _key, title, sections in _page().SETTINGS_CATEGORIES:
        assert title in text, title
        for section in sections:
            assert '`%s`' % section in text, section
