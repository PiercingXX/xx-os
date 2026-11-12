# XX phone OS — design for a Pixel 9 Pro build

Written 2026-11-12. Target: Google Pixel 9 Pro (`caiman`, Tensor G4, Mali-G715,
Exynos 5400 modem, 16 GB RAM, USB-C 3.2 with DisplayPort). Base: XX-WM and its apps.

**Status of this document.** It is a design, not a record of work. Nothing has been
tried on a Pixel. Claims are marked *sourced* (a primary page was read), *reported*
(search summary or self-report), *assumed* (background knowledge) or *decision* (a
choice made here). The research notes behind it are kept outside this repository.

---

## 0. Decision record (2026-11-12)

The owner delegated the engineering decision: "I design, you engineer." Owner's
constraints: stand on others' work where possible; the best phone experience and true
convergence; a device that is actually usable; a wrong decision now is expensive later.

**Decided:**

1. **Our investment goes into the layer we own.** A window-management core written in
   Rust: handset policy, dwindle tiling, keybinds, dock behaviour. It is written
   against a narrow interface so the compositor underneath can be replaced.
2. **Compositor now: river, unmodified, from upstream.** Its stable window-manager
   protocol is that interface. No fork.
3. **Contingency: sway.** If river fails the prototype checklist (touch is the open
   question), the same Rust core drives sway through a second adapter.
4. **Own compositor: deferred, with triggers.** Built on Smithay from the pieces in
   the table in section 4, with ideas from Jay, speaking river's protocol so the Rust
   core runs on it unchanged. Triggers: river's touch handling falls short and
   upstream won't take fixes; the handset needs compositor-side behaviour river will
   not carry (finger-tracked transitions, suspending background apps); river's
   one-maintainer risk becomes real.
5. **The shell stays Python/GTK.** 58k lines that work are not rewritten. New
   system-level pieces (window manager, dock controller, trackpad bridge, later
   hardware daemons) are Rust.
6. **Graphics on the Pixel: the kernel's display driver as normal Linux graphics**,
   software-rendered first, accelerated second. The Android-display fallback is not
   pursued for the docked desktop.
7. **Base: Debian.** Target: Pixel 9 Pro only.

**Why this is the low-regret choice:** the two things that are expensive to change
later are the user-facing behaviour and the hardware port. Neither depends on which
compositor is underneath once the Rust core has its interface. The compositor becomes
a part that can be swapped in weeks, not a foundation.

---

## 1. The claim

A Linux phone on current flagship hardware that is also the owner's workstation when a
monitor is plugged in. No Linux port of any Pixel 9 was found, and no mainline kernel
support for its chip (*reported*). The only other systems on the 9 Pro are
Android-based. If this boots, calls, and docks, it is the first.

Three things have to be true for that claim, and the design is ordered around proving
them early:

1. The phone's display and GPU can be driven as ordinary Linux graphics, including the
   external monitor.
2. Voice calls work on the Exynos modem.
3. One shell covers both the handset and the docked desktop.

## 2. What it is, in use

**Handset.** XX-WM as it is today: text home, search drawer, shade, lock, calls, the
first-party apps. One app at a time, gestures, on-screen keyboard.

