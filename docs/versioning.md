# Per-app versioning

Each XX app rolls its own version independently. The `version:` in the app
dir's `meson.build` is THE version (xx-wm's lives in
`xx-wm/launcher/meson.build`). Most apps are at 0.1.0; xx-wm/launcher is at
0.1.1; xx-keyboard tracks its upstream Stevia fork scheme (0.58.0).

## The rule

- Bump the app's meson `version:` in the same change that alters its
  behavior. Patch for fixes, minor for features, major for breaking changes.
- `scripts/app_versions.py` is the tooling:
  - `generate` rewrites `apps.json` from the tree. Commit the result with
    the change.
  - `bump <app> [patch|minor|major]` edits meson.build and regenerates
    `apps.json` in one step.
  - `check` is the gate: `apps.json` must match the meson versions, and
    (when git history is available) any app dir changed since the commit
    that last touched `apps.json` must carry a newer version than the one
    recorded there.
- The gate runs as `xx-wm/tests/test_app_versions.py` (via
  `sh scripts/check-all.sh xx-wm`), or standalone:
  `python3 scripts/app_versions.py check`.

## When common changes

`common` is shared code, not an app: it carries its own version and every
dependent records the common version it was generated against in its
`apps.json` entry (`deps: ["common"]` plus a `common:` field). A change to
`common/` therefore requires only a `common` version bump plus a regenerated
`apps.json` — dependents do NOT need a version bump of their own. The
updater (`xx-apps-update --apply`) rebuilds every installed dependent whose
recorded `common` is older than the available one, dependencies first, so a
common-only change still reaches all apps on the next update.

## Installed state

`install.sh` records each app it installs in
`/var/lib/xx-apps/installed.json` (app → version, the common version it was
built against, and the source commit), written atomically at the end of each
app install. `xx-apps-update --check` compares that file against the
`apps.json` published on GitHub main at one pinned commit SHA, so check and
apply always see the same tree.

Apply runs install.sh's per-app steps (`meson setup --prefix=/usr`, then
`meson install`) for only the outdated apps, dependencies first, followed
by install.sh's `refresh_running_apps`. One deliberate difference: apply
compiles every target (`meson compile`) before installing any of them, so
a build failure leaves the installed apps untouched with one plain-line
error. Only `meson install` runs as root.

## Owner hook

`docs/update-system-hook.patch` is a small optional step for the owner's
`update-system.sh` (piercing-dots repo): when `xx-apps-update` exists it
runs `xx-apps-update --apply` through the script's own
`run_step`/`record_step` pattern so the result lands in the summary table.
It is applied to the owner's repo after review, never vendored here.

Note for the review: the new `update_xx_apps` step returns `$SKIP` when
`xx-apps-update` is not on PATH, so machines without the XX suite see no
new summary row at all. `xx-apps-update --apply` never prompts (root via
non-interactive sudo or pkexec, stdin never read), so it is already
non-interactive under the script's headless defaults. The step sits right
after Flatpak in `universal_steps`, so it still runs when the core package
upgrade succeeded and is skipped with everything else when it failed.
Apply with `patch -p1` from the piercing-dots root; verified with
`patch --dry-run` plus a shell syntax check of the patched file.
