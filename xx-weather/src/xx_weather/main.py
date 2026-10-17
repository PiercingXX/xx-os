"""xx-weather: ZIP-code weather (NWS, US only).

Adw.Application with forecast / places / settings pages plus a day
detail page. Every fetch runs on a worker thread; results post back
with GLib.idle_add. Headless-safe import: Gtk/Adw load lazily so pure
tests never need a display.
"""

from __future__ import annotations

import os
import threading
import time

def app_css(scale: float = 1.0) -> str:
    """App-local type roles, scaled by the suite text-size setting so
    large text stays proportional (the shared sheet scales the rest)."""

    def px(base: int) -> str:
        return f"{round(base * scale)}px"

    return f"""
.xx-weather-hero {{ font-size: {px(72)}; font-weight: 200; }}
.xx-weather-cond {{ font-size: {px(16)}; }}
.xx-weather-tile-value {{ font-size: {px(20)}; }}
.xx-hour-temp {{ font-size: {px(16)}; }}
.xx-weather-hour {{ min-height: {px(44)}; }}
.xx-weather-hours-strip {{ padding-bottom: 4px; }}
.xx-setting.xx-weather-last, .xx-row.xx-weather-last {{ border-bottom: none; }}
"""


APP_CSS = app_css()


def _require_gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: F401

    return Adw, Gdk, Gio, GLib, Gtk, Pango


LICENSE_ROWS = (
    ("NWS weather.gov", "Forecasts \u00b7 US public domain"),
    ("Open-Meteo.com", "Weather data \u00b7 CC BY 4.0"),
    ("Zippopotam.us", "ZIP lookup from GeoNames \u00b7 CC BY 4.0"),
    ("xx-weather", "App code \u00b7 GPL-3.0-only"),
)

FOOTER_TEXT = ("NWS weather.gov \u00b7 Open-Meteo.com (CC BY 4.0) "
               "\u00b7 GeoNames (CC BY 4.0)")

ALERT_HOURS = (3, 6, 12)
ALERT_TEMPS_F = (20, 28, 32, 40)