**Docked** (owner's spec): the monitor shows a top bar and nothing else. Windows tile.
Tapping Super opens a launcher; everything else is the piercing-dots keybinds
(Super+HJKL focus, +Shift move, +Alt resize, Super+1..0 workspaces, Super+Q close,
Super+F float). The phone screen goes black and is the trackpad, with a setting to
change that. The keyboard is Bluetooth or on the dock.

**One session.** Apps opened on the handset are still running when the monitor is
plugged in, and the other way round. Docking does not start a second desktop.

## 3. Architecture

```
  first-party apps (GTK4)        Android apps (container, later)
  ───────────────────────────────────────────────────────────────
  XX-WM shell      handset UI · top bar · launcher · lock · trackpad
  XX-WM window manager   handset policy + tiling policy  (new)
  ───────────────────────────────────────────────────────────────
  compositor       river  (fallback: sway)          Wayland, wlroots
  graphics         kernel DRM/KMS + open Mali driver  (fallback: hwcomposer)
  hardware bridge  Android vendor services in a container:
                   modem · camera · fingerprint · audio DSP · GPS · sensors
  ───────────────────────────────────────────────────────────────
  userspace        Debian stable, own package archive
  kernel           Google's 6.1 Pixel kernel, vendor modules, own build
  boot             Pixel bootloader · A/B slots · own verified-boot key
```

## 4. Compositor: river, with XX-WM as the window manager

**Decision: Wayland, not X11.** On a touch phone X11 has no good answer for the
on-screen keyboard, per-screen scaling, rotation or a secure lock (*assumed*), and the
desktop world is leaving it. The owner's X11 configs stay useful as the source of the
keybinds, not as the display server.

**Decision: do not fork a compositor. Write the window manager.**

| | river 0.5 | sway 1.12 | mango (dwl family) | phoc 0.58 |
|---|---|---|---|---|
| Model | Compositor does drawing and input; **window management is a separate program** speaking a stable protocol | i3-compatible, steered over IPC | Small C compositor with native dwindle | Phone compositor, no tiling beyond half-snap |
| Handset and tiling in one instance | Yes: our program decides, per screen | Yes, by convention (workspaces pinned to screens) | Yes (per-tag layouts) | No |
| Dwindle | Whatever we write | Needs a helper script | Native | None |
| Fork needed | **No** | No | Yes | — |
| Licence / language | GPL-3.0 / Zig | MIT / C | GPL-3.0 / C | GPL-3+ / C |
| Community size | ~1.3k stars, 25 third-party WMs | ~15k stars, runs on phones under Sxmo | ~4k stars | Phosh's compositor |

(*sourced* for river's architecture, protocol list and release; several per-protocol
details for the others are *assumed* and listed for re-checking in the notes.)

Why river:
- Its design since 0.4 is exactly the split this project needs. The README states the
  window-management protocol is stable and "We do not break window managers"
  (*sourced*). XX-WM becomes a window manager in fact, not just in name.
- Layout policy is ours and can differ per screen: handset screen = one app filling
  it; monitor = dwindle tiling with the owner's binds. No convention-bending.
- It exposes every protocol the shell uses today (layer-shell, window list, session
  lock, input method, virtual pointer and keyboard, output and power management,
  screen capture, idle, fractional scale) (*sourced* from its source tree).
- Nothing to carry as a fork. Long-lived forks trail upstream by weeks to months
  (SwayFX landed ~10 weeks after sway 1.12, *sourced*).

What it costs:
- River is close to a one-maintainer project, and it requires a very recent Zig; the
  required version was bumped the day before this was written (*sourced*). We package
  river and its toolchain ourselves.
- None of the 25 existing window managers is for a phone. Touch behaviour and
  what happens if the window-manager program crashes are unverified.

**Fallback: upstream sway, steered over its IPC.** Bigger community, proven on phones,
and the owner already has a 120-bind sway config. Its gaps: no touchscreen gesture
bindings (*assumed*), and "one app at a time" is enforced by convention.

**If the project ever wants to own the compositor: fork mango.** Not now.

**Ruled out:** Hyprland (owner), niri (scrolling columns only, limited touch), qtile
(its new Wayland backend is still fixing memory-safety bugs), X11 window managers for
the handset.

### Rust options, evaluated 2026-11-12

Each option was evaluated against the same checklist; the deciding claims below were
re-read from the primary page afterwards.

| Option | Verdict | Deciding facts |
|---|---|---|
| **Window manager in Rust on river** | **Chosen** | River is Zig, but the part we write can be any language; two of its existing window managers are Rust (`rrwm`, `orilla`) (*sourced*) |
| cosmic-comp | Fork only, heavy | GPL-3.0, tree tiling per screen, has lock / input-method / output-power. Depends on five COSMIC libraries; keybinds live in COSMIC's settings; its control protocol cannot place or tile windows; no virtual pointer (needed for phone-as-trackpad); 950 open issues; nobody found running another shell on it (*sourced*) |
| Jay | Not for a phone yet | Has almost every protocol and an in-process Rust config library. **Touch input is not mentioned anywhere in its feature list** (*re-checked*); needs OpenGL or Vulkan, no software rendering mentioned; no dwindle; one main developer on a from-scratch stack |
| Pinnacle | Too young | Pre-1.0 (v0.2.4), 649 stars. Does let an outside program supply the layout per screen. Phone protocols and touch could not be established |
| Catacomb | Learn from, do not fork | A real phone compositor in Rust (PinePhone, Librem 5), GPL-3.0, with lock, input method and both capture protocols wired up. **Single output, no hot-plug** (*re-checked in its main source file*), touch-only focus model, archived on GitHub in May 2026. Docked mode would be a rewrite of its core |
| Fork niri | Not recommended | 29k stars, per-screen layout overrides. Scrolling columns only; an open bug moves focus when the on-screen keyboard is tapped on multi-screen setups; **its contribution rules forbid LLM-written code and text** (*re-checked*), so nothing built the way this project is built could go upstream |
| Own compositor on Smithay | The serious alternative, later | Smithay supplies layer-shell, lock, input method, virtual keyboard, idle, fractional scale and a software renderer. It does not supply screen capture, virtual pointer, output management or the window-list protocol XX-WM uses. Still 0.x with breaking changes every release. Scale: niri took one developer from 2023 to v0.1 in early 2025 |

Conclusion: no Rust compositor is ready to be the base today. The Rust route that
works now is the one already chosen, with the window manager written in Rust. If the
project later decides to own its compositor, the path is Smithay with Catacomb and
niri as references, and the window-manager logic written for river carries over.

**The decision is confirmed by a prototype, not by this table** (milestone M1): the
same checklist run on river and on sway on the Librem 5, which has a real GPU and
normal Linux graphics.

### What changes in XX-WM

The shell is only lightly tied to phoc: it draws through standard protocols, and the
phoc-specific parts are the lock shield and one auto-maximize setting, in a handful of
files (*measured in the tree*).

| New piece | What it does |
|---|---|
| Compositor backend layer | One interface (`phoc`, `river`, `sway`) for: list/activate/close windows, lock, output power, dock detection. phoc stays supported for FuriOS, Librem 5 and existing installs |
| Window-manager client | Speaks river's protocol. Handset policy and dwindle policy. Reads the keybinds from one file in the piercing-dots vocabulary |
| Lock | Standard session-lock protocol on river and sway (the compositor keeps the screen locked if the shell dies). The phoc shield stays for phoc |
| Top bar | A layer surface on the monitor in the shell's own type and themes: workspaces, focused title, clock, battery, signal, call and notification state |
| Launcher | The existing search, shown as a centered prompt on Super. Same renames, hidden apps and typed verbs as on the handset |
| Dock controller | On monitor connect: black the handset, turn touches into pointer events, move the on-screen keyboard out of the way, hand notifications and calls to the bar. Undo on disconnect |
| Settings | Docked behaviour (phone as trackpad / second screen / off), bar on or off, keybind sheet |

All of this is buildable now on hardware that already works. None of it waits for the
Pixel.

## 5. Graphics: the gate that decides the rest

| Route | What it is | Compositor it allows | Docked monitor |
|---|---|---|---|
| **A. Kernel display driver + open GPU driver** (target) | The Pixel kernel's own display driver used as normal Linux graphics; Mesa's driver for the Mali-G715 | Any current one: river 0.5, sway 1.12 | A normal second screen, if DisplayPort shows up as a connector |
| **B. Same, software-rendered** (first milestone) | As A, CPU rendering | Same | Same; fine for text and terminals, poor for a browser |
| **C. Android's display service through libhybris** (fallback) | What FuriOS does | **Only old ones.** The hwcomposer backend exists only in wlroots forks at 0.17–0.18; river, sway and mango are on 0.20 (*sourced*) | Nobody was found doing it |

So route C would mean staying on phoc for the handset and leaving the monitor
unsolved, or forward-porting the hwcomposer backend ourselves. **The product in
section 2 needs route A or B.**

What is known: Mesa lists the Mali-G715 at GLES 3.1 / Vulkan 1.4 (*sourced*). The
kernel half of that driver is in mainline Linux but not in the Pixel's 6.1 kernel
(*assumed*), so it needs a backport or a kernel bump. Whether the Pixel's display
driver works with a normal compositor, and whether the monitor appears as a connector
without Android's USB service, are both *assumed* and are the first things to test
(milestone M3).

## 6. Hardware bridges

For parts with no open driver, Android's vendor services run in a container and are
bridged to Linux. FuriOS on the FLX1s is a working example of every row except the
modem model.

| Part | Bridge | Risk |
|---|---|---|
| **Modem: data, SMS** | Vendor radio service + oFono's binder plugin, presented as ModemManager | Medium |
| **Modem: voice (VoLTE)** | Same, plus the IMS stack | **High.** No success report found for any Samsung modem this way. US carriers have no calls without it |
| Audio | PipeWire with the Android audio bridge | Medium; the Pixel's audio DSP is a known blocker |
| Camera | Android camera service bridge | High for quality, medium for "works" |
| Fingerprint | fprintd shim over the Android service (as FuriOS does) | Medium |
| Sensors, GPS, haptics | Existing bridges (sensorfw, geoclue provider, feedbackd) | Low |
| Wi-Fi, Bluetooth | Kernel drivers + firmware already on the phone | Low |
| Suspend | Android-style wakelocks, not normal Linux suspend | Medium; battery life depends on it |

**The bridge layer itself is thin for this phone.** The 9 Pro shipped on Android 14,
so it needs the Android 14 or 16 edition of Halium. The 16 branch is one developer's
work, last touched August 2026; existing distros ship the Android 13 edition
(*sourced/reported*). Expect to contribute to it, not just consume it.

## 7. System

- **Base: Debian stable** with the project's own package archive, kept
  package-compatible with Droidian and FuriOS (*decision*). It is glibc, the bridge
  packages already exist there, and XX-WM already runs on it. postmarketOS (renamed
  Nura in September 2026) classes Android-kernel ports as a stepping stone that can be
  archived at any time (*sourced*), so it is a second target for the shell and apps,
  not the base.
- **Kernel:** Google's 6.1 Pixel kernel built from source (manifest
  `android-gs-caimito-6.1-android16`, *sourced*), kept compatible with the vendor
  modules. Note the trend: Google's driver page lists the 9 Pro for Android 14 and 15
  only, and device trees reportedly stopped at 16 (*reported*).
