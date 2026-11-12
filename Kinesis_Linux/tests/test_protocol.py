import base64
import struct
import unittest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
from kinesis_linux.session import Session, digest, identity_from_record, identity_record, point, sign, verify
from kinesis_linux.wire import CipherState, DataX, Receiver, derive, field, fields, frame, need, parse_frame
from kinesis_linux.controls import Router
from kinesis_linux.auth import callback_values


class Peer:
    def __init__(self, identity=False, enroll=False, raw=False):
        self.events = []
        self.app = ec.generate_private_key(ec.SECP256R1())
        self.band = ec.generate_private_key(ec.SECP256R1())
        self.s = Session(lambda event, **values: self.events.append((event, values)),
                         identity=(self.app, self.band.public_key()) if identity else None, enroll=enroll, raw=raw)
        private = ec.derive_private_key(1, ec.SECP256R1())
        self.public = point(private.public_key())
        self.challenge, self.seed = bytes(range(16)), bytes(range(32))
        iv = bytes(range(16, 32))
        request = frame(0x8001, (0x81000005, 0x02000001), field(1, self.public) + field(2, self.challenge) + field(3, 0) + field(4, 3))
        enable = frame(1, (0x02000002,), field(1, self.public) + field(2, self.seed) + field(3, iv) + field(4, 42) + field(5, 3))
        output = b"".join(self.s.feed(bytes([b])) for b in request + enable)
        size = (int.from_bytes(output[:2], "big") & 32767) + 4
        local = fields(parse_frame(output[:size]).payload)
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + self.s.public)
        secret = private.exchange(ec.ECDH(), peer)
        self.tx = CipherState(derive(secret, self.s.challenge, self.seed), iv, 42)
        self.rx = Receiver(CipherState(derive(secret, self.challenge, need(local, 2, 2)), need(local, 3, 2), need(local, 4)))
        self.datax = DataX()
        self.startup = self.decode(output[size:])

    def decode(self, data):
        return [f for plain in self.rx.feed(data) for f in self.datax.feed(plain)]

    def send(self, channel, kind, payload=b""):
        data = self.tx.encrypt(frame(channel, (kind,), payload))
        output = b"".join(self.s.feed(bytes([b])) for b in data)
        return self.decode(output)

    def trusted(self, proof_first=False):
        proof = field(1, bytes(12)) + field(2, sign(self.band, digest(self.s.challenge, self.s.public, self.seed, self.public)))
        first = (0x8003, 0x02001001, proof) if proof_first else (2, 0x03001000, b"")
        second = (2, 0x03001000, b"") if proof_first else (0x8003, 0x02001001, proof)
        return self.send(*first) + self.send(*second)

    def input(self):
        self.send(0x8001, 0x02001000, field(1, 1) + field(2, bytes(16)))
        return self.send(3, 0x02000315, field(1, 1) + field(2, 1) + field(4, b""))

    def enabled(self):
        return self.send(5, 0x02000315, field(1, 3) + field(2, 1) + field(5, b"".join(field(n, 1) for n in (3, 6, 8))))


