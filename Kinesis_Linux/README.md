# Kinesis Linux — experimental CLI port

This is a Python translation of the MIT-licensed protocol implementation in
[callbacked/kinesis](https://github.com/callbacked/kinesis), inspected at commit
`5c78485f48c0758e2ce46ee11d3aaa070d827b75` (2026-11-08).
It is intended for Linux computers and Raspberry Pi OS with a Bluetooth LE adapter.

**Status: protocol tests pass; no real band, Linux Bluetooth adapter, Meta account
login, or desktop input device was available for end-to-end validation. Treat this
as a prototype to test, not a verified working Linux release.**

## What is included

- BlueZ/Bleak scan, pairing, GATT PSM read, and kernel LE L2CAP CoC transport.
- P-256 ECDH, AirShield parameter-3 HKDF, AES-CBC, per-record HMAC verification.
- Existing-owner authentication using an identity record, plus one-time Meta
  login and ownership enrollment translated from upstream.
- JSON lines for recognized gestures, gyro, quaternion, handedness, and raw EMG.
- Pinch-and-turn volume dial with confirmed wrist side, hold delay, stale-motion
  release, and rate limiting.
- Optional Linux virtual keyboard controls through `evdev`/`uinput`.
- Stream shutdown on the original subscription and a three-second ack window.
- Owner identity/session files with permissions 0600 inside a 0700 state directory.

This CLI does not reproduce the macOS settings UI, hand animation, cursor mode,
handwriting experiments, model capture, or automatic reconnect. Battery UI and
handedness writes are not included. It reads the band's current handedness.

## Install on Raspberry Pi OS / Debian / Ubuntu

Use a normal desktop user. Python 3.11+ is supported; ctypes supplies the Linux
LE socket address fields missing from CPython before 3.14. The kernel must support
Bluetooth LE credit-based L2CAP sockets and `BT_MODE_LE_FLOWCTL`.

```bash
sudo apt update
sudo apt install -y bluez python3-venv python3-gi gir1.2-webkit2-4.1
cd Kinesis_Linux
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install .
kinesis-linux --help
```

The optional Meta login window needs a graphical desktop and WebKitGTK 4.1. Use
the system Python with `--system-site-packages` so it can import distro `gi`.
Headless operation works after enrollment or with your own existing identity.

If copying this folder into the original Git checkout, it belongs at `linux/`;
run the installation commands from that directory instead.

## First scan and pair

Disconnect the glasses/phone/other Kinesis processes from the band. Put it in
pairing mode. In a separate terminal, leave a Bluetooth pairing agent running:

```text
bluetoothctl
power on
agent on
default-agent
```

Accept pairing requests for your band when prompted. Then run:

```bash
kinesis-linux scan
```

Use the returned Linux Bluetooth MAC in the following commands. A macOS
CoreBluetooth UUID is not a Linux MAC. With multiple adapters, add `--adapter hci1`
(or the appropriate adapter name) to scan and connection commands.

## Enroll this Linux computer

```bash
kinesis-linux login
kinesis-linux enroll --address AA:BB:CC:DD:EE:FF
kinesis-linux listen --address AA:BB:CC:DD:EE:FF
```

Replace the example MAC. Login opens Meta's own HTTPS sign-in page and validates
its callback before exchanging the login blob. Credentials are entered on Meta's
page; the program stores the returned account token, not the password. Network
calls happen only when you explicitly run login/enroll. Ordinary `listen` uses
Bluetooth and the stored owner identity, without Meta HTTP requests.

Enrollment claims the band using your Meta account and a fresh Linux owner key.
It changes the owner's application identity, so an existing phone/Mac setup may
need enrollment again. Preserve a working setup until you are ready to test this.
A band belonging to another account must be handled using that account or the
upstream reset procedure. This implementation does not bypass ownership checks.

Meta's private endpoints, callback format, and embedded-WebKit compatibility are
unverified here and may change. A successful sign-in is not evidence that BLE
transport or enrollment will succeed. Error 0x03001043 means the app identity does
not match the enrolled owner; 0x03001042 indicates the wrong account.

If Meta omits the band's public key, this follows upstream's fallback: it checks
that the band accepts the app's identity and authenticates AirShield records, but
cannot verify the band's long-term identity proof. A warning event reports this.
A provided public key is always verified; a bad signature aborts the connection.

## Use an existing owner identity

If you already have your own compatible owner identity JSON:

```bash
chmod 600 /path/to/band-identity-record.json
kinesis-linux listen --address AA:BB:CC:DD:EE:FF \
  --identity /path/to/band-identity-record.json
```

The format follows upstream: base64 `AppPrivateKey` (32 bytes), `AppPublicKey`
(64-byte P-256 point), and optional band key `AppECPubicKey` or `AppECPublicKey`
(64-byte point). Private/public agreement is checked. An arbitrary generated key
cannot authenticate a previously owned band. This package does not extract keys
from another device.

## Collect data for a Pi application

```bash
kinesis-linux listen --address AA:BB:CC:DD:EE:FF --raw-emg > band-events.jsonl
```

Stdout contains JSON only; stderr contains diagnostics. With the observed
configuration, an `emg_batch` event has `samples` shaped **16 samples × 8 channels**,
2048 samples/second/channel, unsigned 16-bit ADC values. Data is interleaved by
sample, then channel on the wire. `missing_before` reports sequence gaps. Unknown
configurations remain opaque `raw_emg_payload` events. Voltage scale and physical
electrode order have not been established. Concurrent raw EMG + motion may expose
throughput problems; test with gestures alone first.

A typical decoded event looks like:

```json
{"event":"gesture","finger":"index","action":"tap","derived_action":"singleTap","synthetic":false,"sequence":1,"timestamp_us":1000000,"received_at":42.0,"mapping":"index_tap"}
```

The band supplies already-recognized gestures; this program is not training an
EMG classifier. Use `emg_batch` as an input stream if you want your own classifier.
No motor or orthosis actuation is implemented.

## Optional desktop controls

Install the extra dependency and ensure your desktop user's account has read/write
access to `/dev/uinput` under your distribution's policy:

```bash
python -m pip install evdev
sudo modprobe uinput
kinesis-linux listen --address AA:BB:CC:DD:EE:FF --controls
```

`--controls` is opt-in; without it gestures are printed only. The virtual keyboard
works through the Linux input subsystem; the actual shortcut behavior depends on
your desktop/compositor. Do not run the Meta login window as root. Configure a
narrow uinput device access rule for your desktop user if permission is denied;
this package does not change system device permissions.

Default mappings:

| Gesture | Key output |
| --- | --- |
| Thumb swipe left/right | Ctrl + Alt + Left/Right |
| Thumb swipe up/down | Super / Escape |
| Index tap / double tap | Play-pause / Mute |
| Middle tap / double tap | Next / Previous track |
| Index pinch + turn | Volume up/down |

`mapping.example.json` contains every default. Copy it, edit the arrays of Linux
`KEY_*` names, and pass `--mapping your-mapping.json`. Omitted mappings do nothing.
Use `KEY_BRIGHTNESSUP`/`KEY_BRIGHTNESSDOWN` for dial brightness instead of volume.
Raw and derived representations of one gesture are deduplicated; synthetic
gestures never dispatch key actions.

## Troubleshooting and first hardware test

1. Verify `bluetoothctl list` and `bluetoothctl show`, then run `scan`.
2. Complete OS pairing with the pairing agent open. If the band was reset, remove
   its stale entry using `bluetoothctl remove <MAC>` and scan/pair again. Only remove
   the entry for the band you intend to reconnect.
3. Start `listen` without `--raw-emg` or `--controls`. Look for `connected`, then
   `handedness`, gesture, and gyro events. An owned band without its matching key
   can reject the legacy pairing-mode path; use enrollment or the correct record.
4. If startup fails at L2CAP, capture `sudo btmon` output in a separate terminal
   and note `uname -a`, `bluetoothctl --version`, and the reported failure stage.
   Kernel/BlueZ/firmware compatibility remains the largest hardware unknown.
5. Add `--raw-emg` only after gesture streaming works; check sample counts and
   sequence gaps. Add `--controls` last.
6. Stop with Ctrl+C. On link loss, shutdown cannot guarantee stream disable. Run
   the command again to reconnect; queued gestures are never replayed.

State is under `$XDG_STATE_HOME/kinesis-linux` or `~/.local/state/kinesis-linux`.
Deleting those private files only forgets local credentials; it does not reset
server-side band ownership. Do not share this directory, identity JSON, Meta
session, or captures containing private identifiers.

## Tests and source map

```bash
python -m unittest discover -s tests -v
```

Tests cover upstream fixed crypto vectors, counter wrap, bytewise fragmentation,
MAC rejection without cipher advancement, both trust-proof orders, owner rejection,
enrollment receipts, stop acknowledgement, raw EMG layout/gaps/unknown encoding,
callback validation, identity import, gesture deduplication, private-file
permissions, native socket address layout, and dial freshness/left-hand reversal.
They do not establish compatibility with physical hardware or Meta's servers.

| Upstream implementation | Linux module |
| --- | --- |
| AirShield.swift, BandWire.swift | `wire.py` |
| BandSession.swift, BandIdentity.swift, OwnershipCeremony.swift | `session.py` |
| CoreBluetooth portion of BandConnection.swift | `transport.py` |
| MetaAuth.swift, MetaLoginView.swift, MetaPairClient.swift | `auth.py` |
| GestureRouter, PinchDial, DialRouter, MacShortcuts | `controls.py` |
| Application orchestration and persistence | `cli.py` |

The author’s earlier `neural-band-poc` repository was read for context; its Python
files are not redistributed. This port is translated from the MIT Kinesis source.
See `REVERSE_ENGINEERING.md` for the protocol breakdown and platform changes.
