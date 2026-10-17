# xx-weather

ZIP-code weather for Linux phones: the PiercingXX Android xx-weather
("Weather" on Google Play) ported to GTK4. ZIP in, forecast out — NWS
first, Open-Meteo if NOAA is down. No location permission, no extra
services.

- Current conditions, 24-hour strip, 10-day outlook, details grid
- Saved locations with per-ZIP caches and honest "Updated" stamps
- Optional rain/freeze alerts on the same 15-minute refresh
- Offline: last-good cache stays on screen, never a lying clock

See [PORT_SPEC.md](PORT_SPEC.md) for the Android → Linux mapping.

## Run

```sh
cd xx-weather && meson setup build --prefix=/usr && sudo meson install -C build
xx-weather
```

Background refresh (opt-in, evaluates alerts while the app is closed):

```sh
systemctl --user enable --now xx-weather-refresh.timer
```

## Develop

```sh
sh scripts/check-all.sh xx-weather
sh xx-weather/tools/snap_all.sh xx-weather/lane_shots
```

## License

GPL-3.0. See [LICENSE](../LICENSE). Forecast data: NWS weather.gov (US
public domain); weather data by Open-Meteo.com (CC BY 4.0); ZIP lookup
via Zippopotam.us from GeoNames (CC BY 4.0).