- **Images:** built in public CI: a root filesystem plus Android-format boot images.
- **Install:** flash the project's slot and leave working Android on the other slot
  during development (the method the Pixel 6 port uses, *sourced*). A browser-based
  installer later.
- **Updates:** A/B, reusing the Pixel bootloader's retry-and-rollback (*assumed*
  feasible, unverified on this phone).
- **Verified boot:** the project's own key in the Pixel's secure element, bootloader
  re-locked (*sourced* that Pixels support this). Two channels: locked and verified,
  or unlocked and hackable. Almost no Linux phone has verified boot; this is a
  headline feature.
- **Encryption:** LUKS with an on-screen unlock at boot first. Tying the key to the
  security chip is a later research item.
- **Android apps:** a container (Waydroid-style) after the phone works as a phone. The
  Android kernel already has what containers need. Do not promise banking apps.
- **Vendor files:** Google's factory-image and driver pages say the files "may not be
  … redistributed" (*sourced*). So: the user installs a named stock build first; the
  project ships only its own kernel and userspace and uses the vendor partition
  already on the phone. A lawyer reads the licence text before any public release.

## 8. Project

Rules drawn from Asahi, GrapheneOS and postmarketOS (*sourced* where quoted in the
notes):

1. **One device family, stated.** Pixel 9 Pro first. Others only when someone owns
   them. GrapheneOS: "Broad device support is counter to the aims."
