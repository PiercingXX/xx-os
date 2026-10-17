"""Wayland protocol contract for older-phoc compat (lane kb_compat).

xx-keyboard vendors wlr-data-control-unstable-v1.xml and generates it
like the ext protocol so the clipboard works on compositors that only
offer the wlr variant (phoc 0.44 on the FuriPhone FLX1). This pins the
messages the C code relies on, plus the meson wiring, with stdlib only
(no build or display needed).
"""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WLR_XML = REPO / 'protocol' / 'wlr-data-control-unstable-v1.xml'
MESON_BUILD = REPO / 'protocol' / 'meson.build'


@pytest.fixture(scope='module')
def wlr_root():
    assert WLR_XML.is_file(), f'vendored protocol missing: {WLR_XML}'
    return ET.parse(str(WLR_XML)).getroot()


def _interface(root, name):
    for iface in root.findall('interface'):
        if iface.get('name') == name:
            return iface
    raise AssertionError(f'interface {name} missing')


def test_protocol_name(wlr_root):
    assert wlr_root.get('name') == 'wlr_data_control_unstable_v1'


def test_manager_offers_get_data_device(wlr_root):
    manager = _interface(wlr_root, 'zwlr_data_control_manager_v1')
    assert int(manager.get('version')) >= 2
    req = manager.find("./request[@name='get_data_device']")
    assert req is not None
    args = {a.get('name'): a for a in req.findall('arg')}
    assert args['id'].get('interface') == 'zwlr_data_control_device_v1'
    assert args['seat'].get('interface') == 'wl_seat'


def test_device_events_cover_clipboard_use(wlr_root):
    device = _interface(wlr_root, 'zwlr_data_control_device_v1')
    events = {e.get('name') for e in device.findall('event')}
    # What pos-clipboard-manager listens to (primary_selection only
    # arrives when bound at v2, which old phoc offers).
    assert {'data_offer', 'selection', 'finished', 'primary_selection'} <= events


def test_offer_supports_receive(wlr_root):
    offer = _interface(wlr_root, 'zwlr_data_control_offer_v1')
    receive = offer.find("./request[@name='receive']")
    assert receive is not None
    args = {a.get('name'): a for a in receive.findall('arg')}
    assert args['mime_type'].get('type') == 'string'
    assert args['fd'].get('type') == 'fd'
    assert offer.find("./event[@name='offer']") is not None


def test_meson_generates_vendored_protocol():
    text = MESON_BUILD.read_text(encoding='utf-8')
    assert "'wlr-data-control-unstable-v1.xml'" in text


def test_wlr_matches_ext_for_clipboard_messages(wlr_root):
    """The fallback is only valid because the two protocols agree on the
    messages we use; spot-check against the system ext XML when the
    wayland-protocols package is installed."""
    ext_path = Path('/usr/share/wayland-protocols/staging/ext-data-control/ext-data-control-v1.xml')
    if not ext_path.is_file():
        pytest.skip('wayland-protocols not installed')
    ext = ET.parse(str(ext_path)).getroot()

    def messages(root, iface_name):
        iface = _interface(root, iface_name)
        out = {}
        for kind in ('request', 'event'):
            for msg in iface.findall(kind):
                out[(kind, msg.get('name'))] = [
                    (a.get('name'), a.get('type'), a.get('interface'))
                    for a in msg.findall('arg')
                ]
        return out

    pairs = [
        ('ext_data_control_manager_v1', 'zwlr_data_control_manager_v1'),
        ('ext_data_control_device_v1', 'zwlr_data_control_device_v1'),
        ('ext_data_control_offer_v1', 'zwlr_data_control_offer_v1'),
    ]
    for ext_name, wlr_name in pairs:
        for (kind, name), ext_args in messages(ext, ext_name).items():
            wlr_args = messages(wlr_root, wlr_name).get((kind, name))
            assert wlr_args is not None, f'{wlr_name} lacks {kind} {name}'
            # Same messages apart from the interface-name prefix.
            norm = [
                (n, t, i.replace('ext_data_control', 'zwlr_data_control') if i else i)
                for n, t, i in ext_args
            ]
            assert wlr_args == norm, f'{wlr_name} {kind} {name} differs from ext'