class ProtocolTests(unittest.TestCase):
    def test_upstream_fixed_vectors_and_counter_wrap(self):
        self.assertEqual(derive(bytes(range(32)), bytes(range(16)), bytes(range(32, 64))).hex(), "5080496832e72e0a4de90f22e7797a3c6277197b4bd29f296bd7142850db1998")
        tx = CipherState(bytes(range(32)), bytes(range(16)), 0xffffffff)
        first = tx.encrypt(b"first block.....second block....")
        self.assertEqual(first.hex(), "40c9f748fb0f0ae8e0018675352ee743a1e58ccd288d25d27f6e72551887e4aad6fc68175e7287a1eee1")
        second = tx.encrypt(b"last block......")
        self.assertEqual(second.hex(), "4038cb0decaa0692b900d5962230f8734c8a3de4d262936261f1")
        rx = Receiver(CipherState(bytes(range(32)), bytes(range(16)), 0xffffffff))
        plain = [x for b in first + b"\x81\x00\x01\x02\x2a\x2b\x2c" + second for x in rx.feed(bytes([b]))]
        self.assertEqual(plain, [b"first block.....second block....", b"last block......"])
        self.assertEqual(rx.cipher.counter, 1)

    def test_bad_mac_never_advances_state(self):
        for index in (1, 12):
            rx = CipherState(bytes(32), bytes(16), 3)
            packet = bytearray(CipherState(bytes(32), bytes(16), 3).encrypt(b"data"))
            packet[index] ^= 1
            with self.assertRaises(ValueError):
                rx.decrypt(packet)
            self.assertEqual((rx.counter, rx.iv), (3, bytes(16)))

    def test_datax_continues_across_records(self):
        original = frame(0x8010, (0x0200020d,), field(1, 12) + field(2, 100) + field(3, 2))
        d = DataX()
        a, b = original[:7], original[7:]
        pad = lambda value: value + bytes([0xc0 + (-len(value) % 16)]) * (-len(value) % 16)
        self.assertEqual(d.feed(pad(a)), [])
        self.assertEqual(d.feed(pad(b)), [parse_frame(original)])

    def test_mutual_trust_both_orders(self):
        for proof_first in (False, True):
            p = Peer(identity=True)
            proof = fields(p.startup[0].payload)
            verify(p.app.public_key(), need(proof, 2, 2, 64), digest(p.challenge, p.public, p.s.seed, p.s.public))
            frames = p.trusted(proof_first)
            self.assertEqual([f.words for f in frames], [(0x03001000,), (0x02001000,)])
            requests = p.input()
            self.assertEqual([f.channel for f in requests], [0x8005, 0x8005, 0x8006])
            p.enabled()
            self.assertTrue(p.s.streaming)

    def test_wrong_band_key_is_not_acknowledged(self):
        p = Peer(identity=True)
        wrong = ec.generate_private_key(ec.SECP256R1())
        proof = field(2, sign(wrong, digest(p.s.challenge, p.s.public, p.seed, p.public)))
        with self.assertRaises(InvalidSignature):
            p.send(0x8003, 0x02001001, proof)
        self.assertFalse(p.s.band_trusted)

    def test_owner_rejection_is_fatal(self):
        p = Peer(identity=True)
        with self.assertRaises(ValueError):
            p.send(2, 0x03001043)
        self.assertFalse(p.s.streaming)

    def test_legacy_and_stop_ack(self):
        p = Peer()
        self.assertEqual(len(p.startup), 2)
        p.input()
        p.enabled()
        requests = p.decode(p.s.stop())
        self.assertEqual(requests[0].channel, 0x8005)
        p.send(5, 0x02000315, field(1, 4) + field(2, 1) + field(5, b"".join(field(n, 0) for n in (3, 6, 8))))
        self.assertTrue(p.s.stop_ack)

    def test_gestures_motion_and_raw_emg(self):
        p = Peer(raw=True)
        p.input()
        self.assertEqual(p.enabled()[0].channel, 0x8007)
        config = b"".join(field(n, v) for n, v in ((1,2048),(2,8),(4,16),(5,16),(10,0)))
        p.send(7, 0x02000315, field(1, 6) + field(2, 1) + field(6, field(42, config)))
        p.send(5, 0x02000315, field(1, 7) + field(2, 1) + field(5, b""))
        p.send(5, 0x02000315, field(1, 8) + field(2, 1) + field(5, b"".join(field(n, 1) for n in (2,3,6,8))))
        p.send(6, 0x02000315, field(1, 1) + field(2, 1) + field(6, field(10, 0)))
        p.send(0x8010, 0x0200020d, field(1, 1) + field(2, 10) + field(3, 2) + field(4, 3))
        p.send(0x8011, 0x0200020f, field(1, 1) + field(2, 10) + field(3, struct.pack("<3h", 1, -2, 3)))
        for seq in (1, 3):
            p.send(5, 0x0200020a, field(1, seq) + field(2, 10) + field(3, struct.pack("<128H", *range(128))))
        batches = [v for e, v in p.events if e == "emg_batch"]
        self.assertEqual(batches[0]["samples"][0], list(range(8)))
        self.assertEqual(batches[1]["missing_before"], 1)
        self.assertEqual(p.s.hand, "right")
        self.assertTrue(any(e == "gesture" for e, _ in p.events))

    def test_unknown_emg_encoding_stays_opaque(self):
        p = Peer(raw=True)
        p.input(); p.enabled()
        p.send(5, 0x0200020a, field(1, 1) + field(2, 5) + field(3, bytes(256)))
        self.assertEqual(p.events[-1][0], "raw_emg_payload")

    def test_identity_import_rejects_mismatched_public(self):
        a, b = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
        record = identity_record(a, b.public_key())
        self.assertEqual(point(identity_from_record(record)[0].public_key()), point(a.public_key()))
        record["AppPublicKey"] = base64.b64encode(point(b.public_key())).decode()
        with self.assertRaises(ValueError):
            identity_from_record(record)

    def test_enrollment_preserves_signed_receipt(self):
        p = Peer(enroll=True)
        p.send(0x8002, 0x02003001, field(1, bytes(300)) + field(2, b"SERIAL") + field(5, bytes(300)))
        p.send(0x8002, 0x02002001, field(1, bytes(16)))
        receipt = '{ "keep": "spaces" }'
        out = p.decode(p.s.receipt(b"signature", receipt))
        self.assertEqual(need(fields(out[0].payload), 2, 2).decode(), receipt)
        p.send(0x8002, 0x02002003, field(1, b"band signature") + field(2, receipt.encode()))
        p.decode(p.s.receipt(b"final signature", receipt, point(p.band.public_key())))
        out = p.send(0x8002, 0x02002005)
        self.assertEqual(out[0].words, (0x02001000,))
        self.assertTrue(any(e == "identity_created" for e, _ in p.events))
        p.trusted(); p.input(); p.enabled()
        self.assertTrue(p.s.streaming)

    def test_callback_state_validation(self):
        import hashlib
        token = hashlib.sha256(b"native").hexdigest()[:16]
        self.assertEqual(callback_values(f"fb-viewapp://frl_login?token={token}&blob=test", "native"), "test")
        self.assertEqual(callback_values(f"oculus://link/frl_login?token={token}&blob=test", "native"), "test")
        self.assertEqual(callback_values(f"oculus://frl_login?token={token}&blob=test", "native"), "test")
        with self.assertRaises(ValueError):
            callback_values("fb-viewapp://frl_login?token=wrong&blob=test", "native")
        with self.assertRaises(ValueError):
            callback_values("oculus://link/frl_login?token=wrong&blob=test", "native")
        with self.assertRaises(ValueError):
            callback_values(f"oculus://link/other?token={token}&blob=test", "native")

    def test_raw_derived_duplicate_suppression(self):
        router = Router(clock=lambda: 100)
        data = dict(finger="thumb", action="left", derived_action="unknown", sequence=1, timestamp_us=100)
        self.assertEqual(router.gesture(data), "swipe_left")
        self.assertIsNone(router.gesture(data))
        self.assertIsNone(router.gesture(dict(data, action="unknown", derived_action="buttonLeft", sequence=2)))
        self.assertIsNone(router.gesture(dict(data, synthetic=True, sequence=3)))


if __name__ == "__main__":
    unittest.main()
