# xx-auth

Offline TOTP/HOTP two-factor authenticator for Linux phones. Linux port of
the PiercingXX Android xx-auth app ([PORT_SPEC.md](PORT_SPEC.md)). Secrets
stay encrypted on-device; codes die on their own. No network, no account.

Scan a QR image, paste a URI or bare secret, or type the fields by hand.
The vault leaves the phone only as an Aegis vault file you chose to write,
plain or password-encrypted (scrypt + AES-256-GCM, per the Aegis vault
format), and comes back the same way.

```sh
cd xx-auth && meson setup build --prefix=/usr && sudo meson install -C build
sh tools/snap_all.sh lane_shots
```

## Security model

- The vault file (`~/.local/share/xx-auth/vault.json`) holds ciphertext
  only: AES-256-GCM, `IV ‖ ciphertext ‖ tag`, via the `cryptography`
  package. Plaintext exists only in memory while a code is computed.
- The 32-byte vault key lives in the Secret Service (libsecret) when a
  keyring answers; otherwise it is derived with scrypt (N=32768, r=8, p=1)
  from an app password set on first run, and the app lock is mandatory.
- Optional app lock (password + idle relock, default 120 s). Clipboard
  copies auto-clear after 30 s. Nothing secret is ever logged
  (`applog.redact` guards every diagnostic line).

## QR scanning

Image-file scan decodes with the first backend found: `zbarimg`
(zbar-tools), ZBar GI, or pyzbar. The camera comes later. Whatever the
backend, payloads are gated through the real `otpauth://` /
`otpauth-migration://` parsers — junk QRs never reach the vault.

## Develop

```sh
PYTHONPATH=src:../common python3 -m xx_auth
sh ../scripts/check-all.sh xx-auth
```
