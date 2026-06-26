from unittest.mock import MagicMock

from clarinet.files._patterns import fields_from
from clarinet.files._template import RenderMode, render_template


def _rec(*, rid=1, user_id=None, rtype="seg", data=None):
    r = MagicMock()
    r.id = rid
    r.user_id = user_id
    r.patient_id = "P1"
    r.study_uid = "S"
    r.series_uid = "SE"
    r.record_type = MagicMock(name_attr=rtype)
    r.record_type.name = rtype
    r.data = data or {}
    return r


def test_origin_type_inverts_to_parent():
    child = _rec(rtype="compare")
    parent = _rec(rtype="segmentation")
    assert fields_from(child, parent)["origin_type"] == "segmentation"
    assert fields_from(child)["origin_type"] == "compare"


def test_scalar_falls_back_to_parent():
    child = _rec(user_id=None)
    parent = _rec(user_id="doctor-7")
    assert fields_from(child, parent)["user_id"] == "doctor-7"
    assert fields_from(child)["user_id"] is None


def test_list_data_field_coerces_join_not_repr():
    rec = _rec(data={"mods": ["SR", "CT"]})
    out = render_template("{data.mods}", fields_from(rec), mode=RenderMode.LENIENT)
    assert out == "CT_SR"  # sorted, "_"-joined — NOT "['SR', 'CT']"
