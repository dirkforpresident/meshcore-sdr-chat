#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Part of meshcore-sdr-chat.
# MeshCore codec — verified against the ripplebiz/MeshCore source.
#
# Channel  (encryption / who can read):   channel_hash = SHA256(key)[0],
#          payload AES-128-ECB(key16), MAC = HMAC-SHA256(secret32, cipher)[:2].
# Scope    (forwarding region, optional): key = SHA256("#"+name)[:16];
#          transport_code = HMAC-SHA256(scope_key, payload_type||payload)[:2]
#          (0000/FFFF reserved). Scoped packets use ROUTE_TYPE_TRANSPORT_FLOOD.
import struct, hmac, hashlib, base64, time
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

PT_GRP_TXT = 5
TXT_TYPE_PLAIN = 0
ROUTE_FLOOD = 1
ROUTE_TRANSPORT_FLOOD = 0
PUBLIC_PSK_B64 = "izOH6cXN6mrJ5e26oRXNcg=="   # MeshCore built-in "Public" channel

# ---------------------------------------------------------------- channel
class Channel:
    """A MeshCore group channel, identified by its pre-shared key (PSK)."""
    def __init__(self, name, psk_b64):
        raw = base64.b64decode(psk_b64)
        if len(raw) not in (16, 32):
            raise ValueError("PSK must decode to 16 or 32 bytes")
        self.name = name
        self.key = raw[:16]                       # AES-128 key
        self.secret = raw + b"\x00" * (32 - len(raw))   # 32-byte HMAC secret
        self.hash = hashlib.sha256(raw).digest()[0]     # channel_hash byte
        self._psk_b64 = psk_b64                   # kept so custom channels can be persisted
    def __repr__(self): return f"<Channel {self.name} hash=0x{self.hash:02x}>"

    @classmethod
    def from_key(cls, name, key16):
        self = cls.__new__(cls)
        self.name = name; self.key = key16
        self.secret = key16 + b"\x00" * 16
        self.hash = hashlib.sha256(key16).digest()[0]
        self._psk_b64 = None                  # name-derived (hashtag) channel
        return self

PUBLIC = Channel("public", PUBLIC_PSK_B64)

def hashtag_channel(name):
    """A community 'hashtag' channel whose key derives from its name:
       key = SHA256('#'+name)[:16] (verified against live traffic)."""
    n = name.lstrip("#")
    return Channel.from_key(n, hashlib.sha256(("#" + n).encode()).digest()[:16])

# backwards-compatible module-level names (public channel)
KEY16, SECRET, CH_HASH = PUBLIC.key, PUBLIC.secret, PUBLIC.hash

def _aes(key): return Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
def _enc(key, pt):
    pt = pt + b"\x00" * ((-len(pt)) % 16)
    return _aes(key).encryptor().update(pt)
def _dec(key, ct): return _aes(key).decryptor().update(ct)
def _mac(secret, ct): return hmac.new(secret, ct, hashlib.sha256).digest()[:2]
mac = _mac                                     # public alias for the 2-byte MAC
def chan_hash(key16): return hashlib.sha256(key16).digest()[0]   # channel_hash byte from a key

def grp_text_from_cipher(key16, cipher):
    """Decrypt a raw GRP_TXT ciphertext with a known key -> 'name: text' (or None)."""
    if len(cipher) < 16 or len(cipher) % 16: return None
    pt = _dec(key16, cipher)
    if len(pt) < 6: return None
    body = pt[5:].split(b"\x00", 1)[0]
    try: return body.decode("utf-8")
    except Exception: return None

def looks_valid_grp(key16, cipher):
    """Decrypt and check the plaintext is a plausible GRP_TXT message.
       The 2-byte MAC alone collides over large search spaces, so a real crack
       must also confirm a plausible timestamp and 'name: text' structure."""
    if len(cipher) < 16 or len(cipher) % 16: return False
    pt = _dec(key16, cipher)
    if len(pt) < 6: return False
    ts = struct.unpack_from("<I", pt, 0)[0]
    if not (1500000000 <= ts <= 2050000000): return False               # ~2017..2035 sanity
    body = pt[5:].split(b"\x00", 1)[0]
    if len(body) < 2: return False
    try: s = body.decode("utf-8")
    except Exception: return False
    if any(ord(c) < 9 or (13 < ord(c) < 32) for c in s): return False   # control chars -> junk
    return ": " in s

# ---------------------------------------------------------------- scope
def scope_key(name):
    n = name if name.startswith("#") else "#" + name
    return hashlib.sha256(n.encode()).digest()[:16]

