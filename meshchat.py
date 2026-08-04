#!/usr/bin/env python3
# meshcore-sdr-chat — MeshCore public-channel chat over SDR (USRP + gr-lora_sdr)
#
# Read AND send on the MeshCore public channel with a pure SDR receiver/
# transmitter — no LoRa transceiver chip involved.
#
#   python3 meshchat.py --name yourhandle
#
# Split-screen terminal UI: scrolling chat on top, input line at the bottom.
# Type + Enter to send.  Ctrl-D or /quit to exit.
#
# SPDX-License-Identifier: GPL-3.0-or-later
import os, sys
# Silence UHD's own console logger before GNU Radio pulls it in.
os.environ.setdefault("UHD_LOG_CONSOLE_LEVEL", "off")

import time, argparse, curses, textwrap, datetime, queue, threading, json, subprocess
import meshcore

# The SDR flowgraph runs in a separate process (sdr_worker.py); the parent (this
# file) has no GNU Radio dependency, so a native gr-lora_sdr crash never touches
# the UI, chat history, cracker or state — the worker is just respawned.

def _err(msg):
    try: sys.stderr.write(msg)
    except Exception: pass

import datetime as _dt
ERR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meshchat_errors.log")
def log_error(where, exc):
    try:
        import traceback
        with open(ERR_LOG, "a", encoding="utf-8") as f:
            f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} [{where}] "
                    f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}\n")
    except Exception:
        pass

# ---------------------------------------------------------------- args
ap = argparse.ArgumentParser(description="MeshCore public-channel chat over SDR")
ap.add_argument("--name", default="notsure", help="display name (NOT a callsign — this is ISM, not ham)")
ap.add_argument("--freq", type=float, default=869.618e6, help="center frequency in Hz")
ap.add_argument("--bw",   type=float, default=62500,  help="LoRa bandwidth in Hz")
ap.add_argument("--sf",   type=int,   default=8,      help="spreading factor")
ap.add_argument("--cr",   type=int,   default=4,      help="coding rate index (1=4/5 .. 4=4/8)")
ap.add_argument("--sync", type=lambda x: int(x, 0), default=0x12, help="LoRa sync word")
ap.add_argument("--rxgain", type=float, default=65)
ap.add_argument("--txgain", type=float, default=89)
ap.add_argument("--osf",  type=int,   default=4, help="oversampling factor (samp_rate = bw*osf)")
ap.add_argument("--min-gap", type=float, default=3.0, help="min seconds between transmissions (duty cycle)")
ap.add_argument("--watchdog", type=int, default=0, metavar="SEC",
                help="restart the radio if no frame is decoded for SEC seconds "
                     "(0 = off, default; the mesh is often quiet, so a stall is not a fault. "
                     "The supervisor already respawns the worker on a real crash. "
                     "Set e.g. 1800 only if your USRP tends to wedge silently)")
ap.add_argument("--scope", default=None, help="forwarding region for sends, e.g. de-nord (default: unscoped)")
ap.add_argument("--sdr", choices=["uhd", "soapy"], default="uhd", help="SDR backend (default uhd)")
ap.add_argument("--device", default="", help="UHD device args, e.g. 'serial=30D3F49'")
ap.add_argument("--soapy-driver", default="rtlsdr", help="SoapySDR RX driver, e.g. rtlsdr, hackrf, plutosdr")
ap.add_argument("--soapy-tx-driver", default=None, help="SoapySDR TX driver (default: same as RX)")
ap.add_argument("--ant", default="TX/RX", help="UHD antenna port (default TX/RX)")
ap.add_argument("--log", nargs="?", const="meshcore-log.jsonl", default=None,
                metavar="FILE", help="log adverts and messages to a JSONL file (passive observer)")
ap.add_argument("--monitor", action="store_true",
                help="decrypt and show ALL catalog channels, not just joined ones")
ap.add_argument("--crack", action="store_true",
                help="crack unknown channel keys with the built-in name generator (~3.6M)")
ap.add_argument("--wordlist", default=None, metavar="FILE",
                help="crack unknown channel keys from a wordlist file instead of the generator")
ap.add_argument("--brute-len", type=int, default=0, metavar="N",
                help="after the wordlist, exhaustively brute-force names up to N chars (0 = off)")
ap.add_argument("--rx-only", action="store_true", help="receive only, disable transmitter")
ap.add_argument("--plain", action="store_true", help="plain line mode instead of the curses UI")
ap.add_argument("--seconds", type=int, default=0, metavar="N",
                help="headless: receive for N seconds (printing decodes) then exit")
args = ap.parse_args()

# PlutoSDR (AD936x) can't sample below ~521 kSPS. Our default rate (bw*osf) is
# lower for narrowband LoRa, so bump the oversampling until we clear the floor.
PLUTO_MIN_RATE = 521e3
if args.sdr == "soapy" and "pluto" in (args.soapy_driver + (args.soapy_tx_driver or "")).lower():
    while args.bw * args.osf < PLUTO_MIN_RATE:
        args.osf *= 2

RATE = args.bw * args.osf

# ---------------------------------------------------------------- events
# decode/UI events -> UI (main thread) via a thread-safe queue.
EVENTS = queue.Queue()
START = time.time()
def emit(kind, text): EVENTS.put((kind, text))
def now(): return datetime.datetime.now().strftime("%H:%M:%S")
def uptime():
    s = int(time.time() - START); return f"{s//3600:d}h{(s%3600)//60:02d}m{s%60:02d}s" if s>=3600 else f"{s//60:d}m{s%60:02d}s"
def rel_age(ts):
    if not ts: return "?"
    s = int(time.time() - ts)
    return f"{s}s" if s < 60 else (f"{s//60}m" if s < 3600 else f"{s//3600}h")

# ---------------------------------------------------------------- RX sink
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdr_worker.py")