class WeatherWindow:
    """Main window; pages live in a Gtk.Stack with bottom tabs."""

    def __init__(self, app, store) -> None:
        Adw, Gdk, Gio, GLib, Gtk, Pango = _require_gtk()
        self._Adw, self._Gdk, self._Gio = Adw, Gdk, Gio
        self._GLib, self._Gtk, self._Pango = GLib, Gtk, Pango
        self.app = app
        self.store = store
        from xx_phone.weather import (Units, ZipRefreshState,
                                      apply_refresh_result)

        self._Units = Units
        self._ZipRefreshState = ZipRefreshState
        self._apply_refresh_result = apply_refresh_result
        self.state = ZipRefreshState()
        self.loading = True
        self.refreshing = False
        self.page = "forecast"
        self.day_date = None
        self._started = False
        self._timer_id = 0

        import xx_ui
        from xx_ui import widgets as W

        self._W = W
        self.win = Adw.ApplicationWindow(application=app)
        self.win.set_title("xx-weather")
        self.win.set_default_size(360, 720)
        scale = 1.0
        try:
            scale = xx_ui.apply(self.win).scale
        except Exception:  # noqa: BLE001 - theme is decorative
            pass
        self._app_css_provider = None
        self._load_app_css(scale)
        try:
            xx_ui.on_change(lambda theme: self._load_app_css(theme.scale))
        except Exception:  # noqa: BLE001 - live restyle is decorative
            pass

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_vexpand(True)
        self._build_forecast_page()
        self._build_places_page()
        self._build_settings_page()
        self._build_day_page()
        self._build_licenses_page()

        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        shell.append(self.stack)
        self.tabs = W.bottom_tabs([
            ("", "FORECAST", True, lambda: self.show("forecast")),
            ("", "PLACES", False, lambda: self.show("places")),
            ("", "SETTINGS", False, lambda: self.show("settings")),
        ])
        shell.append(self.tabs)
        self.win.set_content(shell)
        self.show("forecast")

    # -- theme ---------------------------------------------------------

    def _load_app_css(self, scale: float = 1.0) -> None:
        try:
            Gtk, Gdk = self._Gtk, self._Gdk
            if self._app_css_provider is None:
                display = Gdk.Display.get_default()
                if display is None:
                    return
                self._app_css_provider = Gtk.CssProvider()
                Gtk.StyleContext.add_provider_for_display(
                    display, self._app_css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self._app_css_provider.load_from_data(
                app_css(scale).encode("utf-8"))
        except Exception:  # noqa: BLE001 - app css is decorative
            pass

    # -- navigation ------------------------------------------------------

    def show(self, name: str) -> None:
        self.page = name
        self.stack.set_visible_child_name(name)
        try:
            tabs = getattr(self, "tabs", None)
            if tabs is not None:
                index = {"forecast": 0, "places": 1, "settings": 2,
                         "licenses": 2, "day": 0}.get(name, 0)
                child = tabs.get_first_child()
                pos = 0
                while child is not None:
                    if pos == index:
                        child.add_css_class("active")
                    else:
                        child.remove_css_class("active")
                    child = child.get_next_sibling()
                    pos += 1
        except Exception:  # noqa: BLE001 - tab highlight is decorative
            pass

    # -- forecast page -----------------------------------------------------

    def _page_shell(self, header, body) -> object:
        Gtk = self._Gtk
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_margin_start(20)
        page.set_margin_end(20)
        page.set_margin_top(12)
        # The page margins are the gutter; a second one from the header's
        # own padding pushed titles 20 px right of the content.
        header.remove_css_class("xx-page-body")
        page.append(header)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(body)
        page.append(scroll)
        return page

    def _build_forecast_page(self) -> None:
        Gtk, W = self._Gtk, self._W
        header = W.page_header(
            "Weather", None,
            [("REFRESH", self.refresh_forced)])
        self.forecast_header_box = header
        self.forecast_title = header.get_first_child().get_first_child()
        self.refresh_button = self.forecast_title.get_next_sibling()
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(12)
        body.set_margin_bottom(28)
        self.error_label = W.wrap_label("", "xx-secondary")
        self.error_label.add_css_class("xx-danger-text")
        self.error_label.set_visible(False)
        body.append(self.error_label)
        self.forecast_body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.append(self.forecast_body)
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(32, 32)
        self.spinner.set_halign(Gtk.Align.CENTER)
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_vexpand(True)
        body.append(self.spinner)
        self.forecast_scroll = self._page_shell(header, body)
        self.stack.add_named(self.forecast_scroll, "forecast")

    def _set_forecast_title(self, place) -> None:
        """The page names the place it shows, like the Android header."""
        title = place.city if place is not None else "Weather"
        try:
            self.forecast_title.set_text(title)
            # Nothing saved, nothing to refresh.
            self.refresh_button.set_visible(place is not None)
        except Exception:  # noqa: BLE001 - title swap is decorative
            pass

    def _render_forecast(self) -> None:
        Gtk, W = self._Gtk, self._W
        from xx_phone.weather import (condition_label, day_label, hilo,
                                      hour_label, temp_str, updated_label)

        while (child := self.forecast_body.get_first_child()) is not None:
            self.forecast_body.remove(child)

        places = self.store.places()
        selected = self.store.selected()
        self._set_forecast_title(selected if places else None)

        state = self.state
        data = None
        if selected is not None:
            data = state.data_by_zip.get(selected.zip)
        error = (selected.zip if selected is not None else None)
        error_msg = state.errors.get(error) if error else None
        if error_msg:
            self.error_label.set_text(error_msg)
            self.error_label.set_visible(True)
        else:
            self.error_label.set_visible(False)

        units = self.store.units()
        state_code = selected.state if selected is not None else ""
        if not places:
            # Nothing saved: the one primary action, nothing else.
            self.spinner.set_visible(False)
            self.spinner.stop()
            add = W.outline_button("Add ZIP code", self._go_add_zip)
            add.set_hexpand(True)
            self.forecast_body.append(add)
            self._update_caption(None)
            return
        if data is None and error_msg is None:
            self.spinner.set_visible(True)
            self.spinner.start()
            self._update_caption(None)
            return
        self.spinner.set_visible(False)
        self.spinner.stop()
        if data is None:
            # The red error line says it; REFRESH in the header retries.
            self._update_caption(None)
            return

        stamp = f"Updated {updated_label(data.updated_at_ms)}"
        if state_code:
            stamp = f"{state_code} · {stamp}"
        self._update_caption(stamp)

        today = data.daily[0] if data.daily else None
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        hero.append(W.label(temp_str(data.current.temp_f, units),
                            "xx-weather-hero"))
        cond = W.wrap_label(data.current.condition_text, "xx-weather-cond")
        hero.append(cond)
        if today is not None:
            line = hilo(today.hi_f, today.lo_f, units)
            if line:
                sub = W.label(line, "xx-secondary")
                sub.set_margin_top(4)
                hero.append(sub)
        self.forecast_body.append(hero)

        self.forecast_body.append(W.section_label("HOURLY"))
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=22)
        strip.add_css_class("xx-weather-hours-strip")
        for index, hour in enumerate(list(data.hourly)[:24]):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            col.set_valign(Gtk.Align.START)
            col.set_size_request(46, -1)
            when = "Now" if index == 0 else hour_label(hour.time)
            time_l = W.label(when, "xx-secondary", xalign=0.5)
            time_l.set_halign(Gtk.Align.CENTER)
            # A short fixed label in a scrolling strip: the column grows
            # to fit "10 PM" at large text instead of ellipsizing it.
            time_l.set_ellipsize(self._Pango.EllipsizeMode.NONE)
            col.append(time_l)
            temp_l = W.label(temp_str(hour.temp_f, units),
                             "xx-hour-temp", xalign=0.5)
            temp_l.set_halign(Gtk.Align.CENTER)
            col.append(temp_l)
            pop = hour.pop_pct or 0
            pop_l = W.label(f"{pop}%" if pop >= 30 else "", "xx-time",
                            xalign=0.5)
            pop_l.set_halign(Gtk.Align.CENTER)
            col.append(pop_l)
            strip.append(col)
        hours = Gtk.ScrolledWindow()
        hours.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        hours.set_child(strip)
        self.forecast_body.append(hours)

        self.forecast_body.append(W.section_label("10-DAY"))
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        daily = list(data.daily)
        for pos, point in enumerate(daily):
            secondary = condition_label(point.condition)
            if point.pop_pct is not None and point.pop_pct >= 30:
                secondary = f"{secondary} · {point.pop_pct}%"
            row = W.list_row(
                "Today" if pos == 0 else day_label(point.date), secondary,
                trailing=self._hilo_columns(point.hi_f, point.lo_f, units),
                on_activate=self._open_day_cb(point.date),
                last=pos == len(daily) - 1)
            rows.append(row)
        self.forecast_body.append(rows)

        self.forecast_body.append(W.section_label("DETAILS"))
        grid = Gtk.Grid(column_spacing=12, row_spacing=12,
                        column_homogeneous=True)
        for pos, (name, value, sub) in enumerate(
                self._detail_tiles(data, units)):
            tile = W.card()
            tile.set_spacing(4)
            tile.append(W.caption(name))
            value_l = W.label(value, "xx-weather-tile-value")
            value_l.set_margin_top(8)
            tile.append(value_l)
            if sub:
                tile.append(W.label(sub, "xx-secondary"))
            grid.attach(tile, pos % 2, pos // 2, 1, 1)
        self.forecast_body.append(grid)

        foot = W.wrap_label(FOOTER_TEXT, "xx-tiny")
        foot.set_margin_top(8)
        self.forecast_body.append(foot)

    def _hilo_columns(self, hi_f, lo_f, units):
        """High over low as two aligned columns: high in text, low muted.
        A night-only date omits the high instead of printing "--"."""
        Gtk, W = self._Gtk, self._W
        from xx_phone.weather import temp_str

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hi = W.label(temp_str(hi_f, units) if hi_f is not None else "",
                     "xx-title", xalign=1.0)
        hi.set_width_chars(4)
        lo = W.label(temp_str(lo_f, units) if lo_f is not None else "",
                     "xx-secondary", xalign=1.0)
        lo.set_width_chars(4)
        box.append(hi)
        box.append(lo)
        return box

    def _go_add_zip(self) -> None:
        self.show("places")
        try:
            self.zip_entry.grab_focus()
        except Exception:  # noqa: BLE001 - focus is a convenience
            pass

    def _select_zip_cb(self, zip_code: str):
        def _select() -> None:
            self.store.set_selected_zip(zip_code)
            self._render_forecast()
            self._render_places()
            self._render_settings()
            self.show("forecast")
            self.refresh_visible(False)

        return _select

    def _open_day_cb(self, day):
        def _open() -> None:
            self.day_date = day
            self._render_day()
            self.stack.set_visible_child_name("day")
            self.page = "day"

        return _open

    def _update_caption(self, caption_text) -> None:
        header = self.forecast_header_box
        old = getattr(self, "_forecast_caption", None)
        if old is not None:
            try:
                header.remove(old)
            except Exception:  # noqa: BLE001 - caption swap is decorative
                pass
            self._forecast_caption = None
        if caption_text:
            sub = self._W.caption(caption_text)
            header.append(sub)
            self._forecast_caption = sub

    def _detail_tiles(self, data, units) -> list:
        from xx_phone.weather import (clock_label, temp_str, uv_label,
                                      uv_text, wind_dir)

        current = data.current
        feels = temp_str(current.feels_like_f, units)
        humidity = f"{current.humidity_pct}%" if current.humidity_pct \
            is not None else "--"
        dew = (f"Dew point {temp_str(current.dewpoint_f, units)}"
               if current.dewpoint_f is not None else "")
        if current.wind_mph is not None:
            wind = f"{round(current.wind_mph)} mph"
            wind_sub = wind_dir(current.wind_dir_deg) \
                if current.wind_dir_deg is not None else ""
        else:
            wind, wind_sub = "--", ""
        uv = uv_text(current.uv_index) if current.uv_index is not None \
            else "--"
        if current.pressure_inhg is not None:
            pressure = f"{current.pressure_inhg:.2f} inHg"
            pressure_sub = "Station" if current.pressure_station_level \
                else "Sea level"
        else:
            pressure, pressure_sub = "--", "Sea level"
        visibility = f"{current.visibility_mi:.1f} mi" \
            if current.visibility_mi is not None else "--"
        if data.sun is not None and data.sun.sunrise is not None \
                and data.sun.sunset is not None:
            sunrise = clock_label(data.sun.sunrise)
            sunset_sub = f"Sunset {clock_label(data.sun.sunset)}"
        else:
            sunrise, sunset_sub = "--", ""
        pop = f"{current.pop_pct}%" if current.pop_pct is not None else "--"
        return [
            ("FEELS LIKE", feels, f"Actual {temp_str(current.temp_f, units)}"),
            ("HUMIDITY", humidity, dew),
            ("WIND", wind, wind_sub),
            ("UV INDEX", uv, uv_label(current.uv_index)),
            ("PRESSURE", pressure, pressure_sub),
            ("VISIBILITY", visibility, ""),
            ("SUNRISE", sunrise, sunset_sub),
            ("PRECIP", pop, "Next 12 hours"),
        ]

    # -- places page ---------------------------------------------------------

    def _build_places_page(self) -> None:
        Gtk, W = self._Gtk, self._W
        header = W.page_header("Places", None, None)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(12)
        body.set_margin_bottom(28)
        self.places_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.places_body)

        # Adding a ZIP lives here and only here: entry + ADD on one line.
        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.zip_entry = W.text_entry("ZIP code")
        self.zip_entry.set_max_length(5)
        self.zip_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.zip_entry.set_max_width_chars(10)
        self.zip_entry.set_hexpand(True)
        self.zip_entry.connect("activate", lambda _e: self._on_add_zip())
        add_row.append(self.zip_entry)
        add_btn = W.text_button("ADD", self._on_add_zip)
        add_btn.set_valign(Gtk.Align.CENTER)
        add_row.append(add_btn)
        body.append(add_row)
        self.zip_error = W.wrap_label("", "xx-secondary")
        self.zip_error.add_css_class("xx-danger-text")
        self.zip_error.set_visible(False)
        body.append(self.zip_error)
        self.stack.add_named(self._page_shell(header, body), "places")

    def _render_places(self) -> None:
        Gtk, W = self._Gtk, self._W
        from xx_phone.weather import temp_str

        while (child := self.places_body.get_first_child()) is not None:
            self.places_body.remove(child)
        places = self.store.places()
        units = self.store.units()
        for pos, place in enumerate(places):
            data = self.state.data_by_zip.get(place.zip)
            failed = (place.zip in self.state.errors
                      or (place.zip in self.state.data_by_zip
                          and data is None))
            if data is not None:
                secondary = data.current.condition_text
            else:
                secondary = ("Couldn't load" if failed else "Loading…")
            trailing = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=4)
            if data is not None:
                temp = W.label(temp_str(data.current.temp_f, units),
                               "xx-weather-tile-value", xalign=1.0)
                temp.set_valign(Gtk.Align.CENTER)
                trailing.append(temp)
            if len(places) > 1:
                remove = W.icon_button("window-close-symbolic",
                                       self._remove_zip_cb(place.zip))
                remove.set_valign(Gtk.Align.CENTER)
                remove.set_tooltip_text(f"Remove {place.display_name}")
                trailing.append(remove)
            row = W.list_row(place.display_name,
                             f"{place.zip} · {secondary}",
                             trailing=trailing,
                             on_activate=self._select_zip_cb(place.zip),
                             last=pos == len(places) - 1)
            self.places_body.append(row)

    def _remove_zip_cb(self, zip_code: str):
        def _remove() -> None:
            def _work() -> None:
                self.store.remove_place(zip_code)
                self._GLib.idle_add(self._after_places_changed)

            threading.Thread(target=_work, daemon=True,
                             name="xx-weather-remove").start()

        return _remove

    def _after_places_changed(self) -> bool:
        self._render_places()
        self._render_forecast()
        self._render_settings()
        self.refresh_visible(False)
        return False

    # -- settings page ---------------------------------------------------------

    def _build_settings_page(self) -> None:
        Gtk, W = self._Gtk, self._W
        header = W.page_header("Settings", None, None)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(12)
        body.set_margin_bottom(28)

        body.append(W.section_label("TEMPERATURE"))
        self.units_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.units_box)

        body.append(W.section_label("ALERTS"))
        self.alerts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.alerts_box)

        body.append(W.section_label("ABOUT"))
        body.append(W.list_row("Licenses", "NWS, Open-Meteo, GeoNames",
                               on_activate=lambda: self.show("licenses"),
                               last=True))

        self.settings_body = body
        self.stack.add_named(self._page_shell(header, body), "settings")

    def _render_settings(self) -> None:
        W = self._W
        from xx_phone.weather import Units

        while (child := self.units_box.get_first_child()) is not None:
            self.units_box.remove(child)
        units = self.store.units()
        self.units_box.append(W.segmented([
            ("°F", units == Units.FAHRENHEIT,
             lambda: self._set_units(Units.FAHRENHEIT)),
            ("°C", units == Units.CELSIUS,
             lambda: self._set_units(Units.CELSIUS)),
        ]))

        while (child := self.alerts_box.get_first_child()) is not None:
            self.alerts_box.remove(child)
        enabled = self.store.alerts_enabled()
        selected = self.store.selected()
        rows = [("Weather alerts",
                 selected.display_name if selected is not None else "",
                 enabled, self._on_alerts_enabled, None)]
        if enabled and selected is not None:
            triggers = self.store.ensure_triggers(selected.zip)
            zip_code = selected.zip
            rows.append((
                "Rain within", "50%+ chance or falling",
                triggers.precip_enabled,
                lambda on: self._with_triggers(zip_code, precip_enabled=on),
                [(f"{h}H", triggers.precip_hours == h,
                  self._hours_cb(zip_code, h)) for h in ALERT_HOURS]
                if triggers.precip_enabled else None))
            rows.append((
                "At or below", "Temperature",
                triggers.temp_enabled,
                lambda on: self._with_triggers(zip_code, temp_enabled=on),
                [(f"{t}\u00b0F", triggers.temp_at_or_below_f == t,
                  self._temp_cb(zip_code, t)) for t in ALERT_TEMPS_F]
                if triggers.temp_enabled else None))
        for pos, (title, sub, active, callback, chips) in enumerate(rows):
            last = pos == len(rows) - 1
            row = W.setting_row(title, sub, W.make_switch(active, callback))
            self.alerts_box.append(row)
            if not chips:
                if last:
                    row.add_css_class("xx-weather-last")
                continue
            # Chips belong to their row: the hairline goes under them.
            row.add_css_class("xx-weather-last")
            bar = W.chip_bar(chips)
            bar.set_margin_bottom(16)
            self.alerts_box.append(bar)
            if not last:
                self.alerts_box.append(
                    self._Gtk.Separator(
                        orientation=self._Gtk.Orientation.HORIZONTAL))

    def _with_triggers(self, zip_code: str, **changes) -> None:
        from dataclasses import replace

        current = self.store.ensure_triggers(zip_code)
        self.store.set_triggers(zip_code, replace(current, **changes))
        self._render_settings()

    def _hours_cb(self, zip_code: str, hours: int):
        def _set() -> None:
            self._with_triggers(zip_code, precip_hours=hours)

        return _set

    def _temp_cb(self, zip_code: str, temp_f: int):
        def _set() -> None:
            self._with_triggers(zip_code, temp_at_or_below_f=temp_f)

        return _set

    def _set_units(self, units) -> None:
        self.store.set_units(units)
        self._render_settings()
        self._render_forecast()
        self._render_places()

    def _on_alerts_enabled(self, enabled: bool) -> None:
        self.store.set_alerts_enabled(enabled)
        selected = self.store.selected()
        if enabled and selected is not None:
            self.store.ensure_triggers(selected.zip)
        self._render_settings()

    def _on_add_zip(self) -> None:
        raw = self.zip_entry.get_text()
        self.zip_error.set_visible(False)

        def _work() -> None:
            from xx_weather.worker import add_place

            try:
                place = add_place(self.store, raw)
            except Exception as exc:  # noqa: BLE001 - show, keep old city
                message = str(exc) or "Could not save ZIP code."
                self._GLib.idle_add(self._show_zip_error, message)
                return
            self._GLib.idle_add(self._after_add_zip, place.zip)

        threading.Thread(target=_work, daemon=True,
                         name="xx-weather-add").start()

    def _show_zip_error(self, message: str) -> bool:
        self.zip_error.set_text(message)
        self.zip_error.set_visible(True)
        return False

    def _after_add_zip(self, zip_code: str) -> bool:
        self.zip_entry.set_text("")
        self._render_settings()
        self._render_places()
        self._render_forecast()
        self.show("forecast")
        self.refresh_visible(True)
        return False

    # -- day + licenses pages ----------------------------------------------------

    def _build_day_page(self) -> None:
        Gtk = self._Gtk
        self.day_header_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.day_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=0)
        self.day_body.set_margin_top(8)
        self.day_body.set_margin_bottom(28)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_margin_start(20)
        page.set_margin_end(20)
        page.set_margin_top(12)
        page.append(self.day_header_slot)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.day_body)
        page.append(scroll)
        self.stack.add_named(page, "day")

    def _render_day(self) -> None:
        Gtk, W = self._Gtk, self._W
        from xx_phone.weather import full_date, hilo, hour_label, temp_str

        while (child := self.day_body.get_first_child()) is not None:
            self.day_body.remove(child)
        while (child := self.day_header_slot.get_first_child()) is not None:
            self.day_header_slot.remove(child)
        units = self.store.units()
        selected = self.store.selected()
        data = self.state.data_by_zip.get(selected.zip) if selected else None
        day = self.day_date
        hours = data.hourly_on(day) if data is not None and day is not None \
            else []
        point = None
        if data is not None and day is not None:
            point = next((d for d in data.daily if d.date == day), None)
        summary = ""
        if point is not None:
            bits = [b for b in [hilo(point.hi_f, point.lo_f, units),
                                f"{point.pop_pct}% precip"
                                if point.pop_pct is not None else ""]
                    if b]
            summary = " · ".join(bits)
        title = full_date(day) if day is not None else "Day"
        header = W.compact_header(lambda: self.show("forecast"), title,
                                  summary)
        # The page body already carries the gutter.
        header.remove_css_class("xx-page-body")
        self.day_header_slot.append(header)
        if not hours:
            return
        from datetime import datetime as _now

        now_hour = _now.now().hour
        for pos, hour in enumerate(hours):
            when = "Now" if pos == 0 and hour.time.hour == now_hour else \
                hour_label(hour.time)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.add_css_class("xx-weather-hour")
            time_l = W.label(when, "xx-secondary")
            time_l.set_width_chars(6)
            time_l.set_valign(Gtk.Align.CENTER)
            row.append(time_l)
            temp_l = W.label(temp_str(hour.temp_f, units), "xx-title")
            temp_l.set_hexpand(True)
            temp_l.set_valign(Gtk.Align.CENTER)
            row.append(temp_l)
            pop = hour.pop_pct or 0
            pop_l = W.label(f"{pop}%" if pop >= 20 else "", "xx-secondary",
                            xalign=1.0)
            pop_l.set_valign(Gtk.Align.CENTER)
            row.append(pop_l)
            self.day_body.append(row)

    def _build_licenses_page(self) -> None:
        Gtk, W = self._Gtk, self._W
        header = W.compact_header(lambda: self.show("settings"), "LICENSES")
        header.remove_css_class("xx-page-body")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.set_margin_top(8)
        body.set_margin_bottom(28)
        for pos, (name, terms) in enumerate(LICENSE_ROWS):
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            row.add_css_class("xx-setting")
            if pos == len(LICENSE_ROWS) - 1:
                row.add_css_class("xx-weather-last")
            row.append(W.label(name, "xx-title"))
            row.append(W.wrap_label(terms, "xx-secondary"))
            body.append(row)
        self.stack.add_named(self._page_shell(header, body), "licenses")

    # -- refresh ---------------------------------------------------------------

    def start(self) -> None:
        """Paint from prefs, then load caches + refresh off the main loop."""
        self._render_forecast()
        self._render_places()
        self._render_settings()
        self.refresh_visible(False)
        if not self._timer_id:
            self._timer_id = self._GLib.timeout_add_seconds(900, self._tick)

    def _tick(self) -> bool:
        self.refresh_visible(False)
        return True

    def refresh_forced(self) -> None:
        self.refresh_visible(True)

    def refresh_visible(self, force: bool) -> None:
        if self.refreshing and not force:
            return
        places = self.store.places()
        if not places:
            self.loading = False
            self._render_forecast()
            return
        self.refreshing = True
        self._render_forecast()

        def _work() -> None:
            from xx_weather.worker import refresh_place

            results = []
            targets = places
            if not force:
                selected = self.store.selected()
                if selected is not None:
                    targets = [selected] + [p for p in places
                                            if p.zip != selected.zip]
            for place in targets:
                try:
                    results.append((place.zip,
                                    refresh_place(self.store, place,
                                                  force=force)))
                except Exception as exc:  # noqa: BLE001 - one ZIP never
                    from xx_phone.weather import RefreshFailure  # sinks all

                    results.append((place.zip, RefreshFailure(
                        str(exc) or "Could not reach weather services.",
                        self.store.load_cached(place))))
            self._GLib.idle_add(self._after_refresh, results)

        threading.Thread(target=_work, daemon=True,
                         name="xx-weather-refresh").start()

    def _after_refresh(self, results: list) -> bool:
        from xx_phone.weather import RefreshSuccess
        from xx_weather.notify import pending_fires, record_fires, send_fire

        for zip_code, result in results:
            self.state = self._apply_refresh_result(self.state, zip_code,
                                                    result)
            if isinstance(result, RefreshSuccess):
                try:
                    fires = pending_fires(self.store, result.data,
                                          int(time.time() * 1000))
                    for fire in fires:
                        try:
                            send_fire(self.app, fire)
                        except Exception:  # noqa: BLE001 - notify never
                            pass  # breaks a forecast refresh
                    record_fires(self.store, fires)
                except Exception:  # noqa: BLE001 - alerts never break paint
                    pass
        self.loading = False
        self.refreshing = False
        self._render_forecast()
        self._render_places()
        if self.page == "day":
            self._render_day()
        return False

    def open_zip(self, zip_code: str) -> None:
        places = self.store.places()
        if any(p.zip == zip_code for p in places):
            self.store.set_selected_zip(zip_code)
            self._render_forecast()
            self._render_places()
            self.show("forecast")
            self.refresh_visible(False)
        self.win.present()


