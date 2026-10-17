# xx-auth — Linux Phone Port Spec

Port of the PiercingXX Android app `xx-auth` ("Offline 2FA codes" on Google
Play; Kotlin/Compose, `ref/xx-auth/`, design locks C1–C20) to Linux phones
running phoc (XX-WM or Phosh). Stack: Python 3.12+, GTK4 (+ libadwaita via the
`Adw.Application` shell only, as xx-vitals does), meson install, stdlib + GI
only, plus the `cryptography` distro package for AES-256-GCM. App id
`io.piercingxx.XXAuth`.

Visual target: `ref/xx-auth` screens (Accounts list, Add account, Account
detail, Import/export) inside the PiercingXX brand via the shared
`common/xx_ui` design system; every theme preset works live. Phone canvas
360x720, works down to 320 px wide, large text.

Public Play edition only. Anything talking to the owner's home server, the suite login
login, or log upload is dropped (see §3).

## 1. Android → Linux mapping

### M1. OTP engine (`engine/` → `base32.py`, `otp.py`, `otp_uri.py`, `migration_uri.py`)

- `Base32` → `base32.py`: case-insensitive decode ignoring padding and
  whitespace, uppercase no-pad encode, `decode_or_null` lenient variant.
  Residues 1/3/6 mod 8 rejected. Ported with `Base32Test` vectors (RFC 4648
  §10).
- `Hotp.generate` → `otp.hotp_generate`: RFC 4226 dynamic truncation,
  6–8 digits, SHA1/256/512 via `hmac`. Ported with Appendix D counters 0–9.
- `Totp.generate` / `remainingMillis` → `otp.totp_generate` /
  `remaining_millis`: counter `floor(unixSeconds / timeStep)`, ring math
  unchanged. Ported with RFC 6238 Appendix B vectors, using the CORRECT
  Appendix-B key lengths (todo C1: the Android SHA256/512 tests used the
  20-byte SHA1 secret with invented answers; the Linux tests pin the real
  32/64-byte keys and the full Appendix-B table).
- `OtpUri.parse` / `serialize` → `otp_uri.py`: case-insensitive scheme/type/
  params, `+` stays `+` in the label path, UTF-8 labels, malformed escapes
  kept literally, `issuer=` wins, stored label is the account part, errors
  never echo the URI. `MAX_PERIOD = 86400`, HOTP requires counter ≥ 0.
  Ported with `OtpUriTest` + the `ReviewPassTest` URI hardening cases.
- `MigrationUri` → `migration_uri.py`: hand-rolled protobuf field reader
  (fields 1–7), MD5 skipped and counted, batch index/size, Base64 standard or
  URL-safe. Ported with the `ReviewPassTest` migration cases (payloads built
  with `struct` in the test, mirroring the Kotlin helpers).
- `Digest` → plain `"SHA1" | "SHA256" | "SHA512"` strings validated at every
  entry point; `hmac.new(key, msg, hashlib.sha1/sha256/sha512)`.

### M2. Vault at rest (`vault/` → `store.py`, `keystore.py`)

- Room `accounts` table → `store.py`: one JSON file under
  `$XDG_DATA_HOME/xx-auth/vault.json` (dir mode 0700, created on first run),
  ordered list of account dicts, same columns (`id` UUID, `issuer`, `label`,
  `algorithm`, `digits`, `period`, HOTP `counter` or null, `secret`
  ciphertext blob, `createdAt` ms, `order`). Same DAO contract
  (insert-abort-on-duplicate, update-throws-when-absent, delete,
  delete_all, get_by_id, get_all ordered, count) plus `advance_counter`
  (HOTP only, returns null for TOTP) and `move` (C17 reorder, persist order).
