# xx-radio

An unofficial YouTube Music player for Linux phones (postmarketOS, Mobian,
FuriOS and other phoc-based systems running XX-WM or Phosh). It talks to
YouTube directly from the phone. There is no server.

Status: spec + scaffold. See [PORT_SPEC.md](PORT_SPEC.md) for the product,
the engine decisions, the architecture and the build plan.

## Disclaimer

xx-radio is not affiliated with, endorsed by or supported by YouTube or
Google. It reads YouTube Music through the private interfaces of its own
web and app clients (via ytmusicapi and yt-dlp) and plays the audio
without ads. YouTube's Terms of Service do not allow that, so using it
breaks those terms. YouTube changes these interfaces without notice, and
any change can stop search or playback until the engine is updated. If
you sign in, the account you use could be limited or suspended; a
throwaway account is the careful choice. Downloads are for your own
offline listening on your own phone. You use xx-radio at your own risk.

## Run from the checkout

```sh
PYTHONPATH=src:../common python3 -m xx_radio
XX_RADIO_ENGINE=fake PYTHONPATH=src:../common python3 -m xx_radio   # demo catalog, no network
```

Requires Python 3.12+, GTK4 + libadwaita with PyGObject introspection.
Playback (later stages) needs GStreamer (`Gst`, good + base plugins) and
libsecret (`Secret`). The YouTube engine needs these packages, all from
the distribution (details and fallbacks in PORT_SPEC.md):

| Piece | postmarketOS / Alpine | Arch | Debian |
|---|---|---|---|
| Catalog | `py3-ytmusicapi` | `python-ytmusicapi` | `python3-ytmusicapi` (testing) |
| Stream resolver | `yt-dlp` (pulls `yt-dlp-ejs` + a JS runtime) | `yt-dlp` (pulls `yt-dlp-ejs`, `deno`) | `yt-dlp` (+ `deno`, `nodejs` or `quickjs`) |
| Token helper | set up from Settings, Playback engine | same | same |

Without the token helper, search and browse work and downloaded songs
play, but YouTube offers no audio to stream.

## Layout

| Path | What lives there |
|------|------------------|
| `PORT_SPEC.md` | Product, engine decisions with package evidence, architecture, UI, milestones, extending |
| `meson.build` / `data/` | Install: packages to the datadir, launcher, `.desktop`, metainfo, icon, D-Bus activation |
| `src/xx_music/` | The reusable core: engine contract + models, fake engine, app spec (extension points), paths, worker threads, core UI. Never imports a music source. |
| `src/xx_radio/` | This app: its `AppSpec` (`main.py`) and the YouTube Music engine (`youtube/`) |
| `tests/` | Headless pytest. Never touches the network (a socket guard fails any attempt). |

## Install

Install `common/` first: the app requires the installed `xx_ui` package
and never vendors a copy (`meson setup` fails otherwise):

```sh
meson setup build && meson install -C build   # in common/
meson setup build && meson install -C build   # here
```

## Test

```sh
sh ../scripts/check-all.sh xx-radio
```

## License

GPL-3.0, like the rest of the repository.