def _seed_scenario(window, scenario: str, page: str) -> None:
    from xx_phone.weather import RefreshFailure, RefreshSuccess
    from xx_weather import demo

    store = window.store
    if scenario in ("current", "places", "settings", "alerts", "day"):
        demo.seed_places(store) if scenario == "places" \
            else demo.seed_current(store)
        for place in store.places():
            data = store.load_cached(place)
            if data is not None:
                window.state = window._apply_refresh_result(
                    window.state, place.zip, RefreshSuccess(data, True))
        if scenario == "places":
            window.state = window._apply_refresh_result(
                window.state, demo.AUSTIN_ZIP,
                RefreshFailure("Couldn't reach weather services.", None))
    if scenario == "alerts":
        from xx_phone.weather import ZipAlertTriggers

        store.set_alerts_enabled(True)
        store.set_triggers(demo.DENVER_ZIP, ZipAlertTriggers(
            precip_enabled=True, precip_hours=6,
            temp_enabled=True, temp_at_or_below_f=32))
    if scenario in ("error", "loading"):
        from xx_phone.weather import Place

        store.add_place(Place(zip=demo.DENVER_ZIP, city=demo.DENVER_CITY,
                              state=demo.DENVER_STATE, lat=demo.DENVER_LAT,
                              lon=demo.DENVER_LON))
    if scenario == "error":
        from xx_phone.weather import RefreshFailure

        window.loading = False
        window.state = window._apply_refresh_result(
            window.state, demo.DENVER_ZIP,
            RefreshFailure("Couldn't reach weather services.", None))
    window._render_settings()
    window._render_places()
    if page == "day" or scenario == "day":
        selected = store.selected()
        data = store.load_cached(selected) if selected is not None else None
        if data is not None and data.daily:
            window.state = window._apply_refresh_result(
                window.state, selected.zip, RefreshSuccess(data, True))
            window.day_date = data.daily[0].date
            window._render_day()
            window.stack.set_visible_child_name("day")
            window.page = "day"
            return
    window.show(page if page in ("forecast", "places", "settings",
                                   "licenses", "day") else "forecast")


