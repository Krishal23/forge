import io
import stat
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forge.api import create_app
from forge.chaos import Chaos
from forge.config import SAMPLES_DIR, TargetConfig
from forge.orchestrator import Orchestrator
from forge.repos import RepoError, add_local, import_zip, list_repos, remove_repo, safe_extract
from forge.scenarios import resolve_target
from forge.store import Store


def make_zip(files: dict[str, str], prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(prefix + name, text)
    return buf.getvalue()


def sample_zip(name: str = "inventory-service", prefix: str = "proj-main/") -> bytes:
    root = SAMPLES_DIR / name
    files = {
        str(p.relative_to(root)): p.read_text()
        for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    }
    return make_zip(files, prefix)


def test_extract_strips_single_top_folder(tmp_path):
    n = safe_extract(make_zip({"pkg/__init__.py": "", "tests/test_a.py": "def test_a(): pass\n"}, "repo-main/"),
                     tmp_path / "out")
    assert n == 2
    assert (tmp_path / "out" / "pkg" / "__init__.py").exists()


@pytest.mark.parametrize("bad", ["../evil.py", "/etc/evil.py", "a/../../evil.py", "C:/evil.py"])
def test_extract_rejects_paths_outside(tmp_path, bad):
    with pytest.raises(RepoError):
        safe_extract(make_zip({bad: "x"}), tmp_path / "out")
    assert not (tmp_path / "evil.py").exists()


def test_extract_rejects_symlinks(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(RepoError, match="symbolic link"):
        safe_extract(buf.getvalue(), tmp_path / "out")


def test_extract_rejects_zip_bomb_and_junk(tmp_path):
    with pytest.raises(RepoError, match="expands too much"):
        safe_extract(make_zip({"big.txt": "0" * 5_000_000}), tmp_path / "a")
    with pytest.raises(RepoError, match="not a valid zip"):
        safe_extract(b"not a zip", tmp_path / "b")
    with pytest.raises(RepoError, match="empty"):
        safe_extract(make_zip({"__MACOSX/x": "y"}), tmp_path / "c")


def test_detects_src_layout_and_test_folder(tmp_path):
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "lib" / "__init__.py").write_text("")
    (tmp_path / "test").mkdir()
    cfg = TargetConfig.load(tmp_path)
    assert cfg.source_dirs == ["src/lib"]
    assert cfg.test_dirs == ["test"]


def test_import_zip_checks_and_saves(tmp_path):
    res = import_zip(sample_zip(), "inventory.zip", home=tmp_path)
    assert res["ok"] and res["kind"] == "upload"
    assert res["check"]["tests"]["passed"] == res["check"]["tests"]["total"] > 0
    assert resolve_target(res["name"], tmp_path) == (tmp_path / "repos" / res["name"]).resolve()
    assert [r["name"] for r in list_repos(tmp_path)] == [res["name"]]
    again = import_zip(sample_zip(), "inventory.zip", home=tmp_path, run_check=False)
    assert again["name"] != res["name"]
    assert remove_repo(res["name"], tmp_path)
    assert not (tmp_path / "repos" / res["name"]).exists()


def test_import_zip_reports_problems(tmp_path):
    res = import_zip(make_zip({"notes.txt": "hello"}), "docs.zip", home=tmp_path)
    assert not res["ok"]
    assert any("Python" in p for p in res["check"]["problems"])


def test_add_local_keeps_folder_and_refuses_parents(tmp_path):
    res = add_local(str(SAMPLES_DIR / "payments-service"), home=tmp_path, run_check=False)
    assert res["ok"] and res["kind"] == "local"
    assert remove_repo(res["name"], tmp_path)
    assert (SAMPLES_DIR / "payments-service" / "forge.toml").exists()
    with pytest.raises(RepoError):
        add_local(str(Path.home()), home=tmp_path)
    with pytest.raises(RepoError):
        add_local(str(tmp_path / "missing"), home=tmp_path)


@pytest.fixture
def client(tmp_path):
    store = Store(tmp_path / "forge.db")
    orch = Orchestrator(store, home=tmp_path, chaos=Chaos(), mock_latency_ms=0)
    with TestClient(create_app(store, orch, home=tmp_path, resume=False)) as c:
        yield c
    store.close()


def test_api_upload_list_delete(client):
    r = client.post("/api/repos/upload?filename=inv.zip", content=sample_zip(),
                    headers={"content-type": "application/zip"})
    assert r.status_code == 201, r.text
    name = r.json()["name"]
    assert r.json()["check"]["ok"]
    assert [x["name"] for x in client.get("/api/repos").json()] == [name]
    assert client.delete(f"/api/repos/{name}").json() == {"ok": True}
    assert client.get("/api/repos").json() == []


def test_api_upload_rules(client):
    bad = client.post("/api/repos/upload", content=make_zip({"../x.py": ""}),
                      headers={"content-type": "application/zip"})
    assert bad.status_code == 400
    other = client.post("/api/repos/upload", content=b"x", headers={"content-type": "text/plain"})
    assert other.status_code == 415
    cross = client.post("/api/repos/upload", content=sample_zip(),
                        headers={"content-type": "application/zip", "origin": "https://evil.example"})
    assert cross.status_code == 403
    zip_elsewhere = client.post("/api/runs", content=b"x", headers={"content-type": "application/zip"})
    assert zip_elsewhere.status_code == 415


def test_api_local_folder(client):
    r = client.post("/api/repos/local", json={"path": str(SAMPLES_DIR / "inventory-service")})
    assert r.status_code == 201 and r.json()["ok"]
    assert client.post("/api/repos/local", json={"path": "/no/such/folder"}).status_code == 400