class App:
    """Parent-side app: session state, MeshCore decode, cracker and UI. The SDR
       flowgraph runs in a child process (sdr_worker.py); this supervises it."""
    ADVT = {0: "none", 1: "chat", 2: "repeater", 3: "room", 4: "sensor"}
    STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
    def __init__(self):
        self.logf = open(args.log, "a", buffering=1) if args.log else None
        self.channels = [meshcore.PUBLIC]; self.active = meshcore.PUBLIC
        self.scope = args.scope; self.monitor = args.monitor
        self.found_channels = []; self.brute = None
        self.tx_ok = not args.rx_only; self.muted = False; self.verbose = False
        self.last_tx = 0.0; self.health = "ok"; self.restarts = 0
        self.seen = set(); self.adv_seen = set(); self.pending = None
        self.heard = 0; self.msgs = 0; self.nodes = {}
        self.active_channels = {}; self.active_scopes = {}
        self.proc = None; self._stop = threading.Event(); self._plock = threading.Lock()
        self.load_state()
    # ---- SDR worker lifecycle ----
    def _argv(self):
        a = [sys.executable, WORKER, "--freq", repr(args.freq), "--bw", repr(args.bw),
             "--sf", str(args.sf), "--cr", str(args.cr), "--sync", str(args.sync),
             "--rxgain", str(args.rxgain), "--txgain", str(args.txgain), "--osf", str(args.osf),
             "--ant", args.ant, "--sdr", args.sdr, "--soapy-driver", args.soapy_driver]
        if args.device: a += ["--device", args.device]
        if args.soapy_tx_driver: a += ["--soapy-tx-driver", args.soapy_tx_driver]
        if args.rx_only: a += ["--rx-only"]
        return a
    def _spawn(self):
        # worker stderr -> logfile so a device-open failure leaves a trail
        # (else the supervisor would just respawn silently in a loop)
        werr = open(os.path.join(os.path.dirname(WORKER), "sdr_worker.log"), "a", buffering=1)
        self.proc = subprocess.Popen(self._argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=werr, text=True, bufsize=1)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
    def start(self):
        self._spawn()
        threading.Thread(target=self._supervisor, daemon=True).start()
    def _reader(self, proc):
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line: continue
                try: m = json.loads(line)
                except Exception: continue
                if "f" in m:
                    self.heard += 1
                    try: self.handle_frame(bytes.fromhex(m["f"]), m.get("s") or 0, m.get("n") or 0)
                    except Exception as e: log_error("handle_frame", e)
                elif m.get("ready"):
                    if not m.get("tx", True): self.tx_ok = False
                    if self.health != "dead": self.health = "ok"
        except Exception:
            pass
    def _supervisor(self):
        while not self._stop.is_set():
            p = self.proc
            if p is not None and p.poll() is not None and not self._stop.is_set():
                self.restarts += 1; self.health = "restarting"
                emit("alert", f"SDR-Prozess beendet (Code {p.returncode}) — Neustart …")
                time.sleep(1.0)
                if self._stop.is_set(): break
                self._spawn(); emit("sys", "SDR-Prozess neu gestartet")
            self._stop.wait(0.5)
    def restart_worker(self):
        with self._plock:
            self.health = "restarting"; self.restarts += 1
            try:
                if self.proc: self.proc.terminate()
            except Exception: pass
        return True                              # the supervisor respawns it
    def shutdown(self):
        self._stop.set()
        try:
            if self.proc:
                try: self.proc.stdin.write('{"quit":1}\n'); self.proc.stdin.flush()
                except Exception: pass
                self.proc.terminate()
        except Exception: pass
    # ---- send ----
    def send(self, text):
        if not self.tx_ok: return "receive-only mode"
        if self.muted: return "muted (/unmute)"
        gap = args.min_gap - (time.time() - self.last_tx)
        if gap > 0: return f"wait {gap:.1f}s (duty cycle)"
        frame = meshcore.build_grp_txt(args.name, text, channel=self.active, scope=self.scope)
        self.pending = f"{args.name}: {text}"
        try:
            with self._plock:
                self.proc.stdin.write(json.dumps({"tx": frame.hex()}) + "\n"); self.proc.stdin.flush()
        except Exception as e:
            return f"TX-Fehler: {e}"
        self.last_tx = time.time(); return None
    # ---- state persistence ----
    def log(self, rec):
        if not self.logf: return
        try:
            rec = {"t": datetime.datetime.now().isoformat(timespec="seconds"), **rec}
            self.logf.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
    def save_state(self):
        try:
            data = {"scope": self.scope, "monitor": self.monitor, "active": self.active.name,
                    "channels": [{"name": c.name, "psk": c._psk_b64} for c in self.channels],
                    "found": [c.name for c in self.found_channels],
                    "nodes": self.nodes, "active_channels": self.active_channels,
                    "active_scopes": self.active_scopes}
            tmp = self.STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f)
            os.replace(tmp, self.STATE_FILE)
        except Exception:
            pass
    def load_state(self):
        try:
            with open(self.STATE_FILE, encoding="utf-8") as f: data = json.load(f)
        except Exception:
            return
        chans = []
        for c in data.get("channels", []):
            n = c.get("name")
            if n == "public": chans.append(meshcore.PUBLIC)
            elif c.get("psk"):
                try: chans.append(meshcore.Channel(n, c["psk"]))
                except Exception: pass
            elif n: chans.append(meshcore.hashtag_channel(n))
        if chans: self.channels = chans
        self.found_channels = [meshcore.hashtag_channel(n) for n in data.get("found", []) if n]
        if data.get("scope") and not args.scope: self.scope = data["scope"]
        if data.get("monitor"): self.monitor = True
        act = data.get("active")
        self.active = next((c for c in self.channels if c.name == act), self.channels[0])
        for k, v in data.get("nodes", {}).items():
            if isinstance(v, list):
                v = {"name": v[0] if v else k, "type": v[1] if len(v) > 1 else "?"}
            self.nodes[k] = v
        for skey, dst in (("active_channels", self.active_channels), ("active_scopes", self.active_scopes)):
            for k, v in data.get(skey, {}).items():
                dst[k] = v if isinstance(v, dict) else {"n": int(v or 0), "last": 0}
    # ---- frame handling (decode incoming frames from the worker) ----
    def handle_frame(self, f, sig=0, snr=0):
        if len(f) < 2: return
        h = f[0]; route = h & 3; ptype = (h >> 2) & 0xF
        i = 1 + (4 if route in (0, 3) else 0)
        if i >= len(f): return
        pl = f[i]; hops = pl & 63
        if ptype == 4:                                   # ADVERT — dim life-sign
            payload = f[i + 1 + hops * ((pl >> 6) + 1):]
            if len(payload) >= 100:
                pub = payload[:32]; app = payload[100:]
                typ = self.ADVT.get(app[0] & 0xF, "?") if app else "?"
                nm = ""
                if app and (app[0] & 0x80):
                    j = 1 + (8 if app[0] & 0x10 else 0) + (2 if app[0] & 0x20 else 0) + (2 if app[0] & 0x40 else 0)
                    nm = app[j:].decode("utf-8", "replace")
                lat = lon = None
                if app and (app[0] & 0x10) and len(app) >= 9:
                    import struct as _st
                    lat = _st.unpack_from("<i", app, 1)[0] / 1e6
                    lon = _st.unpack_from("<i", app, 5)[0] / 1e6
                nid = pub[:6].hex()
                self.nodes[nid] = {"name": nm or nid[:6], "type": typ, "sig": round(sig, 1),
                                   "last": time.time(), "lat": lat, "lon": lon, "hops": hops}
                self.log({"kind": "advert", "node": nid, "name": nm, "type": typ,
                               "lat": lat, "lon": lon, "hops": hops, "sig": round(sig, 1)})
                if pub[:6] not in self.adv_seen:
                    self.adv_seen.add(pub[:6])
                    emit("sys", f"advert: {nm or nid[:6]} [{typ}]  {sig:.0f} dBFS")
            return
        # discovery: identify active channels/scopes against the catalog (display-independent)
        def _bump(d, k):
            e = d.get(k) or {"n": 0, "last": 0}
            e["n"] += 1; e["last"] = time.time(); d[k] = e
        try:
            if ptype == 5:
                idc = meshcore.try_decrypt_grp_txt(f, CAT_CH_OBJS)
                if idc: _bump(self.active_channels, idc["channel"].name)
            if route in (0, 3):
                sc = meshcore.identify_scope(f, CAT_REGION_CODES)
                if sc: _bump(self.active_scopes, sc)
        except Exception:
            pass

        # decrypt for display: joined + bruteforce-found (+ whole catalog in monitor mode)
        chans = self.channels + self.found_channels
        if self.monitor: chans = chans + CAT_CH_OBJS
        msg = meshcore.try_decrypt_grp_txt(f, chans)
        if not msg:
            # unknown channel — hand it to the bruteforcer (once per distinct message)
            if ptype == 5 and self.brute is not None:
                payload = f[i:]
                if len(payload) >= 3:
                    ch_b, mac_b, cipher = payload[0], payload[1:3], payload[3:]
                    self.brute.submit(ch_b, mac_b, cipher)
            return                                       # other type / channel we can't read
        key = (msg["timestamp"], msg["text"], msg["channel"].name)
        if key in self.seen: return                      # flood duplicate
        self.seen.add(key)
        text = msg["text"]
        if self.pending and text == self.pending:        # our own message came back
            src = "on-air" if hops == 0 else f"relay hop{hops}"
            emit("ok", f"sent ({src})"); self.pending = None
            return
        self.msgs += 1
        joined_names = [c.name for c in self.channels]
        show_pref = self.monitor or len(joined_names) > 1 or msg["channel"].name not in joined_names
        chan = f"[{msg['channel'].name}] " if show_pref else ""
        # Scope der empfangenen Nachricht bestimmen (Transport-Code -> Region)
        stag = ""; scope = None
        if (f[0] & 3) in (0, 3):
            cand = [n for n, _ in KNOWN_REGIONS]
            if self.scope and self.scope not in cand: cand.append(self.scope)
            scope = meshcore.identify_scope(f, cand)
            stag = f"#{scope} " if scope else f"#?{f[1:3].hex()} "
        self.log({"kind": "msg", "channel": msg["channel"].name, "scope": scope,
                  "hops": hops, "text": text, "sig": round(sig, 1), "snr": round(snr, 1)})
        emit("in", f"{sig:+.0f}dB {stag}{chan}{text}")


