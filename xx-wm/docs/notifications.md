# Notifications

The shell owns `org.freedesktop.Notifications` on the session bus
(`launcher/src/notif_daemon.py`, in-process daemon). When another daemon
(dunst, mako, fnott) already owns the name, the shell backs off and the
shade reads through its fallback subscription path instead.

Capabilities advertised: `body`, `body-markup`, `actions`, `persistence`.

Every `Notify` reaches the shade with its id, app name, summary, body,
desktop entry, raw hints dict, and decoded `[(key, label), ...]` action
pairs. Tapping an action emits `ActionInvoked` on the bus as usual; the
shade then dismisses the notification (resident rows stay).

## Muting

`muted_apps: {app_id: until_epoch}` in the shell config discards an
app's notifications for 1/2/4/8 hours (see "Disable for…" in
`../design.md`): no banner, no sound, nothing in the shade. Muting also
clears the app's live notifications (`NotificationClosed` reason 3, as if
the app closed them itself). The mute expires silently.

## Action targets: reaching an exited service

Some actions belong to a background service that has already exited by
the time the user taps: the dialer policy service posts Call back /
Message / Ring next time / Block on its missed-call cards and then
idle-exits,
and the TxxT daemon posts Reply / Open the same way. A plain
`ActionInvoked` signal would go nowhere, because signals do not
auto-start services.

Such a notification carries the hint `x-xx-wm-action-target` (string):

```
"<bus_name> <object_path> <interface>"
```

When the user invokes an action on a notification with a well-formed
hint, the shell emits `ActionInvoked` as usual **and** calls

```
<interface>.HandleNotificationAction(in u id, in s action_key)
```

on that target with auto-start allowed (async, 5 s timeout,
fire-and-forget, DEBUG log on failure), so the bus daemon starts the
service and it acts. A missing or malformed hint is ignored: the three
parts must be a well-formed bus name (well-known or unique),
object path, and interface, e.g.

```
x-xx-wm-action-target:
  "io.piercingxx.XXDialer.Policy /io/piercingxx/XXDialer/Policy io.piercingxx.XXDialer.Policy1"
```

with actions `call-back` / `ring-next-time` / `block-contact`, plus
`message` on missed-call cards (single cards for known callers carry
`call-back` / `message` / `block-contact`, adding `ring-next-time` for
unknown callers; grouped cards carry no actions). The
service implements `HandleNotificationAction` with the same `(u, s)`
signature and dispatches on the action key it posted.

## Posted-id guard

`HandleNotificationAction` must ignore notification ids the service
did not post: anything else is a silent no-op, so a replayed or
foreign id can never trigger an unrelated action. Every adopter
tracks the ids it posted, bounded (64 entries), and persisted when
the service can exit while its cards stay live:

- xx-dialer: in-memory set plus the `posted_notif` store table, which
  also survives the policy service's idle-exits.
- TxxT: bounded in-memory map (the daemon runs for the whole
  session; server ids die with the shell daemon on reboot, so
  persisting buys nothing).
- xx-email: the `notif_actions` store table, written on each Notify
  reply, read back by the activatable actions service below.

## xx-email action target

The oneshot poller exits while its cards stay live, so every mail
card carries

```
x-xx-wm-action-target:
  "io.piercingxx.XXEmail.Daemon /io/piercingxx/XXEmail/Daemon io.piercingxx.XXEmail.Daemon1"
```

with actions `default` (Open) / `archive` / `mark-read`. Taps reach
`io.piercingxx.XXEmail.Daemon1.HandleNotificationAction(u, s)` on the
activatable `io.piercingxx.XXEmail.Daemon` service (`xx-email
--actions-service`), which routes posted ids through the table above
and idle-exits.