2. **An honest status table**, per feature, with objective tiers. It is the front page.
3. **Never promise** dates, camera parity or banking apps.
4. **Calls proven before any public launch.**
5. **Upstream first.** Fixes go to Halium, libhybris, river, Mesa. Fund the Halium 16
   work; do not fork it.
6. **Shared governance written down early**, several equal voices. Asahi's lesson
   after its lead left in 2025.
7. **Money only into a project account** with a fiscal host.
8. **A name that can be trademarked** and contains no "Pixel", "Android" or
   "GrapheneOS". The owner picks; "XX-WM" stays the shell's name.
9. **Hardware in CI** as soon as there is a second phone to spare.
10. **No vendor files in the repos or images.**

Repositories: the existing monorepo keeps the shell and apps. New, separate: device
port (kernel config, boot images, bridge configuration), image builder, package
archive, installer, website with the status table.

## 9. Milestones

Each has an exit test. M3 and M5 are gates: failing one changes the product.

| | Milestone | Exit test | Where |
|---|---|---|---|
| M0 | Fix list | P0 and P1 items in `../xx-wm/docs/review-2026-11-11.md` closed | Any |
| M1 | Compositor prototype | Checklist passes on river (else sway): handset policy, keyboard resizes the app, lock survives a shell kill, monitor hot-plug, dwindle with the owner's binds | Librem 5 or laptop |
| M2 | Docked session | Section 2 "Docked" works end to end, phone screen as trackpad | Librem 5 or laptop |
| M3 | **Gate: Linux graphics on the Pixel** | Stock kernel boots to a shell; panel shows a picture through normal Linux graphics; touch works; the monitor appears. Software rendering is enough | Spare Pixel 9 |
| M4 | XX-WM on the Pixel | Handset and docked session running, unaccelerated | Pixel |
| M5 | **Gate: calls** | SIM, data, SMS, then a voice call on the owner's carrier | Pixel |
| M6 | Acceleration | Open GPU driver working; browser usable on the monitor | Pixel |
| M7 | Daily-driver hardware | Audio, camera, fingerprint, GPS, suspend with acceptable standby | Pixel |
| M8 | Public alpha | Own key, re-locked bootloader, A/B updates, installer, status table, contributor guide | — |

