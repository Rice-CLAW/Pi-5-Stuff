"""BandSession/OwnershipCeremony translation; HTTP is delegated to the caller."""
import base64
import math
import os
import struct
import time
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from .wire import CipherState, DataX, Receiver, derive, field, fields, frame, need, parse_frame, sha


def point(key):
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)[1:]


def digest(challenge, receiver, seed, sender):
    return sha(sha(challenge + receiver) + sha(seed + sender))


def sign(key, value):
    r, s = utils.decode_dss_signature(key.sign(value, ec.ECDSA(utils.Prehashed(hashes.SHA256()))))
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def verify(key, signature, value):
    if len(signature) != 64:
        raise ValueError("invalid identity signature length")
    der = utils.encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
    key.verify(der, value, ec.ECDSA(utils.Prehashed(hashes.SHA256())))


def identity_from_record(record):
    raw = base64.b64decode(record["AppPrivateKey"], validate=True)
    if len(raw) != 32:
        raise ValueError("identity private key must contain 32 bytes")
    private = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    if point(private.public_key()) != base64.b64decode(record["AppPublicKey"], validate=True):
        raise ValueError("identity public/private keys disagree")
    raw_band = record.get("AppECPubicKey") or record.get("AppECPublicKey")
    band = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + base64.b64decode(raw_band, validate=True)) if raw_band else None
    return private, band


def identity_record(private, band):
    b64 = lambda x: base64.b64encode(x).decode()
    return {"AppPrivateKey": b64(private.private_numbers().private_value.to_bytes(32, "big")),
            "AppPublicKey": b64(point(private.public_key())), "AppECPublicKey": b64(point(band)) if band else None}