def transport_code(skey, payload_type, payload):
    h = hmac.new(skey, bytes([payload_type]) + payload, hashlib.sha256).digest()[:2]
    code = h[0] | (h[1] << 8)
    if code == 0: code = 1
    elif code == 0xFFFF: code = 0xFFFE
    return struct.pack("<H", code)

# ---------------------------------------------------------------- build (send)
def build_grp_txt(name, text, timestamp=None, channel=PUBLIC, scope=None):
    if timestamp is None: timestamp = int(time.time())
    inner = struct.pack("<I", timestamp) + bytes([TXT_TYPE_PLAIN])
    inner += f"{name}: {text}".encode("utf-8") + b"\x00"
    ct = _enc(channel.key, inner)
    payload = bytes([channel.hash]) + _mac(channel.secret, ct) + ct
    if scope:
        tc0 = transport_code(scope_key(scope), PT_GRP_TXT, payload)
        header = (PT_GRP_TXT << 2) | ROUTE_TRANSPORT_FLOOD          # 0x14
        return bytes([header]) + tc0 + b"\x00\x00" + bytes([0]) + payload
    header = (PT_GRP_TXT << 2) | ROUTE_FLOOD                        # 0x15
    return bytes([header, 0x00]) + payload

# ---------------------------------------------------------------- scope of a received frame
def frame_scope_code(frame):
    """Return the frame's transport_code[0] bytes if it is scoped, else None."""
    if len(frame) < 6: return None
    if (frame[0] & 3) not in (0, 3): return None      # unscoped route
    return frame[1:3]

def identify_scope(frame, names):
    """Match a scoped frame's transport code against candidate region names.
       Returns the region name, or None (unscoped, or scope not in `names`)."""
    if frame_scope_code(frame) is None: return None
    header = frame[0]; ptype = (header >> 2) & 0xF
    tc0 = frame[1:3]
    i = 1 + 4
    if i >= len(frame): return None
    path_len = frame[i]; i += 1
    i += (path_len & 63) * ((path_len >> 6) + 1)
    payload = frame[i:]
    for n in names:
        if transport_code(scope_key(n), ptype, payload) == tc0:
            return n
    return None

# ---------------------------------------------------------------- parse (recv)
def try_decrypt_grp_txt(frame, channels=None):
    """Try to decrypt a GRP_TXT frame with any of the given channels.
       Returns {timestamp, flags, text, channel} or None."""
    if channels is None: channels = [PUBLIC]
    if len(frame) < 4: return None
    header = frame[0]; route = header & 3; ptype = (header >> 2) & 0xF
    if ptype != PT_GRP_TXT: return None
    i = 1
    if route in (0, 3): i += 4                     # transport codes
    if i >= len(frame): return None
    path_len = frame[i]; i += 1
    hc = path_len & 63; hs = (path_len >> 6) + 1
    i += hc * hs
    payload = frame[i:]
    if len(payload) < 3: return None
    ch_byte = payload[0]; mac, ct = payload[1:3], payload[3:]
    for ch in channels:
        if ch.hash != ch_byte: continue
        if _mac(ch.secret, ct) != mac: continue
        pt = _dec(ch.key, ct)
        if len(pt) < 5: continue
        ts = struct.unpack_from("<I", pt, 0)[0]
        flags = pt[4] >> 2
        msg = pt[5:].split(b"\x00", 1)[0].decode("utf-8", "replace")
        return {"timestamp": ts, "flags": flags, "text": msg, "channel": ch}
    return None

# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    print("public key:", PUBLIC.key.hex(), " hash:", hex(PUBLIC.hash))
    # 1) public unscoped round-trip
    f = build_grp_txt("t", "hallo", timestamp=1754049600)
    assert try_decrypt_grp_txt(f)["text"] == "t: hallo"
    # 2) scoped round-trip + transport-code recompute (repeater findMatch)
    fs = build_grp_txt("t", "scoped", timestamp=1754049600, scope="test")
    assert fs[0] == 0x14, "scoped header must be TRANSPORT_FLOOD"
    payload = fs[6:]        # header(1)+tc0(2)+tc1(2)+path_len(1) then payload
    assert fs[1:3] == transport_code(scope_key("test"), PT_GRP_TXT, payload), "tc mismatch"
    assert try_decrypt_grp_txt(fs)["text"] == "t: scoped"
    # 3) custom channel isolation
    other = Channel("secret", base64.b64encode(b"\x11" * 16).decode())
    fo = build_grp_txt("t", "geheim", timestamp=1754049600, channel=other)
    assert try_decrypt_grp_txt(fo, [PUBLIC]) is None, "public must not read other channel"
    assert try_decrypt_grp_txt(fo, [PUBLIC, other])["text"] == "t: geheim"
    print("self-test OK — public, scoped, and custom-channel round-trips pass.")
