#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Part of meshcore-sdr-chat.
# Parallel exhaustive channel-name cracker: try every name up to a length over
# a-z0-9-, deriving key = SHA256("#"+name)[:16], filtered by the channel-hash
# byte, MAC-checked. Split across all CPU cores. Lives in its own module so
# multiprocessing 'spawn' children import it cleanly (no re-running the UI).
import hashlib, hmac, itertools, multiprocessing as mp

ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-"

def _search_prefix(args):
    prefix, maxlen, chash, mac_bytes, cipher = args
    import meshcore                         # for plaintext validation (avoids MAC collisions)
    sha = hashlib.sha256
    zero16 = b"\x00" * 16
    for L in range(len(prefix), maxlen + 1):
        for tail in itertools.product(ALPHABET, repeat=L - len(prefix)):
            name = prefix + "".join(tail)
            key = sha(("#" + name).encode()).digest()[:16]
            if sha(key).digest()[0] != chash:
                continue
            if hmac.new(key + zero16, cipher, hashlib.sha256).digest()[:2] == mac_bytes \
               and meshcore.looks_valid_grp(key, cipher):
                return name
    return None

def crack(chash, mac_bytes, cipher, maxlen, workers=None):
    """Return the channel name, or None. Uses all cores; first hit wins."""
    tasks = [(p, maxlen, chash, mac_bytes, cipher) for p in ALPHABET]
    try:
        with mp.Pool(workers or mp.cpu_count()) as pool:
            for res in pool.imap_unordered(_search_prefix, tasks):
                if res:
                    pool.terminate()
                    return res
        return None
    except Exception:                       # fall back to single-process
        for t in tasks:
            r = _search_prefix(t)
            if r: return r
        return None

if __name__ == "__main__":
    # self-test: build a target for a known short name and crack it
    import struct, time
    name = "zx7"
    key = hashlib.sha256(("#" + name).encode()).digest()[:16]
    ch = hashlib.sha256(key).digest()[0]
    cipher = hashlib.sha256(b"probe").digest()[:16]
    mac = hmac.new(key + b"\x00" * 16, cipher, hashlib.sha256).digest()[:2]
    t = time.time()
    print("cracked:", crack(ch, mac, cipher, 3), f"in {time.time()-t:.1f}s on {mp.cpu_count()} cores")
