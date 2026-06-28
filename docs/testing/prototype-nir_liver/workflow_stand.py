"""Host-side driver for the nir_liver workflow stand test.

``Stand`` wraps the three things the test needs against a deployed stand:

* the HTTP API (httpx, logged in as the admin superuser),
* SSH/SCP into the VM (place synthetic files, run ``stand_tool.py``),
* small workflow helpers (wait for record states, submit data, inject a
  segmentation at the record's resolved on-disk path).

The whole suite is gated on ``STAND_URL`` (see ``conftest.py``); without it the
tests skip, mirroring ``deploy/test/acceptance``.

Configuration (env):
    STAND_URL              https://127.0.0.1:8443/nir_liver   (no trailing /api)
    STAND_ADMIN_PASSWORD   admin password
    STAND_SSH_TARGET       clarinet@<stand-ip>
    STAND_SSH_KEY          ~/.ssh/clarinet-vm
    STAND_KNOWN_HOSTS      dedicated known_hosts (optional)
    STAND_PACS_HTTP        http://localhost:8042  (on the VM; for anon cleanup)
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

STORAGE_ROOT = "/var/lib/clarinet/data"
VM_TOOL = "/tmp/stand_tool.py"
VM_PYTHON = "/opt/clarinet/venv/bin/python"
INSTALL_DIR = "/opt/clarinet"


class StandError(RuntimeError):
    """A stand operation failed (HTTP, SSH, or a workflow timeout)."""


@dataclass
class Stand:
    base_url: str  # .../nir_liver  (api is base_url + /api)
    admin_password: str
    ssh_target: str
    ssh_key: str
    known_hosts: str | None = None
    pacs_http: str = "http://localhost:8042"
    pacs_auth: str = "orthanc:orthanc"
    _client: httpx.Client = field(init=False)
    _admin_uuid: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._client = httpx.Client(verify=False, timeout=60.0)
        self.login()
        self._ship_tool()

    # ── HTTP ──────────────────────────────────────────────────────────

    @property
    def api(self) -> str:
        return f"{self.base_url.rstrip('/')}/api"

    def login(self) -> None:
        r = self._client.post(
            f"{self.api}/auth/login",
            data={"username": "admin@clarinet.ru", "password": self.admin_password},
        )
        if r.status_code != 204:
            raise StandError(f"login failed: {r.status_code} {r.text}")

    def _req(self, method: str, path: str, **kw: Any) -> httpx.Response:
        return self._client.request(method, f"{self.api}{path}", **kw)

    def get(self, path: str, **kw: Any) -> Any:
        r = self._req("GET", path, **kw)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, *, expect: int | tuple[int, ...] = 200, **kw: Any) -> httpx.Response:
        r = self._req("POST", path, **kw)
        _expect(r, expect)
        return r

    def patch(self, path: str, *, expect: int | tuple[int, ...] = 200, **kw: Any) -> httpx.Response:
        r = self._req("PATCH", path, **kw)
        _expect(r, expect)
        return r

    @property
    def admin_uuid(self) -> str:
        if self._admin_uuid is None:
            self._admin_uuid = self.get("/auth/me")["id"]
        return self._admin_uuid

    # ── SSH / SCP ─────────────────────────────────────────────────────

    def _ssh_base(self) -> list[str]:
        opts = ["-o", "StrictHostKeyChecking=no", "-i", self.ssh_key]
        if self.known_hosts:
            opts += ["-o", f"UserKnownHostsFile={self.known_hosts}"]
        return opts

    def ssh(self, command: str) -> str:
        cp = subprocess.run(
            ["ssh", *self._ssh_base(), self.ssh_target, command],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if cp.returncode != 0:
            raise StandError(f"ssh failed ({cp.returncode}): {command}\n{cp.stderr}")
        return cp.stdout

    def scp_to(self, local: str, remote: str) -> None:
        cp = subprocess.run(
            ["scp", *self._ssh_base(), local, f"{self.ssh_target}:{remote}"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if cp.returncode != 0:
            raise StandError(f"scp failed ({cp.returncode}): {local} → {remote}\n{cp.stderr}")

    def _ship_tool(self) -> None:
        self.scp_to(str(Path(__file__).with_name("stand_tool.py")), VM_TOOL)

    def tool(self, *args: str) -> dict:
        """Run a ``stand_tool.py`` subcommand on the VM, return its JSON line."""
        out = self.ssh(f"cd {INSTALL_DIR} && {VM_PYTHON} {VM_TOOL} {' '.join(map(str, args))}")
        # The tool logs to stderr; stdout carries exactly one JSON line.
        line = next(ln for ln in reversed(out.splitlines()) if ln.strip().startswith("{"))
        obj = json.loads(line)
        if "error" in obj:
            raise StandError(f"stand_tool {' '.join(map(str, args))} → {obj['error']}")
        return obj

    # ── records ───────────────────────────────────────────────────────

    def find(
        self,
        *,
        record_type_name: str | None = None,
        patient_id: str | None = None,
        study_uid: str | None = None,
    ) -> list[dict]:
        """Search records via the paginated ``/records/find`` endpoint.

        Filter keys mirror ``RecordSearchFilter`` (``record_type_name`` /
        ``patient_id`` / ``study_uid``); the cursor is followed to completion.
        """
        filt = {
            k: v
            for k, v in {
                "record_type_name": record_type_name,
                "patient_id": patient_id,
                "study_uid": study_uid,
            }.items()
            if v is not None
        }
        items: list[dict] = []
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {**filt, "limit": 1000}
            if cursor:
                body["cursor"] = cursor
            page = self.post("/records/find", json=body).json()
            items.extend(page["items"])
            cursor = page.get("next_cursor")
            if not cursor:
                return items

    def record(self, record_id: int) -> dict:
        return self.get(f"/records/{record_id}")

    def one(
        self, name: str, *, study_uid: str | None = None, patient_id: str | None = None
    ) -> dict:
        """Return the single record of *name*; raise if zero or many match."""
        hits = self.find(record_type_name=name, patient_id=patient_id, study_uid=study_uid)
        if len(hits) != 1:
            raise StandError(f"expected exactly one '{name}', got {len(hits)}")
        return hits[0]

    def wait_record(
        self,
        name: str,
        status: str,
        *,
        patient_id: str | None = None,
        timeout: float = 90.0,
        poll: float = 3.0,
    ) -> dict:
        """Poll until a record of *name* reaches *status*; return it."""
        deadline = time.monotonic() + timeout
        last: list[dict] = []
        while time.monotonic() < deadline:
            last = self.find(record_type_name=name, patient_id=patient_id)
            for r in last:
                if r["status"] == status:
                    return r
            time.sleep(poll)
        states = ", ".join(f"{r['id']}:{r['status']}" for r in last) or "none"
        raise StandError(f"timeout waiting for '{name}' → {status} (saw: {states})")

    def submit_data(self, record_id: int, data: dict | None = None) -> dict:
        return self.post(f"/records/{record_id}/data", json=data or {}).json()

    def wait_record_data(
        self, record_id: int, key: str, *, timeout: float = 60.0, poll: float = 2.0
    ) -> dict:
        """Poll until a record's ``data[key]`` is populated, return ``data``.

        Prefill tasks (``prefill_*`` on the ``pending`` status) run asynchronously
        after the record reaches ``pending``, so a record can be ``pending`` with
        ``data`` still ``None`` for a beat. Read the form only once prefill lands.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.record(record_id).get("data")
            if data and data.get(key):
                return data
            time.sleep(poll)
        raise StandError(f"record {record_id}: data[{key!r}] never populated")

    def assign_admin(self, record_id: int) -> None:
        # user_id is a query parameter on this endpoint, not a body field.
        self.patch(f"/records/{record_id}/user", params={"user_id": self.admin_uuid})

    # ── synthetic file injection ──────────────────────────────────────

    def resolve(self, record_id: int, file_name: str) -> str:
        return self.tool("resolve", record_id, file_name)["path"]

    def volume_path(self, study_uid: str) -> str:
        """On-disk volume.nii.gz of a study's create-nifty record."""
        nifty = self.one("create-nifty", study_uid=study_uid)
        return self.resolve(nifty["id"], "volume_nifti")

    def submit_segmentation(
        self, record_id: int, ref_volume: str, classes: str = "mts,unclear", seed: int = 1
    ) -> dict:
        """Assign the admin, synthesise a lesion seg at the user-specific path,
        then submit — order matters: the seg file name embeds ``{user_id}`` and
        ``init_master_model`` reads it at ``on_finished``, so the file must be
        present at the resolved (post-assignment) path before submit."""
        self.assign_admin(record_id)
        self.tool(
            "make-seg",
            record_id,
            "segmentation",
            "--ref-nifti",
            ref_volume,
            "--classes",
            classes,
            "--seed",
            seed,
        )
        return self.submit_data(record_id)

    def inject_blob_seg(
        self, record_id: int, file_name: str, ref_volume: str, label: int = 1
    ) -> str:
        return self.tool(
            "make-blob-seg", record_id, file_name, "--ref-nifti", ref_volume, "--label", label
        )["path"]

    def inject_liver(self, record_id: int, ref_volume: str) -> str:
        return self.tool("make-liver", record_id, "liver_auto", "--ref-nifti", ref_volume)["path"]

    # ── reset (idempotent setup) ──────────────────────────────────────

    def reset_patient(self, patient_id: str, study_uid: str) -> None:
        """Delete every trace of a prior run so the test is re-runnable.

        Records (admin cascade) → study (+series) → patient → on-disk working
        dir → anonymised copies in Orthanc. All best-effort: a clean stand
        simply finds nothing to delete.
        """
        anon_id = self._patient_anon_id(patient_id)  # capture before the patient is deleted
        for _ in range(40):  # records form a forest, not one tree — loop until empty
            recs = self.find(patient_id=patient_id)
            if not recs:
                break
            for r in recs:
                self._req("DELETE", f"/admin/records/{r['id']}")
        self._req("DELETE", f"/studies/{study_uid}")
        self._req("DELETE", f"/patients/{patient_id}")
        self.ssh(f"rm -rf {STORAGE_ROOT}/{patient_id}")
        if anon_id:
            self._purge_anon_studies(anon_id)

    def _patient_anon_id(self, patient_id: str) -> str | None:
        """This patient's derived anon PatientID (``<prefix>_<auto_id>``), or None
        if the patient does not exist yet (first run)."""
        r = self._req("GET", f"/patients/{patient_id}")
        return r.json().get("anon_id") if r.status_code == 200 else None

    def _purge_anon_studies(self, anon_id: str) -> None:
        # Anonymised copies (anon_send_to_pacs=true) land back in Orthanc under
        # this patient's derived anon PatientID — drop only those so reruns don't
        # accumulate. Match the exact ID, not the project-wide prefix: a shared
        # PACS may also hold other patients' anon studies under the same prefix.
        script = (
            "import json,urllib.request,base64;"
            f"auth=base64.b64encode(b'{self.pacs_auth}').decode();"
            "req=lambda m,u,d=None:urllib.request.urlopen("
            "urllib.request.Request(u,data=d,method=m,"
            "headers={'Authorization':'Basic '+auth,'Content-Type':'application/json'}));"
            f"sids=json.load(req('GET','{self.pacs_http}/studies'));"
            "[req('DELETE',f'" + self.pacs_http + "/studies/'+s) for s in sids "
            f"if (lambda t: t.get('PatientID','')=='{anon_id}')("
            "json.load(req('GET',f'" + self.pacs_http + "/studies/'+s))['PatientMainDicomTags'])]"
        )
        with contextlib.suppress(StandError):  # cleanup is best-effort
            self.ssh(f'{VM_PYTHON} -c "{script}"')

    def close(self) -> None:
        self._client.close()


def _expect(r: httpx.Response, expect: int | tuple[int, ...]) -> None:
    codes = (expect,) if isinstance(expect, int) else expect
    if r.status_code not in codes:
        raise StandError(f"{r.request.method} {r.request.url} → {r.status_code} {r.text}")
