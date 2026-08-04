#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Part of meshcore-sdr-chat.
#
# SDR worker: runs the GNU Radio / gr-lora_sdr flowgraph in its OWN process and
# talks to the parent over stdin/stdout, so a native crash in gr-lora_sdr's
# frame_sync (a known SIGBUS bug) kills only this process — the parent respawns
# it and the UI never dies.
#
#   stdout: one JSON line per decoded frame:  {"f": "<hex>", "s": <sig_dbfs>}
#   stdin:  one JSON line per command:        {"tx": "<hex>"}   (transmit)
import os, sys, json, argparse, threading, math
os.environ.setdefault("UHD_LOG_CONSOLE_LEVEL", "off")
import numpy as np
from gnuradio import gr, blocks, uhd, lora_sdr
try: gr.logging().set_default_level(gr.log_levels.off)   # keep GNU Radio quiet
except Exception: pass
import pmt

ap = argparse.ArgumentParser()
ap.add_argument("--freq", type=float, default=869.618e6)
ap.add_argument("--bw", type=float, default=62500); ap.add_argument("--sf", type=int, default=8)
ap.add_argument("--cr", type=int, default=4); ap.add_argument("--sync", type=lambda x: int(x, 0), default=0x12)
ap.add_argument("--rxgain", type=float, default=65); ap.add_argument("--txgain", type=float, default=89)
ap.add_argument("--osf", type=int, default=4); ap.add_argument("--ant", default="TX/RX")
ap.add_argument("--device", default=""); ap.add_argument("--sdr", default="uhd")
ap.add_argument("--soapy-driver", default="rtlsdr"); ap.add_argument("--soapy-tx-driver", default=None)
ap.add_argument("--rx-only", action="store_true")
A = ap.parse_args()
PLUTO_MIN = 521e3
if A.sdr == "soapy" and "pluto" in (A.soapy_driver + (A.soapy_tx_driver or "")).lower():
    while A.bw * A.osf < PLUTO_MIN: A.osf *= 2
RATE = A.bw * A.osf
RX_ONLY_SOAPY = {"rtlsdr", "rtltcp", "airspy", "airspyhf", "sdrplay", "miri"}
if A.sdr == "soapy" and (A.soapy_tx_driver or A.soapy_driver) in RX_ONLY_SOAPY:
    A.rx_only = True

def make_source():
    if A.sdr == "soapy":
        from gnuradio import soapy
        s = soapy.source(f"driver={A.soapy_driver}", "fc32", 1, "", "", "", "")
        s.set_sample_rate(0, RATE); s.set_frequency(0, A.freq); s.set_gain_mode(0, False); s.set_gain(0, A.rxgain)
        return s
    s = uhd.usrp_source(A.device, uhd.stream_args(cpu_format="fc32", channels=[0]))
    s.set_samp_rate(RATE); s.set_center_freq(A.freq, 0); s.set_gain(A.rxgain, 0); s.set_antenna(A.ant, 0)
    return s

def make_sink():
    if A.sdr == "soapy":
        from gnuradio import soapy
        drv = A.soapy_tx_driver or A.soapy_driver
        s = soapy.sink(f"driver={drv}", "fc32", 1, "", "", [""], [""])
        s.set_sample_rate(0, RATE); s.set_frequency(0, A.freq); s.set_gain(0, A.txgain)
        return s
    s = uhd.usrp_sink(A.device, uhd.stream_args(cpu_format="fc32", channels=[0]))
    s.set_samp_rate(RATE); s.set_center_freq(A.freq, 0); s.set_gain(A.txgain, 0); s.set_antenna(A.ant, 0)
    return s

_out_lock = threading.Lock()
def emit(obj):
    with _out_lock:
        sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()

