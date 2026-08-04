# Patches

## `frame_sync-oob-crash.patch` — fixes the recurring `gr-lora_sdr` SIGBUS

`gr::lora_sdr::frame_sync_impl::general_work` crashed intermittently with a
SIGBUS in `_platform_memmove`. It happened most on a *quiet* mesh, because the
trigger is noise, not signal: random noise fakes a preamble, `frame_sync` locks
onto it with a garbage timing estimate (`k_hat`), and then reads out of bounds.

Root cause: `forecast()` only guaranteed **one** symbol of input
(`m_os_factor * (m_number_of_bins + 2)`), but `general_work()` peeks up to
**~two** symbols ahead (e.g. the additional-upchirp copy at
`in[os/2 + k_hat*os_factor .. + samples_per_symbol]`). A large `k_hat` from a
false preamble ran that read past the buffer end. A second copy could even index
*before* `in[0]` (`0.75*sps - k_hat*os_factor` going negative).

The patch:
1. `forecast()` now guarantees the worst-case 2-symbol window, so every forward
   `in[]` read is in bounds for any `k_hat` (which is bounded by the number of
   bins).
2. The negative index is clamped to 0.

Good signals have a small `k_hat` and are unaffected — verified with a pure
software TX->RX loopback: a message still round-trips through the patched
`frame_sync` byte-for-byte.

This is upstream commit `862746d` (tapparelj/gr-lora_sdr, the current HEAD — the
bug is in the latest release, not something an update fixes).

### Apply and rebuild

```bash
git clone https://github.com/tapparelj/gr-lora_sdr
cd gr-lora_sdr
git apply /path/to/patches/frame_sync-oob-crash.patch
mkdir build && cd build
cmake .. -DCMAKE_INSTALL_PREFIX=$(brew --prefix) -DCMAKE_BUILD_TYPE=Release \
  -Dpybind11_DIR="$(python3 -c 'import pybind11;print(pybind11.get_cmake_dir())')"
make -j$(sysctl -n hw.ncpu) && make install
```