# ---------------------------------------------------------------- catalog
# Offline snapshot of community channel/region names (data/*.json). Keys are
# always derived from the name, so the catalog is only for browsing/discovery.
def _load_json(fname):
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", fname)
        with open(p, encoding="utf-8") as f: return json.load(f)
    except Exception:
        return []

# fallback list (verified on air) merged with the catalog
_SEED_REGIONS = [("de", "Deutschland"), ("de-nord", "Norddeutschland"),
    ("de-sh", "Schleswig-Holstein"), ("de-hh", "Hamburg"), ("de-ni", "Niedersachsen"),
    ("hansemesh", "Hamburg & Umland"), ("heidemesh", "Lueneburger Heide"),
    ("seevetal", "Seevetal"), ("landkreis-harburg", "LK Harburg"), ("europe", "Europa")]
_rmap = {c: n for c, n in _SEED_REGIONS}
for r in _load_json("regions.json"): _rmap.setdefault(r.get("code", ""), r.get("name", ""))
_rmap.pop("", None)
CAT_REGIONS = sorted(_rmap.items())                       # [(code, name)]
CAT_REGION_CODES = [c for c, _ in CAT_REGIONS]
KNOWN_REGIONS = CAT_REGIONS                                # back-compat alias

_cmap = {}
for c in _load_json("channels.json"): _cmap[c.get("name", "")] = c.get("desc", "")
_cmap.pop("", None)
CAT_CHANNELS = sorted(_cmap.items())                      # [(name, desc)]
CAT_CH_OBJS = [meshcore.hashtag_channel(n) for n, _ in CAT_CHANNELS]  # for /discover

