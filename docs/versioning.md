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
`apps.json` of the newest signed release (see "Releases" below), so check
and apply always see the same verified tree. Apply records the release tag
next to each app's commit.

Apply runs install.sh's per-app steps (`meson setup --prefix=/usr`, then
`meson install`) for only the outdated apps, dependencies first, followed
by install.sh's `refresh_running_apps`. One deliberate difference: apply
compiles every target (`meson compile`) before installing any of them, so
a build failure leaves the installed apps untouched with one plain-line
error. Only `meson install` runs as root.

## Releases

Phones never build `main`. `xx-apps-update` installs only from a release
tag signed with the xx-os release key, and builds exactly the commit that
tag names.

**Tag scheme.** One tag per release of the whole repository:
`vYYYY.MM.DD`, then `vYYYY.MM.DD.1`, `.2`, … for further releases the same
(UTC) day. Per-app tags would not fit: an app builds against `common` from
the same tree, the updater installs a set of apps in dependency order from
one tree, and each app's own version already sits in that tree's
`apps.json`. A repository tag pins all of that with one signature, and the
per-app versions still decide which apps get rebuilt.

**Cutting a release.** From a clean checkout of a commit that is on
`origin/main` and green in CI, with `gh` logged in:

    scripts/release.sh            # tags v<today>, or v<today>.N
    scripts/release.sh v2026.11.12  # an explicit name, same rules

The script refuses a dirty tree, a tree that fails
`scripts/app_versions.py check` (phones rebuild only apps whose version
went up, so an unbumped change would ship nothing), a commit that is not
on the remote's `main`, a commit that does not descend from the previous release (phones
would refuse it), a tag name that does not sort after the latest release,
and a commit whose CI runs (`gh run list --commit`) are missing, still
running or failed. When `gh` is missing, logged out or erroring, it stops
unless `--force` is given (check CI yourself first); `--force` never
overrides a run gh reports as not passed. It signs an annotated tag with
`~/.ssh/xx-os-release` (`--key` or `XX_RELEASE_KEY` for another path),
verifies it against `xx-wm/launcher/data/release-signers` before pushing,
deletes it again if that fails, and pushes only the tag.

The signing is plain git and works by hand too:

    git -c gpg.format=ssh -c user.signingkey=~/.ssh/xx-os-release \
        tag -s -m "xx-os v2026.11.12" v2026.11.12
    git push origin refs/tags/v2026.11.12

**How phones verify.** The public half of the key ships with the shell as
an OpenSSH allowed-signers file, `xx-wm/launcher/data/release-signers`,
installed root-owned to `/usr/share/xx-wm/release-signers`; its one line
gives the principal `release@xx-os` with `namespaces="git"`. On check and
apply, `xx-apps-update`:

1. fetches `main` and all tags from `https://github.com/PiercingXX/xx-os.git`
   into a cache clone (`~/.cache/xx-apps-update/xx-os.git`; tags are
   force-updated and pruned, so it mirrors the origin);
2. takes the newest tag matching the scheme. If that tag is bad it stops;
   it never falls back to an older one;
3. requires an annotated tag object whose `type` is `commit` and whose
   embedded `tag` name equals the ref name, so an old signed release
   pushed under a newer name is refused;
4. runs `git verify-tag` with `gpg.format=ssh`,
   `gpg.ssh.allowedSignersFile=/usr/share/xx-wm/release-signers` and
   `gpg.minTrustLevel=fully`, and also requires ssh-keygen's
   `Good "git" signature for release@xx-os` line. Unsigned, lightweight,
   tampered and other-key tags all fail here;
5. reads `apps.json` from the tagged commit and works out the updates;
6. refuses the release when it does not descend from the commit an app to
   be updated was installed from (`git merge-base --is-ancestor`), or when
   that commit is not in the fetched history: no downgrades, no side
   branches. Records without a full commit SHA (installs from a tarball)
   have nothing to compare and rely on the version check;
7. exports exactly the tagged commit with `git archive` and builds and
   installs from that.

Check and apply need `git` and `ssh-keygen` (OpenSSH) on the phone.
Phones that still run an updater from before signed releases update once
more from `main`; that update installs the signers file and this updater,
and from then on only signed tags are installed.

**Rotating the key.** Phones trust whatever the installed signers file
lists, and that file only changes through a release, so the new key has to
reach them through a release signed with the old one:

1. Make the new key (`ssh-keygen -t ed25519 -C "xx-os release signing"
   -f ~/.ssh/xx-os-release-new`) and add its public half to
   `xx-wm/launcher/data/release-signers` as a second `release@xx-os
   namespaces="git" …` line. Bump xx-wm's version (the file ships with
   xx-wm), get it through CI and cut a release with the **old** key.
2. Once phones have that release, sign the next release with the new key
   (`--key ~/.ssh/xx-os-release-new`, or move it to the default path).
3. In a later release, drop the old key's line.

If the old key is compromised, remove its line in step 1 as well; phones
that never got that release keep trusting the old key until the shell is
reinstalled with `install.sh` from a fixed checkout. Never commit or copy
the private key; only `release-signers` lives in the repository.

## Owner hook

`docs/update-system-hook.patch` is a small optional step for the owner's
`update-system.sh` (piercing-dots repo): when `xx-apps-update` exists it
runs `xx-apps-update --apply` through the script's own
`run_step`/`record_step` pattern so the result lands in the summary table.
It is applied to the owner's repo after review, never vendored here.

Settings runs `update-system.sh` with `PX_NO_SELF_UPDATE=1` (terminal,
headless and the plain "Update" button alike), so the script never
replaces itself and `~/.scripts` from piercing-dots `main`: only the copy
`bootstrap-dots.sh` installed from its pinned commit runs. Taking a newer
script means bumping that pin.

Note for the review: the new `update_xx_apps` step returns `$SKIP` when
`xx-apps-update` is not on PATH, so machines without the XX suite see no
new summary row at all. `xx-apps-update --apply` never prompts (root via
non-interactive sudo or pkexec, stdin never read), so it is already
non-interactive under the script's headless defaults. The step sits right
after Flatpak in `universal_steps`, so it still runs when the core package
upgrade succeeded and is skipped with everything else when it failed.
Apply with `patch -p1` from the piercing-dots root; verified with
`patch --dry-run` plus a shell syntax check of the patched file.
