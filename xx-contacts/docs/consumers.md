# Consumer contract: using Contacts from other apps

For the xx-dialer, TxxT, email, and shell lanes. xx-contacts owns the
address book (`~/.local/share/xx-contacts/contacts.db`) and every
other app reaches it through the session-bus API below or the CLI
entry points. There is no shared file to read and no second store to
reconcile: EDS is a one-way mirror for GNOME apps, not a source.

## D-Bus API (`io.piercingxx.Contacts1`)

Bus `io.piercingxx.Contacts`, path `/io/piercingxx/Contacts`. Writes
go through the service so it stays the single writer and its `Changed`
signal reaches every sender-filtered subscriber; the GUI itself writes
this way too. Call shapes (tuple replies — `reply.unpack()[0]` first):

- `LookupNumber(s number) -> (s uid, s display_name, as tags)`:
  caller ID, ring policy, SMS headers. Miss = `("", "", [])`.
- `Search(s query, u limit) -> a(sss)`: `(uid, name, primary number)`
  for dialer suggestions and contact pickers. Async only.
- `Get(s uid) -> s vcard`: full vCard 4.0 for one contact.
- `List(t since) -> (t revision, as changed, as deleted)`: polling
  for incremental caches.
- `Create(s vcard) -> s uid`, `Update(s uid, s vcard)`, `Delete(s uid)`:
  programmatic writes (imports, sync). Photos ride inside the vCard
  `PHOTO` data-URI line; removal is a `PHOTO`-less `Update`.
- `SetTags(s uid, as tags)`, `UpdateTags(s uid, as add, as remove)`:
  atomic star/business/blocked switches; unknown tags are rejected.
- `Pick(s kind) -> (s uid, s value)`: modal user pick, `kind` ∈
  `{phone, email, contact}`. Cancel = `("", "")`. Holds the call open
  for human time — call with a 5-minute budget (`PICK_TIMEOUT_MS` in
  `service_client`, with `pick_sync`/`pick_async` helpers) and never
  from a UI thread without the async form. The headless service
  launches `xx-contacts --pick KIND --reply-to TOKEN` and the GUI
  reports back over `PickReply(s token, s uid, s value)` (GUI-side
  `reply_pick_sync`/`reply_pick_async` helpers); no GTK runs in the
  service process. A second concurrent pick fails, an unknown kind
  fails, and a GUI that cannot start fails — the `Pick` contract
  itself is unchanged.
- Signal `Changed(t revision, as uids)`, sender-filtered on
  `io.piercingxx.Contacts`: invalidate per-uid caches, or re-`List`.

First use auto-activates the service; after 60 s idle it exits and the
next call restarts it. Any failure (missing name, timeout, error)
means "contacts unavailable": fall back to the raw number / cached
data, never to a second store.

## "Add to contacts" from Recents and SMS

Today the dialer's Recents writes silently through `Create`. The
better path is the editor, prefilled, so the user reviews before
anything is saved:

```sh
xx-contacts --new --tel +15550100
```

`--tel` implies `--new`; the number lands in the first phone row and
Cancel stays silent (the prefill counts as the clean state). If the
app is already running, the launch forwards to it and the editor opens
on top. Keep `Create` for flows with no user in the loop (imports).

## Files and URIs

- `.vcf` files (`text/vcard`, `text/x-vcard`) opened from Files land
  on the import confirm page: dry-run merge plan first, one `Import`
  call only after the user confirms. Other apps hand vCards over with
  `xdg-open file.vcf`. The app never writes on open.
- `tel:` links on the detail page open the default call handler
  (`Gtk.UriLauncher`, i.e. GNOME Calls on the phone) — outbound
  `tel:`/`sms:`/`mailto:`/`geo:` URIs are owned by the
  dialer/SMS/mail/maps handlers, never by Contacts.