# ---------------------------------------------------------------- commands
HELP = [
    "/help            diese Uebersicht",
    "/quit  /exit     beenden (auch Strg-D)",
    "/name <name>     Anzeigename aendern",
    "/join #praefix   Katalog durchsuchen; /join #name tritt bei (Key aus Name)",
    "/join <n> <psk>  Channel mit base64-PSK beitreten (Custom)",
    "/part <ch>       Channel verlassen",
    "/channels        beigetretene Channels (* = aktiv zum Senden)",
    "/scope #praefix  Regionen durchsuchen; /scope #name setzt Sende-Region",
    "/scope off       unscoped senden",
    "/regions [pre]   Regionen auflisten (optional gefiltert)",
    "/discover        aktive Kanaele/Regionen aus dem Empfang (Katalog-Abgleich)",
    "/monitor         ALLE Katalog-Kanaele mitlesen (nicht nur beigetretene)",
    "/filter <text>   Chat filtern (leer = aus);  Bild-hoch/runter = scrollen",
    "/map             Karte der Nodes mit Position (map.html im Browser)",
    "/nodes           gehoerte Nodes (aus Adverts)",
    "/stats           Zaehler und Laufzeit",
    "/clear           Chatverlauf leeren",
    "/mute  /unmute   Senden aus/an",
    "/ver             mehr anzeigen: sent-Bestaetigungen + Adverts",
    "/mouse           Klick-auf-Zeile setzt deren Scope an/aus",
    "Tipp: empfangene Zeile anklicken -> deren #scope wird dein Sende-Scope",
]
COMMANDS = ["/help", "/quit", "/exit", "/name", "/join", "/channel", "/part", "/channels",
            "/scope", "/regions", "/discover", "/monitor", "/filter", "/map", "/nodes",
            "/stats", "/clear", "/mute", "/unmute", "/ver", "/mouse"]