class Session:
    def __init__(self, emit, identity=None, enroll=False, raw=False, clock=time.monotonic):
        if enroll and identity:
            raise ValueError("choose enrollment or an existing identity")
        self.emit, self.clock, self.identity = emit, clock, identity
        self.enroll, self.raw = enroll, raw
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.public = point(self.private.public_key())
        self.challenge, self.seed, self.iv = os.urandom(16), os.urandom(32), os.urandom(16)
        self.base = int.from_bytes(os.urandom(4), "little")
        self.pending, self.datax, self.types = bytearray(), DataX(), {}
        self.rx = self.tx = self.peer = self.peer_challenge = self.peer_seed = None
        self.app_trusted = self.band_trusted = self.end_sent = False
        self.stage = "enroll_identity" if enroll else ("trust" if identity else "link")
        self.enroll_key = ec.generate_private_key(ec.SECP256R1()) if enroll else None
        self.certificate = self.serial = self.secondary = self.band_key = None
        self.streaming = self.stopping = self.stop_ack = False
        self.hand = self.emg_config = None
        self.last_authenticated = clock()
        self.last_sequence = None
        self.stream_fields = (3, 6, 8)  # raw enabled separately after reading its config
        self.authenticated = 0

    def request(self):
        return frame(0x8001, (0x81000005, 0x02000001), field(1, self.public) + field(2, self.challenge) + field(3, 0) + field(4, 31) + field(7, 16))

    def encrypted(self, channel, words=(), payload=b""):
        return self.tx.encrypt(frame(channel, words, payload))

    def trust(self, opened=False):
        private, _ = self.identity
        signature = sign(private, digest(self.peer_challenge, self.peer, self.seed, self.public))
        return self.encrypted(0x8002, (0x02001000,) if opened else (0x81000024, 0x02001000),
                              field(1, sha(point(private.public_key()))) + field(2, signature))

    def end_link(self):
        self.end_sent = True
        return self.encrypted(0x8001, (0x02001000,), field(1, 1) + field(2, os.urandom(16)))

    def stream(self, request_id, enabled=None, raw=None):
        control = b"" if enabled is None else b"".join(field(n, int(enabled)) for n in self.stream_fields)
        if raw is not None:
            control += field(2, int(raw))
        return self.encrypted(0x8005, (0x8100ce56, 0x02000314) if request_id == 2 else (), field(1, request_id) + field(4, control))

    def stop(self):
        self.stopping = True
        return self.stream(4, False, False if self.raw else None) if self.tx and self.stage == "input" else b""

    def feed(self, data):
        self.pending.extend(data)
        output = bytearray()
        while self.rx is None and len(self.pending) >= 4:
            if not self.pending[0] & 128:
                raise ValueError("unexpected bytes before encryption")
            size = (int.from_bytes(self.pending[:2], "big") & 32767) + 4
            if len(self.pending) < size:
                break
            f = parse_frame(bytes(self.pending[:size]))
            del self.pending[:size]
            p = fields(f.payload)
            remote = need(p, 1, 2, 64)
            key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + remote)
            if f.words == (0x81000005, 0x02000001) and f.channel == 0x8001:
                if self.peer is not None or p.get((3, 0), 0) != 0 or need(p, 4) != 3:
                    raise ValueError("unsupported band encryption parameters")
                self.peer, self.peer_challenge = remote, need(p, 2, 2, 16)
                output.extend(frame(1, (0x02000002,), field(1, self.public) + field(2, self.seed) + field(3, self.iv) + field(4, self.base) + field(5, 3)))
            elif f.words == (0x02000002,) and f.channel == 1:
                if remote != self.peer or need(p, 5) != 3:
                    raise ValueError("unexpected encryption response")
                self.peer_seed = need(p, 2, 2, 32)
                secret = self.private.exchange(ec.ECDH(), key)
                self.tx = CipherState(derive(secret, self.peer_challenge, self.seed), self.iv, self.base)
                self.rx = Receiver(CipherState(derive(secret, self.challenge, self.peer_seed), need(p, 3, 2, 16), p.get((4, 0), 0)))
                if self.enroll:
                    output.extend(self.encrypted(0x8002, (0x81000024, 0x02003000)))
                elif self.identity:
                    output.extend(self.trust())
                else:
                    output.extend(self.encrypted(0x8002, (0x81000024, 0x02003000)) + self.end_link())
            else:
                raise ValueError("unrecognized setup response")
        if self.rx:
            records = self.rx.feed(bytes(self.pending))
            self.pending.clear()
            for plain in records:
                self.authenticated += 1
                self.last_authenticated = self.clock()
                for f in self.datax.feed(plain):
                    if f.words:
                        if len(self.types) >= 1024 and f.channel not in self.types:
                            raise ValueError("too many channels")
                        self.types[f.channel] = f.words[-1]
                    kind = self.types.get(f.channel)
                    output.extend(self.receive(f, kind))
        return bytes(output)

    def receive(self, f, kind):
        if kind is None:
            return b""
        ch = f.channel & 0x7fff
        if kind & 0xff000000 == 0x03000000 and kind != 0x03001000:
            raise ValueError(f"band rejected request: {kind:#010x}; an owned band needs its matching identity or enrollment")
        if self.stage.startswith("enroll"):
            return self.ceremony(f, kind)
        if self.stage == "trust":
            if kind == 0x03001000 and f.channel == 2:
                self.app_trusted = True
            elif kind == 0x02001001 and f.channel & 0x8000:
                if self.band_trusted:
                    raise ValueError("duplicate band identity proof")
                signature = need(fields(f.payload), 2, 2, 64)
                if self.identity[1] is not None:
                    verify(self.identity[1], signature, digest(self.challenge, self.public, self.peer_seed, self.peer))
                else:
                    self.emit("warning", message="No stored band public key: band identity proof is not verified (same fallback as upstream)")
                self.band_trusted = True
                out = self.encrypted(ch, (0x03001000,))
                if self.app_trusted and not self.end_sent:
                    out += self.end_link()
                return out
            elif kind == 0x02001000 and f.channel == 0x8001 and self.end_sent:
                return self.link_ack(f.payload)
            if self.app_trusted and self.band_trusted and not self.end_sent:
                return self.end_link()
            return b""
        if kind == 0x02001000 and f.channel == 0x8001 and self.stage == "link":
            return self.link_ack(f.payload)
        if kind == 0x02000315:
            p = fields(f.payload)
            rid, status = need(p, 1), need(p, 2)
            if self.stage == "info" and ch == 3 and rid == 1:
                if status != 1:
                    raise ValueError("device-info request rejected")
                self.stage = "input"
                return self.stream(2) + self.stream(3, True) + self.encrypted(0x8006, (0x8100ce56, 0x02000314), field(1, 1) + field(5, b""))
            if self.stage != "input":
                return b""
            if ch == 6 and rid == 1:
                config = fields(p.get((6, 2), b""))
                if status == 1 and config.get((10, 0)) in (0, 1):
                    self.hand = "left" if config[10, 0] else "right"
                    self.emit("handedness", hand=self.hand)
            elif ch == 7 and rid == 6 and self.raw:
                if status != 1:
                    raise ValueError("EMG configuration rejected")
                self.emg_config = fields(need(fields(need(p, 6, 2)), 42, 2))
                self.emit("emg_configuration", sample_rate=self.emg_config.get((1, 0)), channels=self.emg_config.get((2, 0)))
                return self.stream(7)
            elif ch == 5:
                if status != 1:
                    raise ValueError("stream request rejected")
                flags = fields(need(p, 5, 2))
                if rid == 4 and self.stopping:
                    self.stop_ack = all(flags.get((n, 0)) == 0 for n in self.stream_fields + ((2,) if self.raw else ()))
                    return b""
                if self.stopping:
                    return b""
                if rid in (3, 5, 8) and not all(flags.get((n, 0)) == 1 for n in self.stream_fields):
                    raise ValueError("band stopped a requested stream")
                if rid == 8 and flags.get((2, 0)) != 1:
                    raise ValueError("raw EMG subscription not confirmed")
                if rid == 3:
                    self.streaming = True
                    self.emit("connected")
                    if self.raw:
                        return self.encrypted(0x8007, (0x8100ce56, 0x02000314), field(1, 6) + field(5, b""))
                if rid == 7 and self.raw:
                    return self.stream(8, True, True)
            return b""
        if self.stage != "input" or self.stopping or not self.streaming:
            return b""
        if kind not in (0x0200020a, 0x0200020d, 0x0200020f, 0x02000212):
            return b""
        p = fields(f.payload)
        seq, stamp = need(p, 1), need(p, 2)
        if kind == 0x0200020d:
            def name(n, names):
                value = p.get((n, 0), 0)
                return names[value] if value < len(names) else f"unknown:{value}"
            self.emit("gesture", sequence=seq, timestamp_us=stamp,
                      finger=name(3, ("unknown", "thumb", "index", "middle", "notApplicable")),
                      action=name(4, ("unknown", "press", "release", "tap", "doubletap", "click", "up", "down", "left", "right", "wake", "swipeIn", "swipeOut", "ia", "partialPress", "partialRelease", "partialClick", "partialUp", "partialDown", "partialLeft", "partialRight")),
                      derived_action=name(5, ("unknown", "singleTap", "doubleTap", "buttonHold", "buttonRelease", "buttonUp", "buttonDown", "buttonLeft", "buttonRight", "buttonPress", "buttonHoldRelease")),
                      synthetic=bool(p.get((12, 0), 0)))
        elif kind == 0x0200020a:
            data = need(p, 3, 2)
            expected = {(1, 0): 2048, (2, 0): 8, (4, 0): 16, (5, 0): 16, (10, 0): 0}
            if not self.emg_config or any(self.emg_config.get(k) != v for k, v in expected.items()) or len(data) != 256:
                self.emit("raw_emg_payload", sequence=seq, timestamp_us=stamp, interpreted=False, payload_b64=base64.b64encode(f.payload).decode())
            else:
                values = struct.unpack("<128H", data)
                missing = 0 if self.last_sequence is None else max(0, seq - self.last_sequence - 1)
                self.last_sequence = seq
                self.emit("emg_batch", sequence=seq, timestamp_us=stamp, missing_before=missing, samples=[list(values[i:i+8]) for i in range(0, 128, 8)])
        else:
            values = struct.unpack("<3h" if kind == 0x0200020f else "<4f", need(p, 3, 2, 6 if kind == 0x0200020f else 16))
            if kind == 0x02000212 and (not all(map(math.isfinite, values)) or not .9 <= sum(x*x for x in values) <= 1.1):
                raise ValueError("invalid orientation quaternion")
            self.emit("gyro" if kind == 0x0200020f else "orientation", sequence=seq, timestamp_us=stamp, values=values)
        return b""

    def link_ack(self, payload):
        p = fields(payload)
        if need(p, 1) != 1:
            raise ValueError("link setup failed")
        if self.identity:
            need(p, 2, 2, 16)
        self.stage = "info"
        return self.encrypted(0x8003, (0x8100ce56, 0x02000314), field(1, 1) + field(3, b""))

    def ceremony(self, f, kind):
        if f.channel & 0x7fff != 2:
            return b""
        p = fields(f.payload)
        if self.stage == "enroll_identity" and kind == 0x02003001:
            self.certificate, self.secondary = need(p, 1, 2), need(p, 5, 2)
            self.serial = need(p, 2, 2).decode("ascii")
            if min(len(self.certificate), len(self.secondary)) <= 256 or not self.serial:
                raise ValueError("invalid band identity")
            self.stage = "enroll_nonce"
            return self.encrypted(0x8002, (0x02002000,))
        if self.stage == "enroll_nonce" and kind == 0x02002001:
            self.stage = "enroll_pending_http"
            self.emit("pair_request", certificate=self.certificate, secondary=self.secondary, serial=self.serial,
                      nonce=need(p, 1, 2, 16), app_public=point(self.enroll_key.public_key()))
        elif self.stage == "enroll_pending" and kind == 0x02002003:
            self.stage = "enroll_final_http"
            self.emit("pair", signature=need(p, 1, 2), receipt=need(p, 2, 2).decode())
        elif self.stage == "enroll_final" and kind == 0x02002005:
            if f.payload or f.words != (0x02002005,):
                raise ValueError("unexpected enrollment confirmation")
            self.identity = self.enroll_key, self.band_key
            self.emit("identity_created", record=identity_record(*self.identity))
            self.stage = "trust"
            return self.trust(opened=True)
        return b""

    def receipt(self, signature, receipt, band_key=None):
        if not signature or not receipt:
            raise ValueError("empty ownership receipt")
        if self.stage == "enroll_pending_http":
            self.stage, kind = "enroll_pending", 0x02002002
        elif self.stage == "enroll_final_http":
            if band_key is not None and len(band_key) != 64:
                raise ValueError("server returned an invalid band public key")
            self.band_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + band_key) if band_key else None
            self.stage, kind = "enroll_final", 0x02002004
        else:
            raise ValueError("unexpected HTTP completion")
        return self.encrypted(0x8002, (kind,), field(1, signature) + field(2, receipt.encode()))
