# XX-WM — host tools

## `xxdb` — wireless debugging host CLI

Pair a computer with the phone over Wi-Fi (the Android-style "wireless
debugging" flow: a code shown on the phone, then shell, logs,
screenshots, file transfer, app deploy, and input injection). Stdlib
Python only; needs `openssl` once to create the host certificate.

On the phone: Settings page, Developer section, "Wireless debugging"
switch. Then:

```bash
# Pair: "Pair with code" shows HOST:PORT, a 6-digit code and the phone's
# fingerprint. xxdb refuses to pair if the fingerprint it sees differs.
./tools/xxdb pair 192.168.1.20:37234 482916 1a2b 3c4d 5e6f 7a8b

# Verify / list
./tools/xxdb devices
./tools/xxdb connect 192.168.1.20

# Shell + logs + screen
./tools/xxdb shell --to 192.168.1.20 -- echo hello
./tools/xxdb shell --to 192.168.1.20        # interactive
./tools/xxdb logcat 192.168.1.20 --lines 100
./tools/xxdb screenshot 192.168.1.20 shot.png

# Files ($HOME on the phone unless --system, which the phone user confirms)
./tools/xxdb push 192.168.1.20 local.txt notes/remote.txt
./tools/xxdb pull 192.168.1.20 .local/share/xx-wm/shell.log shell.log

# Deploy a launcher tree + restart the shell (replaces scripts/deploy.sh's
# ssh path once paired)
./tools/xxdb deploy 192.168.1.20 [--src launcher/src]

# Shell IPC + input injection
./tools/xxdb ipc 192.168.1.20 shade.show
./tools/xxdb input 192.168.1.20 tap 180 400
./tools/xxdb input 192.168.1.20 text "hello"
```

Host state lives in `~/.config/xxdb/` (`key.pem`, `cert.pem`,
`phones.json` with the pinned phone fingerprints; all 0600). Discovery
uses `avahi-browse` when available and falls back to known hosts.
