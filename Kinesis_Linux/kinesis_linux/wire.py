"""AirShield and DataX, translated from KinesisCore/{AirShield,BandWire}.swift.

Only the observed parameter-3 exchange is supported. Never log key material.
"""
import hashlib
import hmac
from dataclasses import dataclass
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def sha(data):
    return hashlib.sha256(data).digest()


def varint(value):
    if not 0 <= value < 2**64:
        raise ValueError("integer outside uint64")
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    return bytes(out) + bytes([value])


def field(number, value):
    if not 0 < number < 2**29:
        raise ValueError("invalid field number")
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    return varint(number << 3 | 2) + varint(len(value)) + value


def fields(data):
    offset, result = 0, {}
    def read():
        nonlocal offset
        value = 0
        for shift in range(0, 64, 7):
            if offset >= len(data):
                raise ValueError("truncated protobuf integer")
            b = data[offset]
            offset += 1
            if shift == 63 and b > 1:
                raise ValueError("protobuf integer overflow")
            value |= (b & 127) << shift
            if b < 128:
                return value
        raise ValueError("invalid protobuf integer")
    while offset < len(data):
        tag = read()
        number, wire = tag >> 3, tag & 7
        if not 0 < number < 2**29 or (number, wire) in result:
            raise ValueError("invalid or repeated scalar field")
        if wire == 0:
            value = read()
        else:
            if wire not in (1, 2, 5):
                raise ValueError("unsupported protobuf wire type")
            size = read() if wire == 2 else (8 if wire == 1 else 4)
            if size > len(data) - offset:
                raise ValueError("truncated protobuf bytes")
            value = data[offset:offset + size]
            offset += size
        result[number, wire] = value
    return result


def need(values, number, wire=0, size=None):
    value = values[number, wire]
    if size is not None and len(value) != size:
        raise ValueError("unexpected protobuf byte length")
    return value


@dataclass(frozen=True)
class Frame:
    channel: int
    words: tuple
    payload: bytes


def frame(channel, words=(), payload=b""):
    body = b"".join(w.to_bytes(4, "big") for w in words) + payload
    if len(body) > 32767:
        raise ValueError("DataX frame too large")
    return (len(body) | (32768 if words else 0)).to_bytes(2, "big") + channel.to_bytes(2, "big") + body


def parse_frame(data):
    length = int.from_bytes(data[:2], "big")
    if len(data) != (length & 32767) + 4:
        raise ValueError("invalid DataX length")
    words, offset = [], 4
    if length & 32768:
        while True:
            if offset + 4 > len(data):
                raise ValueError("truncated typed header")
            word = int.from_bytes(data[offset:offset + 4], "big")
            offset += 4
            words.append(word)
            if not word & 0x80000000:
                break
    return Frame(int.from_bytes(data[2:4], "big"), tuple(words), data[offset:])


class DataX:
    def __init__(self):
        self.pending = bytearray()

    def feed(self, plain):
        if not plain or len(plain) % 16:
            raise ValueError("unaligned plaintext")
        padding = plain[-1] - 0xc0
        if 1 <= padding <= 15 and plain[-padding:] == bytes([plain[-1]]) * padding:
            plain = plain[:-padding]
        self.pending.extend(plain)
        out = []
        while len(self.pending) >= 4:
            size = (int.from_bytes(self.pending[:2], "big") & 32767) + 4
            if len(self.pending) < size:
                break
            out.append(parse_frame(bytes(self.pending[:size])))
            del self.pending[:size]
        return out


def derive(secret, challenge, seed):
    if (len(secret), len(challenge), len(seed)) != (32, 16, 32):
        raise ValueError("invalid AirShield key material")
    hashed = sha(secret)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=sha(hashed + challenge + seed), info=b"AirShield").derive(hashed)


class CipherState:
    def __init__(self, key, iv, counter):
        if len(key) != 32 or len(iv) != 16 or not 0 <= counter < 2**32:
            raise ValueError("invalid cipher parameters")
        self.key, self.iv, self.counter = key, iv, counter

    def tag(self, body):
        return hmac.new(self.key, self.counter.to_bytes(4, "little") + body, hashlib.sha256).digest()[:8]

    def encrypt(self, plain):
        padding = -len(plain) % 16
        plain += bytes([0xc0 + padding]) * padding
        if not plain or len(plain) > 4096:
            raise ValueError("invalid encrypted record length")
        encryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).encryptor()
        ciphertext = encryptor.update(plain) + encryptor.finalize()
        body = bytes([len(ciphertext) // 16 - 1]) + ciphertext
        packet = b"\x40" + self.tag(body) + body
        self.iv, self.counter = ciphertext[-16:], (self.counter + 1) & 0xffffffff
        return packet

    def decrypt(self, packet):
        if len(packet) < 26 or packet[0] != 0x40 or len(packet) != 10 + (packet[9] + 1) * 16:
            raise ValueError("invalid authenticated record")
        if not hmac.compare_digest(self.tag(packet[9:]), packet[1:9]):
            raise ValueError("packet authentication failed")
        ciphertext = packet[10:]
        decryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).decryptor()
        plain = decryptor.update(ciphertext) + decryptor.finalize()
        self.iv, self.counter = ciphertext[-16:], (self.counter + 1) & 0xffffffff
        return plain


class Receiver:
    def __init__(self, cipher):
        self.cipher, self.pending = cipher, bytearray()

    def feed(self, data):
        self.pending.extend(data)
        result = []
        while self.pending:
            marker = self.pending[0]
            if marker in (0x81, 0x82):
                if len(self.pending) < 2:
                    break
                if self.pending[1] > 1:
                    raise ValueError("unsupported relay control")
                del self.pending[:2]
                continue
            if marker in (1, 2):
                if len(self.pending) < 2:
                    break
                size = 3 + self.pending[1]
            elif marker in (0x40, 0x41, 0x42):
                if len(self.pending) < 10:
                    break
                size = 10 + (self.pending[9] + 1) * 16
            else:
                raise ValueError("unsupported transport marker")
            if len(self.pending) < size:
                break
            if marker == 0x40:
                result.append(self.cipher.decrypt(bytes(self.pending[:size])))
            # Relay channels never reach the input decoder.
            del self.pending[:size]
        return result