class RawFrameSink(gr.sync_block):
    """Extract each decoded LoRa frame + an approximate signal level and forward
       it to the parent. No MeshCore parsing here — that lives in the parent."""
    def __init__(self, probe):
        gr.sync_block.__init__(self, "rawframesink", [np.uint8], None)
        self.probe = probe; self.buf = bytearray(); self.need = 0
        self._floor = None; self._peak = 0.0; self.muted = False
    def _sig(self):
        p = self._peak; f = self._floor or p or 1e-12
        return (round(10 * math.log10(p + 1e-12), 1), round(10 * math.log10((p + 1e-12) / (f + 1e-12)), 1))
    def work(self, input_items, output_items):
        # The ENTIRE body is guarded: a Python exception escaping work() becomes a
        # pybind11 error_already_set in the C++ scheduler and segfaults the process
        # on teardown (Python 3.14). During full-duplex TX the B210 hears its own
        # burst and frame_sync can emit malformed tags -> this must never throw out.
        inp = input_items[0]
        if self.muted:                 # our own TX burst: drop it untouched, never
            return len(inp)            # run the tag/emit path (would crash on 3.14)
        try:
            try:
                p = self.probe.level()
                if self._floor is None: self._floor = p
                elif p < self._floor: self._floor = self._floor * 0.999 + p * 0.001
                else: self._floor = self._floor + (p - self._floor) * 0.0002
                self._peak = max(self._peak * 0.6, p)
            except Exception:
                pass
            for t in self.get_tags_in_window(0, 0, len(inp), pmt.intern("frame_info")):
                try: pl = pmt.to_long(pmt.dict_ref(t.value, pmt.intern("pay_len"), pmt.from_long(0)))
                except Exception: pl = 0
                if 0 < pl <= 512: self.need = pl; self.buf = bytearray()   # bound: reject junk pay_len
            self.buf.extend(bytes(inp))
            while self.need and len(self.buf) >= self.need:
                frame = bytes(self.buf[:self.need]); self.buf = self.buf[self.need:]; self.need = 0
                try:
                    s, n = self._sig(); emit({"f": frame.hex(), "s": s, "n": n})
                except Exception: pass
        except Exception:
            pass                # swallow EVERYTHING — never let it reach pybind11
        return len(inp)

class Worker(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self)
        src = make_source()
        # RX gate: closed during our own TX burst so frame_sync never has to
        # process the near-field self-signal. Full-duplex self-reception makes
        # frame_sync choke and raise a Python exception that segfaults the block
        # thread (Python 3.14 + pybind teardown). Half-duplexing avoids it.
        self.gate = blocks.copy(gr.sizeof_gr_complex); self.gate.set_enabled(True)
        rx = lora_sdr.lora_sdr_lora_rx(
            center_freq=int(A.freq), bw=int(A.bw), cr=A.cr, has_crc=True, impl_head=False,
            pay_len=255, samp_rate=int(RATE), sf=A.sf, sync_word=[A.sync],
            soft_decoding=False, ldro_mode=2, print_rx=[False, False])
        mag = blocks.complex_to_mag_squared()
        ma = blocks.moving_average_ff(2048, 1.0 / 2048, 4000)
        self.probe = blocks.probe_signal_f()
        self.rfs = RawFrameSink(self.probe)
        self.connect(src, mag, ma, self.probe)              # power tap stays live
        self.connect(src, self.gate, rx, self.rfs)
        self.tx_ok = not A.rx_only
        if self.tx_ok:
            self.wh = lora_sdr.whitening(True, False, ',', 'packet_len')
            mod = lora_sdr.modulate(A.sf, int(RATE), int(A.bw), [A.sync], 128, 8)
            il = lora_sdr.interleaver(A.cr, A.sf, 2, int(A.bw)); hd = lora_sdr.header(False, True, A.cr)
            he = lora_sdr.hamming_enc(A.cr, A.sf); gd = lora_sdr.gray_demap(A.sf); ac = lora_sdr.add_crc(True)
            self.connect(self.wh, hd, ac, he, il, gd, mod, make_sink())
    def transmit(self, hexstr):
        if not self.tx_ok: return
        try:
            self.rfs.muted = True               # stop the Python sink from touching
            self.gate.set_enabled(False)        # our own burst (half-duplex)
            self.wh.to_basic_block()._post(pmt.intern("msg"), pmt.intern(hexstr))
        except Exception as e:
            emit({"txerr": str(e)})
        t = threading.Timer(1.2, self._unmute); t.daemon = True; t.start()
    def _unmute(self):
        try: self.gate.set_enabled(True); self.rfs.muted = False
        except Exception: pass

def main():
    tb = Worker(); tb.start()
    emit({"ready": True, "tx": tb.tx_ok, "rate": RATE})
    try:
        for line in sys.stdin:                 # commands from the parent (TX)
            line = line.strip()
            if not line: continue
            try: cmd = json.loads(line)
            except Exception: continue
            if "tx" in cmd: tb.transmit(cmd["tx"])
            elif cmd.get("quit"): break
    except KeyboardInterrupt:
        pass
    tb.stop(); tb.wait()

if __name__ == "__main__":
    main()
