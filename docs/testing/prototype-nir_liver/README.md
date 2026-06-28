# Workflow stand test

End-to-end simulation of the **entire nir_liver record DAG** against a *deployed*
stand — from study arrival to pathomorphology. It drives the public HTTP API
exactly as the frontend would, and uses SSH only to do what a human + 3D Slicer
would otherwise do on the box (drop segmentation files on disk).

## What is real vs simulated

| Stage | How the test drives it |
|---|---|
| study arrival → `first-check` | `POST /dicom/import-study` (real C-FIND to Orthanc) |
| `anonymize-study`, `create-nifty` | **real** — pipeline workers C-GET from Orthanc, anonymize, convert DICOM→NIfTI |
| `master-model`, `create-master-projection`, `compare-with-projection`, `combine-resection`, MDK auto-creation | **real** — automated pipeline tasks |
| `segment-prospective-ct`, `anatomy-model`, `resection-plan`, `postop-resection` (Slicer stages) | synthetic `.seg.nrrd` written at the record's resolved on-disk path, submitted via `POST /data` (no Slicer validator) |
| `mdk-conclusion`, `resection-report`, `pathomorphology` (form stages) | prefill read back, required fields filled, submitted |
| `auto-liver` (GPU TotalSegmentator) | output `liver.nii.gz` injected directly — there is no GPU worker on the stand |

The parallel anon / archive segmentation branches (`segment-ct-single`,
`segment-ct-with-archive`) are intentionally left pending — they are not on the
main spine and gate nothing downstream.

## Files

- `test_workflow.py` — the staged test (one function walking the DAG with assertions).
- `workflow_stand.py` — host-side `Stand` driver (httpx API client + SSH/SCP + waits).
- `stand_tool.py` — VM-side toolbox (resolve file paths, read NIfTI geometry,
  synthesise geometry-consistent `.seg.nrrd` / liver mask). Shipped to the VM by
  the `Stand` fixture; runs inside `/opt/clarinet`'s venv.
- `setup_stand.sh` — one-time idempotent stand provisioning (see below).

## Prerequisites

A deployed stand with the nir_liver project (see `deploy/` —
`PROJECT_SOURCE_DIR` + `make vm-deploy`) and demo DICOM baked into Orthanc
(`make vm-bake DICOM=...`). Then provision the DICOM pipeline once:

```bash
docs/testing/prototype-nir_liver/setup_stand.sh clarinet@<vm-ip> ~/.ssh/clarinet-vm [known_hosts]
```

This enables the DICOM worker, sets C-GET retrieve mode, and registers the
clarinet AET as an Orthanc modality (Orthanc denies Q/R to unknown AETs by
default). Idempotent — safe to re-run.

## Running

The suite skips unless `STAND_URL` is set, so it is safe to collect anywhere.

```bash
STAND_URL=https://127.0.0.1:8443/nir_liver \
STAND_ADMIN_PASSWORD=<admin password> \
STAND_SSH_TARGET=clarinet@<vm-ip> \
STAND_SSH_KEY=~/.ssh/clarinet-vm \
STAND_KNOWN_HOSTS=~/.local/share/clarinet-deploy/known_hosts \
uv run pytest docs/testing/prototype-nir_liver/ -v
```

`STAND_URL` is the project root (no `/api`). When the VM is on a libvirt NAT
not routable from the test host, tunnel first:

```bash
ssh -i ~/.ssh/clarinet-vm -N -L 8443:localhost:443 clarinet@<vm-ip> &
```

The test resets its patient (`DEMO001`) at the start of every run — records,
study, on-disk working dir, and anonymised Orthanc copies — so it is fully
re-runnable.
