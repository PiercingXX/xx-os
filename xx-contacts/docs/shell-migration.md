# Shell migration: `contacts.py` → Contacts1 D-Bus API

For the XX-WM shell lane. This is the exact contract for replacing the
shell's inline contact lookups with the xx-contacts service, so caller
names keep working after the address book moves into
`~/.local/share/xx-contacts/contacts.db`.

## Endpoint

| Item | Value |
|---|---|
| Bus name | `io.piercingxx.Contacts` |
| Object path | `/io/piercingxx/Contacts` |
| Interface | `io.piercingxx.Contacts1` |
| Introspection | `src/xx_contacts/service.py` `INTROSPECTION_XML` |

Gio rules that have bitten before: replies are tuples, so
`reply.unpack()[0]` is the first out-arg; `reply_type` must be a
`GLib.VariantType`, never a string; async `connection.call(...)`
callbacks take exactly `(conn, result)` — do not pass `user_data`.

## API surface the shell needs

`LookupNumber(s number) -> (s uid, s display_name, as tags)`
: Caller-ID name for a raw number. Answered in < 5 ms from an
  in-memory digits-suffix index (same tail-7 semantics as the current
  `ContactBook.lookup_number`). A miss returns `("", "", [])` —
  never an error. `tags` ⊆ `{starred, business, blocked}`.

`Search(s query, u limit) -> a(sss)`
: `(uid, display_name, primary_number)` rows for name/number/email
  input; number hits rank first. The service caps `limit` at 500; the
  dialer passes 4, the Medical ID picker 8.

`List(t since_revision) -> (t revision, as changed_uids, as deleted_uids)`
: Incremental sync for caches. Consumers that only need invalidation
  can ignore it and re-query on `Changed`.

`Pick(s kind) -> (s uid, s value)`
: Blocking modal picker, `kind` ∈ `{phone, email, contact}`.
  Phone/email return the chosen number/address in `value`; contact
  returns the display name. A cancel (or closed window) returns
  `("", "")`. Unknown kinds and a second concurrent pick fail with
  `InvalidArgument`; a pick GUI that cannot start fails with
  `NotImplemented`. Prefer `xx-contacts --new --tel NUMBER` for
  "Add to contacts" (next section) — `Pick` holds the caller's D-Bus
  call open for human-scale time.

The service itself never opens a window: on `Pick` it spawns
`xx-contacts --pick KIND --reply-to TOKEN [--db PATH]` and the GUI
sends the choice back over `PickReply(s token, s uid, s value)`
(`reply_pick_sync` for worker threads, `reply_pick_async` on the GTK
main thread), which settles the held call. The reply token travels
only in the GUI command line, never in a log. If the app is already
running, the `--pick` launch forwards to it through the normal
`do_command_line` path and the pick opens on top.

Signal `Changed(t revision, as uids)`
: Emitted only by the service, on its own connection, after every
  write. Always subscribe with `sender="io.piercingxx.Contacts"`:
  a GUI re-emit from another unique name will not — and must not —
  reach the filter.

## Activation and lifecycle

The service is D-Bus activatable
(`share/dbus-1/services/io.piercingxx.Contacts.service`,
`Exec=xx-contacts --service`, no systemd unit): the first method call
starts it, so clients never launch it themselves. It exits after 60 s
without a call and the next call re-activates it transparently; a cold
call can therefore take ~1 s the first time. `XX_CONTACTS_IDLE_SECONDS`
overrides the idle exit (tests use it). The GUI (`io.piercingxx.XXContacts`)
never owns this name — if it did, the app would treat itself as a
duplicate instance and exit.

## Fail-open rules

Caller ID and SMS summaries must render even when xx-contacts is not
installed, so every call site keeps the current `ContactBook`
(JSON + VCF tail matcher) as the offline fallback and degrades
`LookupNumber → ContactBook → raw number`:

- Never `call_sync` on the GTK main thread. Either check
  `bus_name_has_owner("io.piercingxx.Contacts")` (500 ms default, same
  probe `modem_monitor` uses for Calls/Chatty/TxxT) before routing to
  the app, or make the async call with a 5–8 s timeout and fall back
  on any error, timeout, or empty display name.
- Never raise to the UI. A failed lookup is a missing name, not an error.
- Cache the last answer per number for the hot paths (incoming-call
  label, SMS banner): `LookupNumber` is fast, but activation + a bus
  round-trip on every ring is still worth one dict entry.
- `settings_medical.py` (`search_contacts`, `parse_search_results`)
  is the reference async `Search` consumer: tuple-unpack the reply,
  defensively parse `a(sss)` rows, degrade to failure without raising.

## Caller-by-caller migration

| Current call site | Replacement |
|---|---|
| `contacts.ContactBook` (`_LOCAL_JSON` + VCF scan, tail-7) | Keep as the offline fallback only. Primary source becomes `LookupNumber`. |
| `call_ui.display_name(number)` (incoming-call label) | Async `LookupNumber`; cache per call; fallback `ContactBook().lookup_number(number) or number`. |
| `call_wiring._on_sms_received` banner summary | Same `LookupNumber` + cache; fallback unchanged. TxxT/Chatty ownership checks stay first. |
| `modem_monitor._display_name(number)` (call/SMS log names) | Same `LookupNumber` + cache; this path must stay fast, so never call it cold — warm the cache on `Changed`. |
| `sms.Conversation.open_conversation` header | Caller-supplied name wins, then `LookupNumber`, then the raw number (same precedence as today). |
| `dialer` suggestions (`ContactBook().search(digits, limit=4)`) | Async `Search(digits, 4)`; rows already carry `(uid, name, number` |
...[truncated 2086 chars]