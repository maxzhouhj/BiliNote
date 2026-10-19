import importlib.util
from pathlib import Path


def test_configured_data_directory_is_used_without_extra_nesting(monkeypatch, tmp_path):
    script = Path(__file__).resolve().parents[1] / "app/utils/path_helper.py"
    spec = importlib.util.spec_from_file_location("path_helper_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "application-media"
    monkeypatch.setenv("DATA_DIR", str(target))
    assert Path(module.get_data_dir()) == target
    assert target.is_dir()


def test_working_artifacts_use_the_source_application_directory(monkeypatch, tmp_path):
    script = Path(__file__).resolve().parents[1] / "app/utils/path_helper.py"
    spec = importlib.util.spec_from_file_location("path_helper_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "work"
    monkeypatch.setenv("APP_DATA_DIR", str(target))
    assert Path(module.get_app_dir("output_frames")) == target / "output_frames"
    assert (target / "output_frames").is_dir()
