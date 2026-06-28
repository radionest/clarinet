"""End-to-end nir_liver workflow simulation against a deployed stand.

Walks the full record DAG from study arrival to pathomorphology. Automated
stages run for real on the stand (DICOM anonymisation via C-GET from Orthanc,
DICOM→NIfTI conversion, master-model build, projection, comparison, MDK
auto-creation, resection merge). Human / Slicer stages are simulated: the test
synthesises geometry-consistent ``.seg.nrrd`` / mask files at each record's
resolved on-disk path and submits via ``POST /data`` (which, unlike ``/submit``,
does not invoke the Slicer validator).

The GPU stage (TotalSegmentator) has no worker on the stand; its ``liver.nii.gz``
output is injected directly so anatomy-model can unblock. The parallel anon /
archive segmentation branches are intentionally left pending — they are not on
the main spine and do not gate MDK or anything downstream.

Run (against the local tunnelled stand):

    STAND_URL=https://127.0.0.1:8443/nir_liver \\
    STAND_ADMIN_PASSWORD=... \\
    STAND_SSH_TARGET=clarinet@<stand-ip> \\
    STAND_SSH_KEY=~/.ssh/clarinet-vm \\
    STAND_KNOWN_HOSTS=~/.local/share/clarinet-deploy/known_hosts \\
    uv run pytest docs/testing/prototype-nir_liver/ -v
"""

from __future__ import annotations

import pytest
from workflow_stand import Stand

pytestmark = pytest.mark.stand


def _best_series(series: list[dict]) -> str:
    """Pick the series with the most instances (the workflow's best_series)."""
    return max(series, key=lambda s: s.get("instance_count", 0))["series_uid"]


def test_full_workflow(stand: Stand, demo_patient: tuple[str, dict]) -> None:
    patient_id, demo = demo_patient
    study_uid = demo["study_uid"]

    # ── Stage 0: clean slate ──────────────────────────────────────────
    stand.reset_patient(patient_id, study_uid)

    # ── Stage 1: patient + study arrival → first-check ────────────────
    stand.post(
        "/patients",
        json={"patient_id": patient_id, "patient_name": demo["patient_name"]},
        expect=201,
    )
    imported = stand.post(
        "/dicom/import-study",
        json={"study_instance_uid": study_uid, "patient_id": patient_id},
    ).json()
    assert imported["series"], "import returned no series"
    best_series = _best_series(imported["series"])

    first_check = stand.wait_record("first-check", "pending", patient_id=patient_id, timeout=30)

    # ── Stage 2: first-check accepted → anonymize + create-nifty (real DICOM) ──
    stand.submit_data(
        first_check["id"],
        {"is_good": True, "study_type": "CT", "best_series": best_series},
    )
    stand.wait_record("anonymize-study", "finished", patient_id=patient_id, timeout=120)
    stand.wait_record("create-nifty", "finished", patient_id=patient_id, timeout=120)
    volume = stand.volume_path(study_uid)

    # ── Stage 3: prospective CT segmentation (human) → master model cascade ──
    prospective = stand.wait_record(
        "segment-prospective-ct", "pending", patient_id=patient_id, timeout=30
    )
    stand.submit_segmentation(prospective["id"], volume, classes="mts,unclear")

    # master-model build → auto-projection (CT) → comparison, all automated
    stand.wait_record("create-master-projection", "finished", patient_id=patient_id, timeout=60)
    comparison = stand.wait_record(
        "compare-with-projection", "finished", patient_id=patient_id, timeout=60
    )
    cdata = stand.record(comparison["id"])["data"]
    # Doctor seg == the seg the master model was built from → a clean match,
    # so no second-review (FN) or update-master-model (FP) branch is spawned.
    assert cdata.get("false_negative_num") == 0, cdata
    assert cdata.get("false_positive_num") == 0, cdata

    # ── Stage 4: MDK auto-created once the modality assessment is complete ──
    mdk = stand.wait_record("mdk-conclusion", "pending", patient_id=patient_id, timeout=60)

    # Inject the GPU liver mask first so anatomy-model starts pending (not blocked)
    # when MDK submission creates it.
    auto_liver = stand.one("auto-liver", patient_id=patient_id)
    stand.inject_liver(auto_liver["id"], volume)

    mdk_data = stand.wait_record_data(mdk["id"], "lesions")
    for lesion in mdk_data["lesions"]:
        lesion["classification"] = "metastasis"
    mdk_data["conclusion_text"] = "Stand workflow MDK"
    stand.submit_data(mdk["id"], mdk_data)

    # ── Stage 5: anatomy model (human) → resection plan ───────────────
    anatomy = stand.wait_record("anatomy-model", "pending", patient_id=patient_id, timeout=60)
    stand.inject_blob_seg(anatomy["id"], "anatomy_model_file", volume, label=1)
    stand.submit_data(anatomy["id"])

    # ── Stage 6: resection plan (human) → combine + resection report ──
    plan = stand.wait_record("resection-plan", "pending", patient_id=patient_id, timeout=60)
    stand.inject_blob_seg(plan["id"], "resection_clusters", volume, label=1)
    stand.submit_data(plan["id"])

    stand.wait_record("combine-resection", "finished", patient_id=patient_id, timeout=60)
    report = stand.wait_record("resection-report", "pending", patient_id=patient_id, timeout=60)

    # Prefill already maps every lesion to its overlapping cluster — submit as-is.
    report_data = stand.wait_record_data(report["id"], "lesions")
    assert all(les.get("cluster") for les in report_data["lesions"]), report_data
    stand.submit_data(report["id"], report_data)

    # ── Stage 7: post-op resection (human; output auto-built) → pathomorphology ──
    postop = stand.wait_record("postop-resection", "pending", patient_id=patient_id, timeout=60)
    # init_postop_resection (on pending) builds resection.seg.nrrd from the report;
    # wait for it to exist, then submit.
    postop_out = stand.resolve(postop["id"], "resection_file")
    _wait_file(stand, postop_out, timeout=60)
    stand.submit_data(postop["id"])

    patho = stand.wait_record("pathomorphology", "pending", patient_id=patient_id, timeout=60)
    patho_data = stand.wait_record_data(patho["id"], "lesions")
    for lesion in patho_data["lesions"]:
        lesion.update(name="metastasis", size_mm=10, mandard_trg="TRG3", margin_status="gt5mm")
    stand.submit_data(patho["id"], patho_data)
    stand.wait_record("pathomorphology", "finished", patient_id=patient_id, timeout=30)

    # ── Final: the whole spine is finished ────────────────────────────
    finished = {
        r["record_type_name"]
        for r in stand.find(patient_id=patient_id)
        if r["status"] == "finished"
    }
    spine = {
        "first-check",
        "anonymize-study",
        "create-nifty",
        "segment-prospective-ct",
        "create-master-projection",
        "compare-with-projection",
        "mdk-conclusion",
        "anatomy-model",
        "resection-plan",
        "combine-resection",
        "resection-report",
        "postop-resection",
        "pathomorphology",
    }
    assert spine <= finished, f"missing from finished spine: {spine - finished}"


def _wait_file(stand: Stand, path: str, *, timeout: float = 60.0, poll: float = 3.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stand.ssh(f"test -s '{path}' && echo OK || true").strip() == "OK":
            return
        time.sleep(poll)
    raise AssertionError(f"file never appeared: {path}")