M0–M2 need no Pixel and start now. M3 needs the spare phone, a serial debug adapter
and a DisplayPort dock. M3 and M5 can run in parallel once there is a shell.

**If M3 fails** (no usable Linux graphics): the handset can still be built on route C
with phoc, but the docked desktop is not solved on this phone. Decide then whether to
forward-port the hwcomposer backend or wait for mainline.

**If M5 fails** (no voice): the device is a pocket Linux computer with data, and voice
is SIP. That is still new, but it is a different pitch, and rule 4 says not to launch
it as a phone.

## 10. Risks, ranked

1. Voice over LTE on the Exynos modem.
2. The Pixel display driver not behaving as normal Linux graphics, or the monitor not
   appearing.
3. The open GPU driver against a 6.1 kernel.
4. The Halium base for Android 14/16 being immature.
5. Google publishing less device code over time, for a phone supported to 2031.
6. Battery life under Android-style power management.
7. River's bus factor and toolchain churn (mitigated by the sway fallback and the
   backend layer).
8. Maintainer burnout: one person cannot carry a phone OS. Governance and scope rules
   in section 8 exist for this.

## 11. Decisions the owner still has to make

Settled since this was written:

- The OS name is **xx-os**. Apps drop the `xx-` from visible names and keep it in
  commands, ids and data directories.
- The bring-up phone is a **Pixel 9 (`tokay`)**, not the 9 Pro. Same Tensor G4 and
  modem; a separate device port. The 9 Pro stays a target.
- The carrier is **Verizon**. A 1.8 V USB serial adapter is on order for M3.

Still open:

- Whether phoc support continues for the FLX1s and Librem 5 (the design keeps it; it
  costs a backend).
- Verified-boot key custody: who holds the signing key and where.
- The key that signs app updates (review item P0.6): until it exists, "Update now"
  keeps building `main` unverified.
