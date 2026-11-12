# Reverse-engineering notes and Linux port design

Source: callbacked/kinesis commit 5c78485f48c0758e2ce46ee11d3aaa070d827b75.
The code is already public under MIT; the work here is protocol extraction and
translation, rather than binary reverse engineering or a claim to have independently
recovered Meta's protocol.

## Platform changes

| macOS dependency | Linux replacement |
| --- | --- |
| CoreBluetooth discovery and GATT | Bleak over BlueZ D-Bus |
| CoreBluetooth L2CAP streams | AF_BLUETOOTH / SOCK_SEQPACKET / BTPROTO_L2CAP |
| CryptoKit + CommonCrypto | Python cryptography (OpenSSL backend) |
| Keychain identity/session entries | Atomic 0600 JSON files in a 0700 XDG state directory |
| WKWebView Meta sign-in | Ephemeral WebKitGTK 4.1 window |
| CGEvent / IOKit desktop events | Optional evdev UInput virtual keyboard |
| SwiftUI menu bar and settings | Command-line interface and JSON lines |

The file-based credential store is permission protected, not an encrypted secret
service/keyring. It is an explicit difference from upstream's Keychain storage.

## Bluetooth path

1. Scan advertisements named `Meta Band…` using the selected adapter.
2. Connect and pair through BlueZ; retain this GATT connection for the session.
3. Read characteristic `2d41da7c-82b6-42aa-b34e-e2e01df8cc1a` in service FEB8.
   The observed little-endian bytes `ff 00` identify PSM 255. Other values are
   rejected rather than assumed to have the same protocol.
4. Read Device1.AddressType and the adapter's address from D-Bus. This matters
   for LE random addresses and identity address changes after bonding.
5. Bind an LE socket to the adapter, set medium link security and mode 3
   (`BT_MODE_LE_FLOWCTL`), then connect to the remote LE address and PSM.
6. Feed received SDUs into the incremental AirShield decoder. Chunk outgoing
   bytes into SDUs no larger than the negotiated peer MTU (capped at 128 bytes).
   Fragmentation across these SDUs is a hardware compatibility assumption that
   still needs an actual band test; the upstream transports provide byte streams.

Linux's `sockaddr_l2` carries family, little-endian PSM, reversed Bluetooth address
bytes, CID, and address type. Its native padded size is 14 bytes on the targeted
Linux ABI. ctypes passes this structure to libc bind/connect so Python 3.11–3.13
can use LE CoC despite their limited Python-level L2CAP address tuples.

References:
- https://github.com/bluez/bluez/blob/master/doc/l2cap-protocol.rst
- https://github.com/python/cpython/issues/129288
- BlueZ tools/l2test.c and lib/bluetooth/bluetooth.h (inspected locally).

## Transport encryption

Host opens DataX channel 0x8001 with service 0x05 and RequestEncryption
0x02000001. Both sides use fresh P-256 ephemeral keys. Only the observed peer
parameter 3 is accepted. With shared secret Z, receiver challenge C, sender seed S:

```
H = SHA256(Z)
K = HKDF-SHA256(ikm=H, salt=SHA256(H || C || S), info="AirShield", length=32)
```

Each direction has independent key, CBC IV, and uint32 packet counter. The same
32-byte directional K is used for AES-256-CBC and HMAC-SHA256, matching upstream.
The authenticated wire record is:

```
marker 0x40 | MAC[0:8] | block_count_minus_one[1] | ciphertext[16*n]
MAC = HMAC-SHA256(K, counter_le32 || block_count_minus_one || ciphertext)
```

CBC IV advances to the last ciphertext block and the counter wraps modulo 2^32.
MAC verification occurs before decrypting or advancing either state. Plaintext
padding is the observed repeated 0xc0+N suffix for N=1…15, not PKCS#7. DataX frames
can span decrypted records, so padding removal precedes incremental reassembly.
Relay channels 1/2 are discarded and never passed to the trusted input decoder.

## Owner authentication and enrollment

AirShield transport encryption is separate from ownership. An enrolled band
requires an application identity previously authorized through Meta. The host
signs the digest below with its stored P-256 signing key:

```
D = SHA256(SHA256(receiver_challenge || receiver_ephemeral_public64)
        || SHA256(sender_seed || sender_ephemeral_public64))
```

ECDSA operates on this digest using Prehashed SHA256; signatures are raw r||s,
64 bytes. Using ordinary ECDSA(SHA256) on D would accidentally hash twice.
EnableTrust (service 0x24, type 0x02001000) carries the SHA256 of the application's
identity public point and this signature. A stored band identity key verifies the
reverse EnableTrustEC proof. The band acceptance and proof can arrive in either
order; EndLinkSetup waits for both. Wrong known-band signatures fail the session.
When upstream enrollment omits the device identity key, this port reports the same
unverified-band fallback as upstream and still waits for band acceptance.

For fresh enrollment: identity read 0x3000/0x3001 → nonce 0x2000/0x2001 →
Meta pair_request → StartChangeOwner 0x2002/0x2003 → Meta pair →
FinishChangeOwner 0x2004/0x2005 → store new identity → run trust exchange.
The server's signed receipt strings are transmitted byte-for-byte without JSON
reformatting. This requires the signed-in owner's account; no ownership bypass
is implemented. Public application identifiers are copied from the MIT source;
no personal credentials or captured keys are bundled.

## Input services

After EndLinkSetup acknowledgement, query device info on channel 0x8003. On its
successful response, open subscription channel 0x8005, query status (request 2),
enable gestures/gyro/quaternion (request 3), and read handedness on 0x8006.

| Value | Meaning |
| --- | --- |
| 0x8100ce56 | Input service open word |
| 0x02000314 / 0x02000315 | RPC request / response |
| Control field 3 / 6 / 8 / 2 | Gestures / gyro / quaternion / raw EMG |
| Config field 10 | is_left_handed (0 right, 1 left) |
| Config field 42 | EMG configuration |
| 0x0200020d | Recognized gesture |
| 0x0200020f | Gyro, three little-endian int16 values |
| 0x02000212 | Quaternion, four little-endian floats (w,x,y,z) |
| 0x0200020a | Raw EMG sensor payload |

Raw EMG startup reads configuration on 0x8007, queries the existing subscription,
then updates that same subscription to keep motion/gestures on and enable field 2.
The observed configuration is 2048 Hz × 8 channels × 16 bits, 16 samples per batch,
encoding 0. Only this layout becomes numeric sample arrays. Unknown layouts remain
opaque payloads. No voltage conversion or electrode-position claim is made.

Quiet streams trigger status queries every two seconds; ten seconds without an
authenticated reply fails the connection. Shutdown sends request 4 with every
requested stream disabled, on 0x8005, and waits up to three seconds for its ack.
There is no automatic reconnect or replay queue in this version.

## Validation limits

The tests use independently generated synthetic band peers and the original
project's published fixed cipher vectors. This checks encoding, sequencing, and
crypto agreement with those references. It cannot demonstrate real LE CoC
interoperability, firmware compatibility, pairing-agent behavior, Meta account
sign-in, desktop key permissions, latency, or a sustained 2048-Hz capture rate.