def make_map(points, path):
    """Write a standalone Leaflet HTML map of nodes with positions."""
    markers = []
    for k, v in points:
        nm = json.dumps(v.get("name", k)); typ = v.get("type", "?")
        pop = json.dumps(f"{v.get('name',k)} [{typ}] {v.get('sig','?')}dBFS · {k[:6]}")
        markers.append(f"L.marker([{v['lat']},{v['lon']}]).addTo(m).bindPopup({pop});")
    lats = [v["lat"] for _, v in points]; lons = [v["lon"] for _, v in points]
    ctr = f"[{sum(lats)/len(lats)},{sum(lons)/len(lons)}]"
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>MeshCore Nodes</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#m{{height:100%;margin:0}}</style></head><body><div id="m"></div>
<script>var m=L.map('m').setView({ctr},9);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'OSM'}}).addTo(m);
{chr(10).join(markers)}</script></body></html>"""
    with open(path, "w", encoding="utf-8") as f: f.write(html)

def handle_command(tb, line):
    """returns (quit: bool, action: str|None, lines: list[str])
       action can be 'clear'."""
    p = line.split(maxsplit=1)
    cmd = p[0].lower(); arg = (p[1].strip() if len(p) > 1 else "")
    if cmd in ("/quit", "/exit"):
        return True, None, []
    if cmd == "/help":
        return False, None, HELP
    if cmd == "/name":
        if not arg: return False, None, ["usage: /name <name>"]
        args.name = arg; return False, None, [f"Name -> {args.name}"]
    if cmd == "/nodes":
        n = tb.nodes
        if not n: return False, None, ["noch keine Nodes gehoert"]
        rows = sorted(n.items(), key=lambda kv: -(kv[1].get("last", 0)))
        out = [f"{len(n)} Node(s) (neueste zuerst):"]
        for k, v in rows[:30]:
            pos = f" @{v['lat']:.3f},{v['lon']:.3f}" if v.get("lat") else ""
            out.append(f"  {v['name']} [{v['type']}] {v.get('sig','?')}dBFS vor {rel_age(v.get('last'))} ({k[:4]}){pos}")
        return False, None, out
    if cmd == "/stats":
        s = tb
        return False, None, [f"Laufzeit {uptime()} · Frames {s.heard} · Chat-Msgs {s.msgs} · "
                             f"Nodes {len(s.nodes)} · Senden {'aus' if getattr(tb,'muted',False) else 'an'}"]
    if cmd in ("/join", "/channel"):
        parts = arg.split()
        if not parts:
            return False, None, ["usage: /join #name  (oder #praefix zum Suchen, oder: /join name <psk>)"]
        chname = parts[0].lstrip("#")
        def _do_join(ch):
            if not any(c.name == ch.name for c in tb.channels):
                tb.channels.append(ch)
            tb.active = next((c for c in tb.channels if c.name == ch.name), ch)
            tb.save_state()
            return False, None, [f"aktiver Channel: #{tb.active.name} (hash 0x{tb.active.hash:02x})"]
        if len(parts) >= 2:                                # explicit PSK -> custom channel
            try: return _do_join(meshcore.Channel(chname, parts[1]))
            except Exception as e: return False, None, [f"ungueltiger PSK: {e}"]
        if chname.lower() == "public":
            return _do_join(meshcore.PUBLIC)
        low = chname.lower()
        exact = [n for n, _ in CAT_CHANNELS if n.lower() == low]
        if exact:
            return _do_join(meshcore.hashtag_channel(exact[0]))
        matches = [(n, d) for n, d in CAT_CHANNELS if n.lower().startswith(low)]
        if len(matches) == 1:
            return _do_join(meshcore.hashtag_channel(matches[0][0]))
        if len(matches) > 1:
            head = [f"{len(matches)} Kanaele mit '{chname}': (genauer angeben)"]
            return False, None, head + [f"  #{n:22s} {d}" for n, d in matches[:30]] + (["  …"] if len(matches) > 30 else [])
        return _do_join(meshcore.hashtag_channel(chname))   # not in catalog — join anyway
    if cmd == "/part":
        chname = arg.lstrip("#")
        if chname == "public": return False, None, ["public kann nicht verlassen werden"]
        tb.channels = [c for c in tb.channels if c.name != chname]
        if tb.active.name == chname: tb.active = tb.channels[0]
        return False, None, [f"verlassen: {chname}; aktiv: {tb.active.name}"]
    if cmd == "/channels":
        return False, None, ["Channels:"] + [f"  {'*' if c is tb.active else ' '} {c.name} (0x{c.hash:02x})" for c in tb.channels]
    if cmd == "/scope":
        if not arg: return False, None, [f"aktueller Scope: {tb.scope or 'unscoped'}"]
        if arg.lower() in ("off", "none", "unscoped"):
            tb.scope = None; return False, None, ["Scope aus (unscoped)"]
        t = arg.lstrip("#").lower()
        exact = [c for c in CAT_REGION_CODES if c.lower() == t]
        if exact:
            tb.scope = exact[0]; return False, None, [f"Sende-Scope: #{tb.scope}"]
        matches = [(c, n) for c, n in CAT_REGIONS if c.lower().startswith(t)]
        if len(matches) == 1:
            tb.scope = matches[0][0]; return False, None, [f"Sende-Scope: #{tb.scope}"]
        if len(matches) > 1:
            head = [f"{len(matches)} Regionen mit '{arg.lstrip('#')}': (genauer angeben)"]
            return False, None, head + [f"  #{c:20s} {n}" for c, n in matches[:30]] + (["  …"] if len(matches) > 30 else [])
        tb.scope = arg.lstrip("#")                          # not in catalog — use anyway
        return False, None, [f"Sende-Scope: #{tb.scope} (nicht im Katalog)"]
    if cmd == "/regions":
        rows = [(c, n) for c, n in CAT_REGIONS if not arg or c.lower().startswith(arg.lstrip("#").lower())]
        head = [f"Regionen ({len(rows)}{' gefiltert' if arg else ''}):"]
        return False, None, head + [f"  #{c:20s} {n}" for c, n in rows[:40]] + (["  …"] if len(rows) > 40 else [])
    if cmd == "/map":
        pts = [(k, v) for k, v in tb.nodes.items() if v.get("lat") and v.get("lon")]
        if not pts: return False, None, ["keine Nodes mit Position gehoert (noch)"]
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map.html")
        try: make_map(pts, path)
        except Exception as e: return False, None, [f"Karten-Fehler: {e}"]
        return False, None, [f"Karte mit {len(pts)} Nodes -> {path}", "  im Browser oeffnen: open " + path]
    if cmd == "/discover":
        ac = tb.active_channels; asc = tb.active_scopes
        def fmt(d):
            rows = sorted(d.items(), key=lambda x: -(x[1].get("last", 0)))
            return [f"  #{k}  {v.get('n',0)}x, vor {rel_age(v.get('last'))}" for k, v in rows[:20]] or ["  (noch keine)"]
        return False, None, [f"aktive Kanaele ({len(ac)}):"] + fmt(ac) + [f"aktive Regionen ({len(asc)}):"] + fmt(asc)
    if cmd == "/clear":
        return False, "clear", []
    if cmd in ("/mute", "/unmute"):
        tb.muted = (cmd == "/mute")
        return False, None, ["Senden aus" if tb.muted else "Senden an"]
    if cmd in ("/ver", "/verbose"):
        tb.verbose = not tb.verbose
        return False, None, [f"verbose {'an — sent-Bestaetigungen + Adverts sichtbar' if tb.verbose else 'aus'}"]
    if cmd == "/monitor":
        tb.monitor = not tb.monitor
        return False, None, [f"Monitor {'an — alle Katalog-Kanaele werden angezeigt' if tb.monitor else 'aus'}"]
    return False, None, [f"unbekannt: {cmd} — /help zeigt alle Befehle"]

# ---------------------------------------------------------------- curses UI
def run_curses(stdscr, tb):
    curses.curs_set(1); stdscr.nodelay(True); stdscr.timeout(80)
    use_color = curses.has_colors()
    if use_color:
        curses.start_color(); curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)     # incoming
        curses.init_pair(2, curses.COLOR_GREEN, -1)    # ok
        curses.init_pair(3, curses.COLOR_YELLOW, -1)   # sys/info
        curses.init_pair(4, curses.COLOR_WHITE, -1)    # own
        curses.init_pair(5, curses.COLOR_RED, -1)      # alert
    CMAP = {"in": 1, "ok": 2, "sys": 3, "me": 4, "alert": 5}
    history = []            # (kind, line)
    inbuf = ""
    row_map = {}           # screen row -> history index (for click-to-scope)
    mouse_on = [True]
    def set_mouse(on):
        try:
            curses.mousemask(curses.BUTTON1_CLICKED if on else 0)
            mouse_on[0] = on
        except Exception:
            mouse_on[0] = False
    set_mouse(True)
    scroll = 0; filt = ""
    HIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_history.jsonl")
    def add(kind, line):
        history.append((kind, line))
        try:
            with open(HIST_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"k": kind, "l": line}, ensure_ascii=False) + "\n")
        except Exception:
            pass
    try:                                   # reload recent history so scrollback survives restarts
        with open(HIST_FILE, encoding="utf-8") as f:
            for ln in f.readlines()[-800:]:
                r = json.loads(ln); history.append((r["k"], r["l"]))
        if history: history.append(("sys", "— vorherige Sitzung —"))
    except Exception:
        pass
    def complete(s):
        """Tab completion -> (new_input, [hint_lines])."""
        if s.startswith("/") and " " not in s:                 # command name
            cands = [c for c in COMMANDS if c.startswith(s.lower())]
            if not cands: return s, []
            if len(cands) == 1: return cands[0] + " ", []
            pref = os.path.commonprefix(cands)
            return (pref if len(pref) > len(s) else s), ["  ".join(cands)]
        parts = s.split(" ", 1); cmd = parts[0].lower(); arg = parts[1] if len(parts) > 1 else ""
        pool = None
        if cmd in ("/join", "/channel"):
            pool = sorted(set([c.name for c in tb.channels] + [c.name for c in tb.found_channels]
                              + [n for n, _ in CAT_CHANNELS]))
        elif cmd in ("/scope", "/regions"):
            pool = [c for c, _ in CAT_REGIONS]
        if pool is None: return s, []
        hashd = arg.startswith("#"); raw = arg.lstrip("#").lower()
        cands = [n for n in pool if n.lower().startswith(raw)]
        if not cands: return s, []
        pre = os.path.commonprefix([n.lower() for n in cands])
        newarg = ("#" if hashd else "") + (cands[0] if len(cands) == 1 else pre)
        new = f"{parts[0]} {newarg}" + (" " if len(cands) == 1 else "")
        return new, ([] if len(cands) == 1 else ["  " + "  ".join(f"#{c}" for c in cands[:15])
                                                 + (" …" if len(cands) > 15 else "")])
    def redraw():
        H, W = stdscr.getmaxyx()
        stdscr.erase()
        mode = "RX-ONLY" if not tb.tx_ok else f"as {args.name}"
        scope = tb.scope or "unscoped"
        health = "" if tb.health == "ok" and not tb.restarts else f"  [{tb.health}"+(f" x{tb.restarts}" if tb.restarts else "")+"]"
        extra = (f"  [filter:{filt}]" if filt else "") + ("  [▲]" if scroll else "")
        status = (f" MeshCore SDR  {args.freq/1e6:.3f}MHz SF{args.sf}  {mode}  "
                  f"#{tb.active.name} -> {scope}  frames:{tb.heard}{health}{extra} ")
        stdscr.addnstr(0, 0, status.ljust(W), W, curses.A_REVERSE)
        # wrap history to fit; remember which history entry each screen row shows
        lines = []
        for hidx, (kind, txt) in enumerate(history):
            if filt and filt.lower() not in txt.lower(): continue
            for seg_i, seg in enumerate(textwrap.wrap(txt, max(10, W - 1)) or [""]):
                lines.append((kind, seg, hidx, seg_i))
        rows = H - 3
        sc = max(0, min(scroll, max(0, len(lines) - rows)))
        end = len(lines) - sc
        view = lines[max(0, end - rows):end]
        row_map.clear()
        for r, (kind, seg, hidx, seg_i) in enumerate(view, start=1):
            attr = curses.color_pair(CMAP.get(kind, 0)) if use_color else 0
            if kind in ("sys", "ok"): attr |= curses.A_DIM
            if kind == "alert": attr |= curses.A_BOLD
            stdscr.addnstr(r, 0, seg, W - 1, attr)
            row_map[r] = (hidx, seg_i)               # (history index, wrap-segment index)
        stdscr.hline(H - 2, 0, curses.ACS_HLINE, W)
        prompt = "> " if tb.tx_ok else "(rx-only) "
        stdscr.addnstr(H - 1, 0, (prompt + inbuf)[-(W - 1):], W - 1)
        stdscr.move(H - 1, min(len(prompt) + len(inbuf), W - 1))
        stdscr.refresh()
    history.append(("sys", "listening on the public channel — type to send, /help for commands"))
    redraw()
    while True:
        # drain radio events
        drained = False
        try:
            while True:
                kind, text = EVENTS.get_nowait()
                drained = True                       # Statuszeile (frames) aktuell halten
                if kind in ("ok", "sys") and not tb.verbose:
                    continue                          # passives Funk-Feedback nur bei /ver
                if kind == "in":
                    add("in", f"{now()}  {text}")
                    if args.name and args.name.lower() in text.lower():
                        try: curses.beep()
                        except Exception: pass
                elif kind == "ok": add("ok", f"{now()}  ✓ {text}")
                elif kind == "sys": add("sys", f"{now()}  · {text}")
                elif kind == "alert": add("alert", f"{now()}  ! {text}")
        except queue.Empty:
            pass
        try:
            ch = stdscr.get_wch()           # returns str (key) or int (special)
        except curses.error:                # timeout, no input
            if drained: redraw()
            continue
        if ch == "\x04":                    # Ctrl-D
            break
        elif ch == curses.KEY_MOUSE:        # click a line token -> adopt that part
            try: _id, mx, my, _z, _b = curses.getmouse()
            except curses.error: mx = my = -1
            rm = row_map.get(my)
            if rm is not None and history[rm[0]][0] == "in":
                hidx, seg_i = rm
                full = history[hidx][1]
                # walk the tokens left to right, tracking each one's column span in
                # the full line so we can tell which one the click (mx) landed on.
                # #scope [room] sender: text  (all on the first wrap segment)
                after = full[10:]                         # after "HH:MM:SS  "
                p = 10 + (len(after) - len(after.lstrip()))
                b = full[p:]
                if b[:1] in "+-" and "dB " in b[:8]:      # skip signal token "+52dB "
                    p += b.find("dB ") + 3
                    rest = full[p:]; p += len(rest) - len(rest.lstrip()); b = full[p:]
                was_scoped = b.startswith("#"); scope = None; scope_span = None
                if was_scoped:
                    stok = b.split(None, 1)[0]            # "#de-ni" or "#?ab12"
                    scope_span = (p, p + len(stok))
                    if not b.startswith("#?"): scope = stok[1:]
                    p += len(stok); rest = full[p:]; p += len(rest) - len(rest.lstrip()); b = full[p:]
                cname = None; room_span = None
                if b.startswith("["):
                    c = b.find("] ")
                    if c != -1:
                        cname = b[1:c]; room_span = (p, p + c + 1)
                        p += c + 2; rest = full[p:]; p += len(rest) - len(rest.lstrip()); b = full[p:]
                sender = None; sender_span = None
                if ": " in b:
                    sender = b.split(": ", 1)[0]; sender_span = (p, p + len(sender))
                # which token was clicked? (only the first segment carries tokens)
                col = mx if seg_i == 0 else -1
                def _in(sp): return sp is not None and sp[0] <= col < sp[1]
                if _in(scope_span):   do_scope, do_room, do_user = True, False, False
                elif _in(room_span):  do_scope, do_room, do_user = False, True, False
                elif _in(sender_span): do_scope, do_room, do_user = True, True, True
                else:                 do_scope, do_room, do_user = True, True, True  # text/wrap -> all
                notes = []
                if do_scope:
                    if scope is not None: tb.scope = scope; notes.append(f"#{scope}")
                    elif not was_scoped:  tb.scope = None; notes.append("unscoped")
                    else:                 notes.append("Scope unbekannt (unveraendert)")  # #?
                if do_room and cname:
                    pool = tb.channels + tb.found_channels + CAT_CH_OBJS
                    cobj = next((c for c in pool if c.name == cname), None)
                    if cobj is not None:
                        if not any(c.name == cobj.name for c in tb.channels): tb.channels.append(cobj)
                        tb.active = cobj; notes.append(f"[{cname}]")
                if do_user and sender and not inbuf.strip():
                    inbuf = f"{sender}: "; notes.append(f"an {sender}")
                if notes: add("sys", f"{now()}  · {' '.join(notes)} (per Klick)")
        elif ch in ("\n", "\r"):            # Enter
            line = inbuf.strip(); inbuf = ""; scroll = 0
            cline = line                    # detect a command even behind a "Name: " prefill
            if not line.startswith("/") and ": /" in line:
                tail = line.split(": ", 1)[1].lstrip()
                if tail.split(" ", 1)[0].lower() in COMMANDS: cline = tail
            if cline == "/mouse":
                set_mouse(not mouse_on[0])
                add("sys", f"{now()}  · Maus {'an (Klick=Scope, Text-Auswahl via Alt-Ziehen)' if mouse_on[0] else 'aus'}")
            elif cline.startswith("/filter"):
                filt = cline[7:].strip()
                add("sys", f"{now()}  · Filter {'= '+repr(filt) if filt else 'aus'}")
            elif cline.startswith("/"):
                try:
                    quit_, action, lines = handle_command(tb, cline)
                except Exception as e:
                    log_error("cmd " + cline, e)
                    quit_, action, lines = False, None, [f"Fehler: {e} (meshchat_errors.log)"]
                if quit_: break
                if action == "clear": history.clear()
                for l in lines: add("sys", f"{now()}  · {l}")
            elif line:
                err = tb.send(line)
                if err: add("sys", f"{now()}  · {err}")
                else:   add("me", f"{now()}  {args.name}: {line}")
        elif ch == curses.KEY_PPAGE:        # Bild hoch: zurueckscrollen
            H, _ = stdscr.getmaxyx(); scroll += max(1, (H - 3) // 2)
        elif ch == curses.KEY_NPAGE:        # Bild runter
            H, _ = stdscr.getmaxyx(); scroll = max(0, scroll - max(1, (H - 3) // 2))
        elif ch == curses.KEY_END:          # zurueck ans Live-Ende
            scroll = 0
        elif ch == "\t":                    # Tab: Autovervollstaendigung
            inbuf, hints = complete(inbuf)
            for h in hints: add("sys", f"{now()}  · {h}")
        elif ch == "\x15":                  # Ctrl-U: Eingabezeile leeren
            inbuf = ""
        elif ch in (curses.KEY_BACKSPACE, 127, 8, "\x7f", "\b"):
            inbuf = inbuf[:-1]
        elif ch == curses.KEY_RESIZE:
            pass
        elif isinstance(ch, str) and ch.isprintable():
            inbuf += ch
        redraw()

# ---------------------------------------------------------------- plain UI
def run_plain(tb):
    print(f"MeshCore public chat @ {args.freq/1e6:.3f} MHz as '{args.name}'. Type to send, /quit to exit.")
    stop = threading.Event()
    def printer():
        while not stop.is_set():
            try: kind, text = EVENTS.get(timeout=0.3)
            except queue.Empty: continue
            if kind in ("ok", "sys") and not tb.verbose: continue
            tag = {"in": "", "ok": "✓ ", "sys": "· ", "me": "", "alert": "! "}.get(kind, "")
            print(f"\r{now()}  {tag}{text}")
    threading.Thread(target=printer, daemon=True).start()
    try:
        for line in sys.stdin:
            line = line.rstrip("\n")
            if line.startswith("/"):
                quit_, action, lines = handle_command(tb, line)
                if quit_: break
                for l in lines: print(f"  · {l}")
            elif line:
                err = tb.send(line)
                print(f"  {'· '+err if err else '(sent) '+args.name+': '+line}")
    except (KeyboardInterrupt, EOFError):
        pass
    stop.set()

# ---------------------------------------------------------------- bruteforce
class BruteForcer(threading.Thread):
    """Background worker: index a wordlist (file OR the built-in generator) by
       channel_hash, then for each undecryptable channel packet try the words
       sharing that hash byte. Dedups per hash with a cooldown (cf. the old
       server-meshcore cracker) so uncrackable channels aren't retried forever."""
    COOLDOWN = 300   # seconds before retrying a hash that produced no hit
    ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-"
    def __init__(self, source, on_found, brute_len=0):
        super().__init__(daemon=True)
        self.source = source            # file path, or None for the built-in generator
        self.on_found = on_found
        self.brute_len = brute_len      # exhaustive stage max length (0 = off)
        self.q = queue.Queue(maxsize=500)
        self.index = {}; self.ready = False
        self.done = set()               # hashes already cracked
        self.failed_at = {}             # hash -> last no-hit time
    def _words(self):
        if self.source:
            with open(self.source, encoding="utf-8", errors="ignore") as fp:
                for line in fp:
                    w = line.strip().lstrip("#")
                    if w: yield w
        else:
            import wordlist_gen        # user's generator (~3.6M names)
            for w in wordlist_gen.build_extended_wordlist(): yield w
    def run(self):
        idx = {}; n = 0
        try:
            for w in self._words():
                key = meshcore.scope_key(w)                 # SHA256('#'+w)[:16]
                idx.setdefault(meshcore.chan_hash(key), []).append((w, key))
                n += 1
        except Exception as e:
            emit("alert", f"Wortlisten-Fehler: {e}"); return
        self.index = idx; self.ready = True
        emit("sys", f"Cracker bereit: {n} Namen indiziert")
        while True:
            chash, mac_bytes, cipher = self.q.get()
            hit = None
            for w, key in self.index.get(chash, ()):  # stage 1: wordlist (hash-filtered)
                if meshcore.mac(key + b"\x00" * 16, cipher) == mac_bytes and \
                   meshcore.looks_valid_grp(key, cipher):        # confirm, avoid MAC collisions
                    hit = (w, key); break
            if hit is None and self.brute_len > 0:     # stage 2: exhaustive, no wordlist
                name = self._exhaustive(chash, mac_bytes, cipher)
                if name: hit = (name, meshcore.scope_key(name))
            if hit:
                self.done.add(chash); self.failed_at.pop(chash, None)
                self.on_found(hit[0], hit[1], cipher)   # pass cipher to reveal the message
            else:
                self.failed_at[chash] = time.time()
    def _exhaustive(self, chash, mac_bytes, cipher):
        name = None
        cbin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel_crack")
        if os.path.exists(cbin):                 # native C cracker (OpenSSL + threads)
            try:
                import subprocess
                out = subprocess.run([cbin, f"{chash:02x}", mac_bytes.hex(), cipher.hex(),
                                      str(self.brute_len), str(os.cpu_count() or 4)],
                                     capture_output=True, text=True, timeout=6 * 3600).stdout
                for line in out.splitlines():
                    if line.startswith("RESULT:"): name = line.split("#", 1)[1].strip()
            except Exception:
                pass
        if name is None:
            try:
                import cracker                    # parallel Python fallback
                name = cracker.crack(chash, mac_bytes, cipher, self.brute_len)
            except Exception:
                name = None
        # strong re-check: reject MAC collisions (timestamp + "name: text")
        if name and not meshcore.looks_valid_grp(meshcore.scope_key(name), cipher):
            return None
        return name
    def submit(self, chash, mac_bytes, cipher):
        if not self.ready or chash in self.done: return
        last = self.failed_at.get(chash)
        if last and time.time() - last < self.COOLDOWN: return
        try: self.q.put_nowait((chash, mac_bytes, cipher))
        except queue.Full: pass

