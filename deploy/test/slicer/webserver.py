"""Prepare a headless 3D Slicer for the Clarinet test suite.

Executed inside Slicer:  Slicer --no-splash --python-script webserver.py
(run under Xvfb with a full GUI — NOT --no-main-window, which leaves
``layoutManager()`` None and breaks the slice-widget tests).

It does, programmatically, the four things a user would otherwise do by hand in
the GUI so that the `slicer` / `dicom` marked tests can talk to this instance:

1. **Web Server** — start the built-in module's ``WebServerLogic`` with
   ``enableExec=True`` (REQUIRED: ``SlicerRequestHandler`` gates ``/slicer/exec``
   on it). It binds ``server_address=("", port)`` → ``0.0.0.0``, so the same
   instance is reachable both on ``localhost`` (tests) and on the host's other
   interfaces (e.g. a VM doing a C-MOVE back to it).
2. **DICOM database** — a headless Slicer leaves ``slicer.dicomDatabase`` closed,
   so C-GET / C-MOVE retrievals report success yet index 0 files. Open it.
3. **PACS server in QSettings** — ``PacsHelper.from_slicer()`` reads
   ``DICOM/ServerNodes/*`` and the global ``CallingAETitle`` (the "Application
   Settings > DICOM" data). Seed one server so it resolves.
4. **Storage SCP listener** — ``DICOMListener`` on the storage port, so C-MOVE
   deliveries are received and indexed into the database.

The process stays alive as a daemon: ``slicer.util.exit()`` is never called, so
the Qt event loop keeps the QSocketNotifier-driven HTTP server serving.

Configuration (env vars, all optional — defaults suit a local Orthanc):

    CLARINET_TEST_SLICER_PORT      Web Server port              (default 2016)
    CLARINET_SLICER_PACS_HOST      PACS host seeded in QSettings (default localhost)
    CLARINET_SLICER_PACS_PORT      PACS DIMSE port              (default 4242)
    CLARINET_SLICER_PACS_AET       PACS called AE title         (default ORTHANC)
    CLARINET_SLICER_CALLING_AET    Slicer's own AE title        (default SLICER_TEST)
    CLARINET_SLICER_SCP_PORT       storage SCP listen port      (default 4006)
    CLARINET_SLICER_DICOM_DB       DICOM database path          (default ~/Documents/SlicerDICOMDatabase/ctkDICOM.sql)
"""

import json
import os

import qt
import slicer

import WebServer

PORT = int(os.environ.get("CLARINET_TEST_SLICER_PORT", "2016"))

# --- (2) open the DICOM database (headless leaves it closed) -----------------
DB_PATH = os.environ.get(
    "CLARINET_SLICER_DICOM_DB",
    os.path.expanduser("~/Documents/SlicerDICOMDatabase/ctkDICOM.sql"),
)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
slicer.dicomDatabase.openDatabase(DB_PATH)

# --- (4) storage SCP listener for C-MOVE deliveries --------------------------
# C-GET indexes synchronously via ctkDICOMRetrieve.setDatabase, but C-MOVE has
# the PACS push to our storescp; DICOMListener indexes those incoming files.
# StoragePort must match the AET the tests register in the PACS.
import DICOMLib  # noqa: E402

SCP_PORT = int(os.environ.get("CLARINET_SLICER_SCP_PORT", "4006"))
qt.QSettings().setValue("StoragePort", str(SCP_PORT))
_listener = DICOMLib.DICOMListener(slicer.dicomDatabase)
_listener.delayedAutoUpdateTimer.setInterval(300)  # shrink the 10s default
_listener.start()
slicer.modules._clarinet_dicom_listener = _listener  # keep a ref so it isn't GC'd

# --- (3) seed a PACS server in QSettings for PacsHelper.from_slicer() ---------
PACS_HOST = os.environ.get("CLARINET_SLICER_PACS_HOST", "localhost")
PACS_PORT = int(os.environ.get("CLARINET_SLICER_PACS_PORT", "4242"))
PACS_AET = os.environ.get("CLARINET_SLICER_PACS_AET", "ORTHANC")
CALLING_AET = os.environ.get("CLARINET_SLICER_CALLING_AET", "SLICER_TEST")
_node = {
    "Name": "ORTHANC",
    "Address": PACS_HOST,
    "Port": PACS_PORT,
    "Called AETitle": PACS_AET,
    "Calling AETitle": CALLING_AET,
    "QueryRetrieveCheckState": 2,  # Qt.Checked → picked by from_slicer()
    "StorageCheckState": 0,
    "Retrieve Protocol": "CGET",
}
_s = qt.QSettings()
_s.setValue("DICOM/ServerNodeCount", 1)
_s.setValue("DICOM/ServerNodes/0", json.dumps(_node))
_s.setValue("CallingAETitle", CALLING_AET)
_s.sync()

# --- (1) start the Web Server with the exec endpoint -------------------------
logic = WebServer.WebServerLogic(
    port=PORT,
    enableExec=True,
    enableSlicer=True,
    enableDICOM=True,
    enableStaticPages=True,
)
logic.start()

# findFreePort() silently moves off PORT if it's busy — surface the real port so
# the launcher can detect a port clash instead of a silently-wrong endpoint.
print(
    f"WEBSERVER_STARTED requested={PORT} actual={logic.port} "
    f"dicomdb_open={bool(slicer.dicomDatabase.isOpen)} pacs={PACS_HOST}:{PACS_PORT}",
    flush=True,
)
