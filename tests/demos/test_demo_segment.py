"""Demo: single-volume segmentation in 3D Slicer.

Mirrors the ``segment.py`` script from clarinet_nir_liver:
load a CT volume from PACS, create a 3-segment segmentation,
set up the Segment Editor with Paint tool.

Run: ``make slicer-demo-segment``
"""

from pathlib import Path
from typing import Any

import pytest

from clarinet.services.slicer.service import SlicerService

pytestmark = [pytest.mark.slicer, pytest.mark.demo, pytest.mark.asyncio]


async def test_demo_segment(
    slicer_service: SlicerService,
    slicer_url: str,
    pacs_monkey_patch: str,
    demo_working_dir: Path,
    orthanc_first_study: dict[str, Any],
) -> None:
    """Open Slicer with a single-volume segmentation workspace."""
    study_uid = orthanc_first_study["study_uid"]

    script = f"""\
{pacs_monkey_patch}

s = SlicerHelper('{demo_working_dir}')

# Load volume from PACS
loaded = s.load_study_from_pacs('{study_uid}', window=(-200, 300))
print(f"[Demo] loaded {{len(loaded)}} nodes from study {study_uid}")

# Create segmentation with 3 segments
seg = (
    s.create_segmentation("Segmentation")
    .add_segment("mts", (1.0, 0.0, 0.0))
    .add_segment("unclear", (1.0, 1.0, 0.0))
    .add_segment("benign", (0.0, 1.0, 0.0))
)

# Setup editor
s.setup_editor(seg, effect="Paint", brush_size=5.0)
s.set_layout("axial")
s.add_view_shortcuts()
s.annotate("Demo: segment lesions")

__execResult = {{"status": "ok", "study_uid": "{study_uid}"}}
"""
    result = await slicer_service.execute(slicer_url, script, request_timeout=60.0)
    assert result.get("status") == "ok"
    print(f"[Demo] segment demo loaded successfully: study={study_uid}")
