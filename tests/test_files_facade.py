def test_leaf_modules_import():
    from clarinet.files._template import render_template, validate_template, RenderMode
    from clarinet.files._anon import require_anon_or_raw
    from clarinet.files._fs import run_in_fs_thread, shutdown_fs_executor

    assert render_template("{a}", {"a": "x"}) == "x"
    assert validate_template("{patient_id}/{study_uid}/{series_uid}")