class WeatherApp:
    def __init__(self) -> None:
        Adw, _Gdk, Gio, GLib, _Gtk, _Pango = _require_gtk()
        from xx_weather import __app_id__

        self._Gio, self._GLib = Gio, GLib
        self._app = Adw.Application(application_id=__app_id__,
                                    flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._app.connect("activate", self._on_activate)
        action = Gio.SimpleAction.new("open-zip",
                                      GLib.VariantType.new("s"))
        action.connect("activate", self._on_open_zip)
        self._app.add_action(action)
        self.window = None

    def _on_open_zip(self, _action, param) -> None:
        if self.window is not None:
            try:
                self.window.open_zip(param.get_string())
            except Exception:  # noqa: BLE001 - a bad tap never crashes
                pass

    def _on_activate(self, app) -> None:
        from xx_weather.store import Store

        self._app._weather_window = getattr(self, "window", None)
        if self.window is None:
            self.window = WeatherWindow(app, Store())
            self._app._weather_window = self.window
        scenario = os.environ.get("XX_WEATHER_SCENARIO", "")
        page = os.environ.get("XX_WEATHER_PAGE", "")
        if scenario:
            _seed_scenario(self.window, scenario, page or "forecast")
        else:
            if page in ("forecast", "places", "settings", "licenses",
                        "day"):
                self.window.show(page)
        self.window.win.present()
        if not self.window._started:
            self.window._started = True
            if not scenario:
                self.window.start()
            else:
                if scenario != "loading":
                    self.window.loading = False
                self.window._render_forecast()
                self.window._render_places()

    def run(self, argv=None) -> int:
        return self._app.run(argv if argv is not None else [])


def refresh_once() -> int:
    """Headless refresh for the systemd user timer: refresh every saved
    location, send newly-fired alerts, record dedupe keys."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    from xx_weather import __app_id__
    from xx_weather.notify import pending_fires, record_fires, send_fire
    from xx_weather.store import Store
    from xx_weather.worker import refresh_place

    store = Store()
    places = store.places()
    if not places:
        print("xx-weather: no saved locations")
        return 0
    app = Gio.Application.new(__app_id__, Gio.ApplicationFlags.FLAGS_NONE)
    try:
        app.register()
    except Exception as exc:  # noqa: BLE001 - no bus, no notifications
        print(f"xx-weather: cannot register: {exc}")
        return 1
    app.hold()
    now_ms = int(time.time() * 1000)
    for place in places:
        try:
            result = refresh_place(store, place, force=False,
                                   budget_ms=60000)
        except Exception as exc:  # noqa: BLE001 - one ZIP never sinks all
            print(f"xx-weather: {place.zip}: {exc}")
            continue
        from xx_phone.weather import RefreshSuccess

        if not isinstance(result, RefreshSuccess):
            print(f"xx-weather: {place.zip}: {result.message}")
            continue
        try:
            fires = pending_fires(store, result.data, now_ms)
            for fire in fires:
                try:
                    send_fire(app, fire)
                except Exception:  # noqa: BLE001 - keep evaluating
                    pass
            record_fires(store, fires)
            print(f"xx-weather: {place.zip}: ok ({len(fires)} alerts)")
        except Exception as exc:  # noqa: BLE001 - keep going
            print(f"xx-weather: {place.zip}: {exc}")
    app.release()
    return 0


def main(argv=None) -> int:
    import sys

    args = list(argv) if argv is not None else sys.argv[1:]
    if "--refresh-once" in args:
        return refresh_once()
    app = WeatherApp()
    return app.run(args)
