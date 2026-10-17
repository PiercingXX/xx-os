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

APP_CSS = """
.xx-weather-hero { font-size: 36px; font-weight: 300; }
.xx-weather-sub { font-size: 15px; }
.xx-hour-temp { font-size: 16px; }
"""


def _require_gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: F401

    return Adw, Gdk, Gio, GLib, Gtk, Pango


LICENSES_TEXT = (
    "Forecasts: NWS weather.gov (US public domain).\n\n"
    "Weather data by Open-Meteo.com (CC BY 4.0).\n\n"
    "ZIP lookup via Zippopotam.us from GeoNames (CC BY 4.0).\n\n"
    "App code: GPL-3.0-only."
)

FOOTER_TEXT = ("Forecast: NWS weather.gov \u2022 Weather data by "
               "Open-Meteo.com (CC BY 4.0) \u2022 ZIP lookup: "
               "GeoNames (CC BY 4.0)")

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
        try:
            xx_ui.apply(self.win)
        except Exception:  # noqa: BLE001 - theme is decorative
            pass
        self._load_app_css()

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

    def _load_app_css(self) -> None:
        try:
            Gtk, Gdk = self._Gtk, self._Gdk
            provider = Gtk.CssProvider()
            provider.load_from_data(APP_CSS.encode("utf-8"))
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display, provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
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
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(12)
        body.set_margin_bottom(28)
        self.zip_chips_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.zip_chips_box)
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

    def _render_forecast(self) -> None:
        Gtk, W = self._Gtk, self._W
        from xx_phone.weather import (condition_label, day_label, full_date,
                                      hilo, hour_label, temp_str,
                                      updated_label)

        while (child := self.forecast_body.get_first_child()) is not None:
            self.forecast_body.remove(child)
        while (child := self.zip_chips_box.get_first_child()) is not None:
            self.zip_chips_box.remove(child)

        places = self.store.places()
        selected = self.store.selected()
        if len(places) > 1 and selected is not None:
            chips = W.chip_bar([(p.city, p.zip == selected.zip,
                                 self._select_zip_cb(p.zip)) for p in places])
            self.zip_chips_box.append(chips)

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
        if not places:
            self.spinner.set_visible(False)
            self.spinner.stop()
            box = W.empty_state(
                "Enter a ZIP code for accurate NWS forecasts. "
                "No location tracking, no extra services.",
                "SET ZIP CODE", lambda: self.show("settings"))
            self.forecast_body.append(box)
            self._update_caption(None)
            return
        if data is None and error_msg is None:
            self.spinner.set_visible(True)
            self.spinner.start()
            self._update_caption(
                selected.display_name if selected is not None else None)
            return
        self.spinner.set_visible(False)
        self.spinner.stop()
        if data is None:
            box = W.empty_state("Couldn't load this forecast.",
                                "TRY AGAIN", self.refresh_forced)
            self.forecast_body.append(box)
            self._update_caption(selected.display_name)
            return

        stamp = (f"{data.place.display_name} \u2022 Updated "
                 f"{updated_label(data.updated_at_ms)}")
        self._update_caption(stamp)

        hero = W.card()
        today = data.daily[0] if data.daily else None
        hero_date = today.date if today is not None else None
        date_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        date_label = W.label(
            full_date(hero_date) if hero_date is not None else "", "xx-title")
        date_label.set_hexpand(True)
        date_row.append(date_label)
        if hero_date is not None:
            day_btn = W.text_button("DAY",
                                    self._open_day_cb(hero_date))
            day_btn.set_valign(Gtk.Align.CENTER)
            date_row.append(day_btn)
        hero.append(date_row)
        cond = W.wrap_label(data.current.condition_text, "xx-secondary")
        hero.append(cond)
        temp = W.label(temp_str(data.current.temp_f, units),
                       "xx-weather-hero")
        hero.append(temp)
        if today is not None:
            hero.append(W.label(hilo(today.hi_f, today.lo_f, units),
                                "xx-weather-sub"))
        if data.current.feels_like_f is not None:
            hero.append(W.label(
                f"Feels like {temp_str(data.current.feels_like_f, units)}",
                "xx-secondary"))
        self.forecast_body.append(hero)

        self.forecast_body.append(W.section_label("HOURLY FORECAST"))
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=22)
        strip.set_margin_start(2)
        for index, hour in enumerate(list(data.hourly)[:24]):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            col.set_valign(Gtk.Align.START)
            col.set_size_request(46, -1)
            when = "Now" if index == 0 else hour_label(hour.time)
            time_l = W.label(when, "xx-secondary", xalign=0.5)
            time_l.set_halign(Gtk.Align.CENTER)
            col.append(time_l)
            temp_l = W.label(temp_str(hour.temp_f, units),
                             "xx-hour-temp", xalign=0.5)
            temp_l.set_halign(Gtk.Align.CENTER)
            col.append(temp_l)
            pop = hour.pop_pct or 0
            pop_l = W.label(f"{pop}%" if pop >= 30 else "", "xx-secondary",
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
            if point.pop_pct is not None:
                secondary = f"{secondary} \u2022 {point.pop_pct}%"
            row = W.list_row(
                day_label(point.date), secondary,
                hilo(point.hi_f, point.lo_f, units),
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
            tile.append(W.caption(name))
            tile.append(W.label(value, "xx-title"))
            if sub:
                tile.append(W.label(sub, "xx-secondary"))
            grid.attach(tile, pos % 2, pos // 2, 1, 1)
        self.forecast_body.append(grid)

        foot = W.wrap_label(FOOTER_TEXT, "xx-secondary")
        self.forecast_body.append(foot)

    def _select_zip_cb(self, zip_code: str):
        def _select() -> None:
            self.store.set_selected_zip(zip_code)
            self._render_forecast()
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
            ("PRECIP CHANCE", pop, "Next 12 hours"),
        ]

    # -- places page ---------------------------------------------------------

    def _build_places_page(self) -> None:
        Gtk, W = self._Gtk, self._W
        header = W.page_header("Locations", None,
                               [("ADD", lambda: self.show("settings"))])
        self.places_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=12)
        self.places_body.set_margin_top(12)
        self.places_body.set_margin_bottom(28)
        self.stack.add_named(self._page_shell(header, self.places_body),
                             "places")

    def _render_places(self) -> None:
        W = self._W
        from xx_phone.weather import temp_str

        while (child := self.places_body.get_first_child()) is not None:
            self.places_body.remove(child)
        places = self.store.places()
        units = self.store.units()
        selected = self.store.selected_zip()
        if not places:
            self.places_body.append(W.empty_state(
                "No saved locations yet.", "ADD A ZIP CODE",
                lambda: self.show("settings")))
            return
        for pos, place in enumerate(places):
            data = self.state.data_by_zip.get(place.zip)
            failed = (place.zip in self.state.errors
                      or (place.zip in self.state.data_by_zip
                          and data is None))
            if data is not None:
                secondary = data.current.condition_text
                meta = temp_str(data.current.temp_f, units)
            else:
                secondary = ("Couldn't load" if failed else "Loading\u2026")
                meta = "--"
            name = place.display_name
            if place.zip == selected:
                name = f"\u2022 {name}"
            row = W.list_row(name, f"{place.zip} \u2022 {secondary}", meta,
                             on_activate=self._select_zip_cb(place.zip),
                             last=pos == len(places) - 1)
            self.places_body.append(row)
            if len(places) > 1:
                remove = W.text_button("REMOVE",
                                       self._remove_zip_cb(place.zip))
                remove.set_halign(self._Gtk.Align.END)
                self.places_body.append(remove)

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
        self.refresh_visible(False)
        return False

    # -- settings page ---------------------------------------------------------

    def _build_settings_page(self) -> None:
        Gtk, W = self._Gtk, self._W
        header = W.page_header("Settings", None, None)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(12)
        body.set_margin_bottom(28)

        body.append(W.section_label("ADD ZIP CODE"))
        self.zip_entry = Gtk.Entry()
        self.zip_entry.set_placeholder_text("e.g. 00802")
        self.zip_entry.set_max_length(5)
        self.zip_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.zip_entry.set_max_width_chars(12)
        body.append(self.zip_entry)
        self.zip_error = W.wrap_label("", "xx-secondary")
        self.zip_error.add_css_class("xx-danger-text")
        self.zip_error.set_visible(False)
        body.append(self.zip_error)
        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        add_row.set_halign(Gtk.Align.END)
        add_row.append(W.primary_button("ADD", self._on_add_zip))
        body.append(add_row)

        body.append(W.section_label("TEMPERATURE"))
        self.units_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.units_box)

        body.append(W.section_label("WEATHER ALERTS"))
        self.alerts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=8)
        body.append(self.alerts_box)

        body.append(W.section_label("ABOUT"))
        about_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        about_row.append(W.text_button("LICENSES",
                                       lambda: self.show("licenses")))
        about_row.set_halign(Gtk.Align.START)
        body.append(about_row)
        body.append(W.wrap_label(
            "ZIP-code weather from the National Weather Service. "
            "US only, no location tracking.", "xx-secondary"))

        self.settings_body = body
        self.stack.add_named(self._page_shell(header, body), "settings")

    def _render_settings(self) -> None:
        W = self._W
        from xx_phone.weather import Units

        while (child := self.units_box.get_first_child()) is not None:
            self.units_box.remove(child)
        units = self.store.units()
        self.units_box.append(W.segmented([
            ("\u00b0F", units == Units.FAHRENHEIT,
             lambda: self._set_units(Units.FAHRENHEIT)),
            ("\u00b0C", units == Units.CELSIUS,
             lambda: self._set_units(Units.CELSIUS)),
        ]))

        while (child := self.alerts_box.get_first_child()) is not None:
            self.alerts_box.remove(child)
        enabled = self.store.alerts_enabled()
        selected = self.store.selected()
        self.alerts_box.append(W.setting_row(
            "Enable alerts",
            "Forecasts work with it off.",
            W.make_switch(enabled, self._on_alerts_enabled), last=True))
        if not enabled:
            return
        if selected is None:
            self.alerts_box.append(W.wrap_label(
                "Save a ZIP to attach rain and freeze triggers.",
                "xx-secondary"))
            return
        triggers = self.store.ensure_triggers(selected.zip)
        note = W.wrap_label(
            f"Watching {selected.display_name} ({selected.zip}). "
            "Same 15-minute refresh, no extra polling.", "xx-secondary")
        self.alerts_box.append(note)
        self.alerts_box.append(W.setting_row(
            "Rain in the next N hours", "PoP 50%+ or falling precip",
            W.make_switch(triggers.precip_enabled,
                          lambda on: self._with_triggers(
                              selected.zip, precip_enabled=on)),
            last=True))
        self.alerts_box.append(W.chip_bar(
            [(f"{h}H", triggers.precip_hours == h,
              self._hours_cb(selected.zip, h)) for h in ALERT_HOURS]))
        self.alerts_box.append(W.setting_row(
            "Temperature at or below", "Threshold is \u00b0F",
            W.make_switch(triggers.temp_enabled,
                          lambda on: self._with_triggers(
                              selected.zip, temp_enabled=on)),
            last=True))
        self.alerts_box.append(W.chip_bar(
            [(f"{t}\u00b0F", triggers.temp_at_or_below_f == t,
              self._temp_cb(selected.zip, t)) for t in ALERT_TEMPS_F]))

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
        Gtk, W = self._Gtk, self._W
        header = W.compact_header(lambda: self.show("forecast"), "DAY",
                                  "", "", None)
        self.day_header_box = header
        self.day_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=2)
        self.day_body.set_margin_top(12)
        self.day_body.set_margin_bottom(28)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_margin_start(20)
        page.set_margin_end(20)
        page.set_margin_top(12)
        page.append(header)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.day_body)
        page.append(scroll)
        self.stack.add_named(page, "day")

    def _render_day(self) -> None:
        W = self._W
        from xx_phone.weather import full_date, hilo, hour_label, temp_str

        while (child := self.day_body.get_first_child()) is not None:
            self.day_body.remove(child)
        units = self.store.units()
        selected = self.store.selected()
        data = self.state.data_by_zip.get(selected.zip) if selected else None
        day = self.day_date
        hours = data.hourly_on(day) if data is not None and day is not None \
            else []
        point = None
        if data is not None and day is not None:
            point = next((d for d in data.daily if d.date == day), None)
        title = full_date(day) if day is not None else "Day"
        self.day_body.append(W.label(title, "xx-display"))
        if point is not None:
            bits = [b for b in [hilo(point.hi_f, point.lo_f, units),
                                f"{point.pop_pct}% precip"
                                if point.pop_pct is not None else ""]
                    if b]
            if bits:
                self.day_body.append(W.label(" \u00b7 ".join(bits),
                                             "xx-secondary"))
        self.day_body.append(W.section_label("HOURLY"))
        if not hours:
            self.day_body.append(W.wrap_label(
                "No hourly forecast for this day.", "xx-secondary"))
            return
        from datetime import datetime as _now

        now_hour = _now.now().hour
        for pos, hour in enumerate(hours):
            when = "Now" if pos == 0 and hour.time.hour == now_hour else \
                hour_label(hour.time)
            pop = hour.pop_pct or 0
            meta = temp_str(hour.temp_f, units)
            if pop >= 20:
                meta = f"{meta}  {pop}%"
            self.day_body.append(W.list_row(
                when, "", meta, last=pos == len(hours) - 1))

    def _build_licenses_page(self) -> None:
        Gtk, W = self._Gtk, self._W
        header = W.compact_header(lambda: self.show("settings"), "LICENSES",
                                  "", "", None)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(12)
        body.set_margin_bottom(28)
        body.append(W.wrap_label(LICENSES_TEXT, "xx-secondary"))
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