- `SecretCipher` (Keystore AES-GCM) → `keystore.py`: AES-256-GCM,
  `IV ‖ ciphertext ‖ tag` blob, 12-byte random IV, 128-bit tag, via the
  `cryptography` package. The key never leaves its holder: random 32-byte
  data-encryption key kept in the Secret Service (libsecret, same
  `Secret.password_*_sync` pattern as xx-vitals `LibsecretBackend`) when a
  keyring answers, else derived with `hashlib.scrypt` (N=32768, r=8, p=1)
  from an app password the operator sets on first run. Plaintext exists only
  in memory while a code is computed, a secret revealed, or an export
  written. `Account.__str__` redacts (C20).
- `VaultSettings` → `prefs.py` JSON (`master_key_version`, schema version,
  lock settings, group filter): same single-row semantics with defaults.

### M3. Aegis import/export (`setup/VaultExporter|Importer|Scrypt|Json` → `aegis.py`, `scrypt_kdf.py`)

- `Scrypt.derive` → `hashlib.scrypt` (stdlib RFC 7914) wrapped in
  `scrypt_kdf.py`; the RFC 7914 §12 vectors are pinned in pytest, same as
  `ScryptTest`.
- `Json` (hand parser in Kotlin) → stdlib `json`.
- `VaultExporter` → `aegis.export_vault`: identical plain file
  (`version: 1`, null slots/params, `db.version: 2`, entries in `order`,
  HOTP `counter` instead of `period`) and identical encrypted file (random
  32-byte master key, GCM db with hex nonce/tag, one scrypt password slot
  N=32768/r=8/p=1, hex salt/key/nonce/tag). Ported with
  `VaultImportExportTest` (plain shape incl. no `"accounts"` envelope,
  encrypted round trip, wrong-password GCM failure).
- `VaultImporter` → `aegis.parse_vault` + `apply_vault`: accepts db versions
  1–3, skips Steam/Yandex/mOTP and malformed entries with a count, accepts a
  plain-text `otpauth://` / `otpauth-migration://` URI list (comment/`#`
  lines ignored), REPLACE (wipe after confirm, ciphertext written before the
  wipe) and MERGE (skip on case-insensitive issuer+account match, keep entry
  `uuid` when unused). `is_encrypted`, 4 MB file cap, and the plain-sentence
  `import_error_message` mapping are ported with the `ReviewPassTest` import
  cases.

### M4. Screens (`ui/` Compose → `main.py` on `xx_ui`)

