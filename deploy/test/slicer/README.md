# Headless 3D Slicer for the test suite

The `slicer` / `dicom` marked tests (`tests/integration/test_slicer_*.py`,
`tests/e2e/test_slicer_pacs_workflow.py`) talk to a **running** 3D Slicer over
its Web Server (`POST /slicer/exec`). Slicer is an external dependency — the
tests connect to it, they don't start it.

This directory lets you run that Slicer **headless** (no physical display) so
the full `make test-all-stages` pipeline can exercise the slicer stage on a
server / CI box / WSL instead of skipping it.

| File | Role |
|---|---|
| `run-headless.sh` | locate Slicer, launch it full-GUI under Xvfb, start the Web Server, verify `/slicer/exec`. `--stop` to tear down. |
| `webserver.py` | runs *inside* Slicer: Web Server (`enableExec`), open DICOM db, seed PACS in QSettings, start storage SCP listener. |

## Prerequisites (one-time)

1. **3D Slicer** (Linux build). Use the same version your clinicians run for
   parity. Download from <https://download.slicer.org> and unpack, e.g.:
   ```bash
   curl -fsSL "https://download.slicer.org/find?os=linux&stability=release" | jq -r .download_url   # -> /bitstream/...
   curl -fsSL "https://download.slicer.org/bitstream/<id>" -o /tmp/slicer.tar.gz
   tar -xzf /tmp/slicer.tar.gz -C "$HOME"          # -> ~/Slicer-X.Y.Z-linux-amd64
   ```
2. **System libraries** the Slicer Qt build needs (Debian/Ubuntu):
   ```bash
   sudo apt-get install -y xvfb \
     libpulse-mainloop-glib0 libpcre2-16-0 \
     libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
     libxcb-shape0 libxcb-util1 libxcb-xinerama0 libxcb-xkb1 libxkbcommon-x11-0
   ```
   (Slicer ships its own OpenSSL 1.1; mesa software GL / llvmpipe is enough — no
   GPU required. If `./Slicer --version` reports a missing `lib*.so`, install the
   matching `-dev`-less package and retry.)

## Usage

```bash
SLICER_HOME=~/Slicer-5.10.0-linux-amd64 bash deploy/test/slicer/run-headless.sh
# ... run slicer tests against it ...
bash deploy/test/slicer/run-headless.sh --stop
```

`run-headless.sh` finds Slicer via `$SLICER_HOME/Slicer`, else the newest
`~/Slicer-*/Slicer`, `/opt/Slicer-*/Slicer`, `~/Slicer/Slicer`,
`/opt/Slicer/Slicer`. It **exits non-zero with install hints** when Slicer or
Xvfb is missing or the Web Server fails to come up — that is what makes
`make test-all-stages` fail early (Stage 1) rather than waste ~15 min and then
skip the slicer stage.

## In `make test-all-stages`

Stage 1 launches the headless Slicer and **fails the whole run early** if it
can't. Stage 8 stops it. Opt out with `SKIP_SLICER=1` (then the slicer stage
auto-skips as before — no Slicer required).

Point the tests at the instance and (optionally) a PACS via env:

```bash
SLICER_HOME=~/Slicer-5.10.0-linux-amd64 \
CLARINET_TEST_SLICER_HOST=localhost \
CLARINET_TEST_PACS_HOST=<orthanc-host> \
make test-all-stages
```

### Config (env vars)

| Var | Default | Used by |
|---|---|---|
| `SLICER_HOME` | autodetect | `run-headless.sh` (Slicer location) |
| `CLARINET_TEST_SLICER_HOST` | `localhost` | tests + launcher verify |
| `CLARINET_TEST_SLICER_PORT` | `2016` | Web Server port |
| `CLARINET_SLICER_PACS_HOST` / `_PORT` / `_AET` | `localhost` / `4242` / `ORTHANC` | PACS seeded in Slicer QSettings |
| `CLARINET_SLICER_CALLING_AET` | `SLICER_TEST` | Slicer's own AE title |
| `CLARINET_SLICER_SCP_PORT` | `4006` | storage SCP listen port (C-MOVE) |

## Notes / limitations

- Slicer is not a service — re-run `run-headless.sh` after a reboot.
- The Web Server binds `0.0.0.0`, so a PACS on another host can C-MOVE back to
  `CLARINET_TEST_SLICER_HOST:<SCP_PORT>`.
- C-MOVE-to-Slicer retrieval (`DICOMListener` indexing of storescp deliveries)
  is unreliable under Xvfb; that one test self-skips on a headless Slicer. C-GET
  retrieval is unaffected.