# ---------------------------------------------------------------- watchdog
def watchdog(tb, stop_event, stall_sec):
    """Restart the radio if the decoded-frame counter stops advancing."""
    last = tb.heard; last_change = time.time()
    while not stop_event.wait(15):
        h = tb.heard
        if h != last:
            last = h; last_change = time.time()
            if tb.health != "dead": tb.health = "ok"
            continue
        if time.time() - last_change > stall_sec:
            emit("alert", f"RX steht seit {int((time.time()-last_change)/60)} min — starte Funk neu")
            if tb.restart_worker():
                emit("alert", f"Funk neu gestartet (#{tb.restarts})")
            last_change = time.time()   # give it a fresh window either way

# ---------------------------------------------------------------- main
def main():
    try:
        tb = App()
    except RuntimeError as e:
        _err(f"\nSDR-Fehler: {e}\nIst der USRP angeschlossen? Pruefe mit 'uhd_find_devices'.\n")
        sys.exit(1)
    if args.wordlist or args.crack or args.monitor:   # monitor auto-enables cracking
        def _on_found(word, key, cipher=b""):
            if not any(c.name == word for c in tb.found_channels):
                tb.found_channels.append(meshcore.hashtag_channel(word))
                emit("alert", f"CHANNEL GEKNACKT: #{word} — ab jetzt lesbar")
                txt = meshcore.grp_text_from_cipher(key, cipher) if cipher else None
                if txt: emit("in", f"[{word}] {txt}  (aus Crack)")   # reveal the triggering message
                tb.save_state()          # persist immediately so a crash can't lose it
        tb.brute = BruteForcer(args.wordlist, _on_found, args.brute_len)
        tb.brute.start()
        emit("sys", f"Cracker startet ({args.wordlist or 'eingebauter Generator'}) — indiziere …")
    tb.start()
    time.sleep(1.2)
    stop_event = threading.Event()
    if args.watchdog > 0:
        threading.Thread(target=watchdog, args=(tb, stop_event, args.watchdog), daemon=True).start()
    def _autosave():                    # periodic snapshot so a crash loses ~nothing
        while not stop_event.wait(15): tb.save_state()
    threading.Thread(target=_autosave, daemon=True).start()
    try:
        if args.seconds:                       # headless capture mode
            print(f"headless: {args.seconds}s empfangen …", flush=True)
            t0 = time.time()
            while time.time() - t0 < args.seconds:
                try:
                    kind, text = EVENTS.get(timeout=0.5)
                    if kind in ("in", "alert"): print(f"{now()}  {text}", flush=True)
                except queue.Empty:
                    pass
        elif args.plain: run_plain(tb)
        else:          curses.wrapper(run_curses, tb)
    except KeyboardInterrupt:                   # Strg-C = sauberes Beenden, kein Absturz
        pass
    finally:
        stop_event.set()
        tb.save_state()
        tb.shutdown()
    print("bye.")

if __name__ == "__main__":
    main()