- Accounts list: issuer + account rows, live Space-Mono-group code
  (`xx-display-face`), TOTP progress bar with Warn styling in the last 5 s
  (horizontal bar: the honest Linux mapping of the Compose ring), HOTP
  counter with a Next-code button. Tap copies (sensitive clip, auto-cleared
  after 30 s per this lane's spec), long-press opens the detail. Search
  filters issuer or account case-insensitively; group chips filter local
  groups. Tappable empty state → Add. An undecryptable row stays listed as
  undecryptable. Tick pauses while hidden. `FLAG_SECURE` has no Linux
  equivalent and is dropped (the compositor owns screenshots; nothing is
  written to disk).
- Add account: image-file QR scan (file picker; decode via `zbarimg` CLI,
  ZBar GI, or pyzbar — whichever is installed; camera comes later), paste
  (`otpauth://`, `otpauth-migration://` with "QR n of m", or bare Base32
  → `Secret XXXX` TOTP), manual entry (issuer, account, secret, TOTP/HOTP,
  algorithm, digits, period/counter). Same validation and the same plain
  sentences as `addErrorMessage`. Scan permission has no Linux equivalent.
- Account detail: live code + copy, HOTP Next, edit issuer/account/group,
  reveal secret and copy its `otpauth://` transfer URI behind a confirm
  (QR *display* needs a QR-encode dependency; the URI copy covers transfer
  out until then), move up/down, delete behind a confirm.
- Import/export: file pickers (`Gtk.FileDialog`, async), password + confirm
  fields, blank-password→plain warning, Add (merge) or Replace after
  confirm, count on success, wrong-password rejection. Export default name
  `xx-auth-YYYY-MM-DD.json`.
- About: version + license list via `xx_ui.widgets` license pages (SIL OFL
  fonts note kept as text; no bundled fonts — the suite theme fonts apply).
- Overflow menu: Import/export, About. FAB adds. No HeaderBar chrome:
  `PageHeader` per DESIGN.md.

### M5. Clipboard, log, QR gates (`CodeClipboard|AppLog|QrScanner` → `clipboard.py`, `applog.py`, `qr.py`)

- `CodeClipboard` (60 s) → `clipboard.py` (30 s per this lane): GDK
  clipboard copy on a worker-safe path, `GLib.timeout_add` clear that only
  clears our own clip (clear-if-ours; background reads return null and then
  clear unconditionally, same reasoning as Android).
- `AppLog.redact` → `applog.redact` verbatim (Bearer, password/passwd/
  secret/token/otp/pin values, e-mails, full `otpauth://` URIs) plus a small
  ring/dump used only for diagnostics text. No log file on disk, no crash
  uploader, no share-target: there is no log-upload in the Linux port.
- `QrScanner.otpauthPayload/accountPayload` → `qr.account_payload` gating
  pastes and file-scan results through the real parsers; junk QRs never
  reach the vault. Ported with the `ReviewPassTest` scanner cases.

### M6. Lock model (new, required by this lane)

Android has no app lock (Keystore + device lock is the lock). Linux has no
Keystore, so: with a keyring the vault key is a random 32-byte Secret
Service item and the optional app lock (password + idle relock, default
off) only gates the UI; without a keyring the app password derives the
vault key (scrypt N=32768/r=8/p=1) and the lock is mandatory. Passwords are
verified with a salted SHA-256 verifier, never stored.

## 2. Out of scope and why

- Home-server sync, home-server login, suite backup provider (`SuiteBackupProvider`,
  `AuthBackupProvider`, `Snapshot.kt`, `TarStream.kt`), theme-sync broadcast
  receiver: Play-edition-excluded by the task; the Linux port is offline-only
  (XX-WM theme comes from `xx_ui` live, no broadcast needed).
- Log upload / log-share provider (`LogDumpProvider`, `AppLog.shareText`,
  file ring): excluded by the task; `applog.py` keeps redaction only.
- Camera scanning (`QrCamera`, CameraX): deferred — image-file scan now, as
  the task allows. No camera permission flow on Linux.
- QR-code *display* on the detail screen (ZXing `QRCodeWriter`): needs a new
  QR-encode dependency; transfer out works via URI copy. Revisit when a
  distro QR-encode package is verified.
- Cloud backup rules, `FLAG_SECURE`, launcher theme sync, TalkBack semantics:
  Android-only APIs with no Linux counterpart (a11y comes from GTK/AT-SPI).
- Aegis `groups[]` links: the Android exporter always writes `"groups": []`;
  Linux keeps per-account groups local, exports their names as a valid
  Aegis `groups` array, and ignores group links on import. Same fidelity,
  no invented format.
- PIN/biometric app lock: out of scope on Android too (todo); password lock
  per this lane's spec only.

## 3. Dropped Android code (Play edition)

`suite/backup/` (3 files), `backup/AuthBackupProvider.kt`,
`log/LogDumpProvider.kt` + `AppLog` init/crash/file/share paths,
`setup/QrCamera.kt`, `ui/theme/` launcher-sync, manifest permissions and
backup rules, `about/OssLicenses.kt` (replaced by `xx_ui` license pages).
Gate equivalent: the source tree contains no network imports (pinned by
`test_guards.py`) and the package has no network dependency.

## 4. Verification

- pytest: JVM tests translated (`test_base32/otp/otp_uri/migration_uri/
  scrypt/aegis/accounts/hardening`), plus store/clipboard/QR/identity/
  guards/broadway-smoke tests. No network, no real accounts, private
  `dbus-daemon` bus for service tests.
- `sh scripts/check-all.sh xx-auth` prints ALL PASSED.
- `tools/snap_all.sh` renders list/add/detail/import/lock/empty in amoled +
  paper (graphite/aura spot checks), 360x720 and 320-wide, zero
  `snap: WARNING`; sheets in `xx-auth/lane_shots/`.
