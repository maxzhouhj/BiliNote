"""Check production frontend routing and non-destructive port selection."""
import importlib.util
from pathlib import Path
import socket

from fastapi import FastAPI
from fastapi.testclient import TestClient


def load_launcher():
    script = Path(__file__).resolve().parents[2] / "scripts/source_app.py"
    spec = importlib.util.spec_from_file_location("source_app_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frontend_deep_links_and_api_routes(tmp_path):
    module = load_launcher()
    (tmp_path / "index.html").write_text("<html>BiliNote UI</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/main.js").write_text("console.log('app')", encoding="utf-8")
    app = FastAPI()
    @app.get("/api/sys_check")
    def health():
        return {"code": 0}
    app.mount("/", module.SourceFrontend(directory=str(tmp_path), html=True))
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        deep = client.get("/settings/download/douyin")
        assert deep.status_code == 200
        assert "BiliNote UI" in deep.text
        assert client.get("/assets/main.js").status_code == 200
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/api/sys_check").json() == {"code": 0}
        assert client.get("/api/missing").status_code == 404
        assert client.get("/uploads/missing").status_code == 404


def test_busy_port_is_skipped_without_disturbing_existing_listener():
    module = load_launcher()
    with socket.socket() as existing:
        existing.bind(("127.0.0.1", 0))
        existing.listen()
        preferred = existing.getsockname()[1]
        with module.bind_loopback_socket(preferred) as selected:
            assert selected.getsockname()[0] == "127.0.0.1"
            assert selected.getsockname()[1] != preferred
        assert existing.getsockname()[1] == preferred
