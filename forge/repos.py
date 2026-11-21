"""Bring your own repo: import a zip or register a local folder, then check it before a run.

Uploaded zips are unpacked into FORGE_HOME/repos/<name>/. Local folders are only recorded by
path; each run still works on its own copy, so the original folder is never changed.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from forge.config import FORGE_HOME, SAMPLES_DIR, TargetConfig
from forge.tools.testing import run_tests
from forge.tools.workspace import COPY_IGNORE

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
MAX_FILES = 20_000
MAX_RATIO = 100  # a member that expands more than this is treated as a zip bomb
SKIP_PARTS = {"__MACOSX", ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
NAME_RE = re.compile(r"[^a-z0-9._-]+")


class RepoError(ValueError):
    pass


def repos_dir(home: Path = FORGE_HOME) -> Path:
    return Path(home) / "repos"


def _index_path(home: Path) -> Path:
    return repos_dir(home) / "index.json"


def _load_index(home: Path) -> dict:
    p = _index_path(home)
    return json.loads(p.read_text()) if p.exists() else {}


def _save_index(home: Path, index: dict) -> None:
    p = _index_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2))
    tmp.replace(p)


def slugify(name: str) -> str:
    slug = NAME_RE.sub("-", name.lower()).strip("-.")[:60]
    if not slug:
        raise RepoError("repository name is empty")
    return slug


def _unique(home: Path, base: str) -> str:
    index = _load_index(home)
    name, n = base, 2
    while name in index or (repos_dir(home) / name).exists() or (SAMPLES_DIR / name).exists():
        name, n = f"{base}-{n}", n + 1
    return name


def _member_parts(info: zipfile.ZipInfo) -> tuple[str, ...]:
    """Validate one zip entry and return its path parts. Raises on anything unsafe."""
    raw = info.filename.replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or re.match(r"^[A-Za-z]:", raw):
        raise RepoError(f"zip entry has an absolute path: {info.filename}")
    if ".." in path.parts:
        raise RepoError(f"zip entry leaves the folder: {info.filename}")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise RepoError(f"zip entry is a symbolic link: {info.filename}")
    return tuple(p for p in path.parts if p not in ("", "."))


def safe_extract(data: bytes, dest: Path) -> int:
    """Unpack a zip into dest with limits on size, file count and paths. Returns files written.

    A single top-level folder (as in GitHub downloads) is removed so dest holds the repo itself.
    """
    if len(data) > MAX_ZIP_BYTES:
        raise RepoError(f"zip is larger than {MAX_ZIP_BYTES // (1024 * 1024)} MB")
    with tempfile.TemporaryFile() as fh:
        fh.write(data)
        fh.seek(0)
        try:
            zf = zipfile.ZipFile(fh)
        except zipfile.BadZipFile as exc:
            raise RepoError("not a valid zip file") from exc
        with zf:
            entries = []
            total = 0
            for info in zf.infolist():
                parts = _member_parts(info)
                if not parts or info.is_dir() or any(p in SKIP_PARTS for p in parts):
                    continue
                if info.flag_bits & 0x1:
                    raise RepoError("encrypted zip files are not supported")
                total += info.file_size
                if total > MAX_UNPACKED_BYTES:
                    raise RepoError(f"unpacked size is over {MAX_UNPACKED_BYTES // (1024 * 1024)} MB")
                if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                    raise RepoError(f"zip entry expands too much: {info.filename}")
                entries.append((info, parts))
            if len(entries) > MAX_FILES:
                raise RepoError(f"zip has more than {MAX_FILES} files")
            if not entries:
                raise RepoError("zip is empty")
            tops = {parts[0] for _, parts in entries}
            strip = len(tops) == 1 and all(len(parts) > 1 for _, parts in entries)
            dest.mkdir(parents=True, exist_ok=False)
            root = dest.resolve()
            for info, parts in entries:
                rel = parts[1:] if strip else parts
                out = (root / Path(*rel)).resolve()
                if not out.is_relative_to(root):
                    raise RepoError(f"zip entry leaves the folder: {info.filename}")
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(out, "wb") as dst:
                    written = 0
                    while chunk := src.read(1 << 16):
                        written += len(chunk)
                        if written > info.file_size:  # header lied about the size
                            raise RepoError(f"zip entry is larger than declared: {info.filename}")
                        dst.write(chunk)
    return len(entries)


def inspect_repo(root: Path, run_check: bool = True, timeout_s: float = 120) -> dict:
    """Describe a repo the way Forge will see it, with problems and warnings for the user."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise RepoError(f"{root} is not a folder")
    cfg = TargetConfig.load(root)
    py_files = [p for p in root.rglob("*.py") if not any(s in p.parts for s in SKIP_PARTS)]
    problems: list[str] = []
    warnings: list[str] = []
    if not py_files:
        problems.append("No Python files found. Forge works on Python repositories.")
    if not cfg.source_dirs:
        problems.append("No source package found (a folder with __init__.py, or src/<package>). "
                        "Add a forge.toml with source_dirs to say where the code is.")
    present_tests = [d for d in cfg.test_dirs if (root / d).is_dir()]
    if not present_tests:
        problems.append(f"No test folder found (looked for {', '.join(cfg.test_dirs)}). "
                        "Forge needs existing pytest tests to check the change.")
    if not (root / "forge.toml").exists():
        warnings.append("No forge.toml, so the layout was detected automatically and no files are "
                        "marked high-risk.")
    if any((root / f).exists() for f in ("requirements.txt", "setup.py", "pyproject.toml")):
        warnings.append("Tests run with Forge's own Python. Packages the repo depends on must be "
                        "installed there (uv pip install -r requirements.txt).")
    info = {
        "name": cfg.name, "path": str(root), "python_files": len(py_files),
        "source_dirs": cfg.source_dirs, "test_dirs": present_tests, "has_forge_toml": (root / "forge.toml").exists(),
        "high_risk_paths": cfg.high_risk_paths, "tests": None, "problems": problems, "warnings": warnings,
    }
    if run_check and not problems:
        info["tests"] = _baseline(root, cfg, timeout_s)
        t = info["tests"]
        if t["total"] == 0:
            problems.append("pytest collected no tests.")
        elif t["missing_modules"]:
            warnings.append(f"Tests need packages that are not installed: {', '.join(t['missing_modules'])}. "
                            "Install them into Forge's environment (uv pip install <name>) and check again.")
        elif not t["ok"]:
            warnings.append(f"{t['failed'] + t['errors']} of {t['total']} existing tests fail before any "
                            "change. Forge will not let a change make them worse, but a fix loop may "
                            "be harder. Missing packages are the usual cause.")
    info["ok"] = not problems
    return info


def _baseline(root: Path, cfg: TargetConfig, timeout_s: float) -> dict:
    """Run the existing tests on a throwaway copy so the user's folder is not touched."""
    with tempfile.TemporaryDirectory(prefix="forge-check-") as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(root, copy, ignore=COPY_IGNORE)
        reports = Path(tmp) / "reports"
        rep = run_tests(copy, TargetConfig.load(copy), reports, coverage=False, timeout_s=timeout_s)
    out = {k: getattr(rep, k) for k in ("ok", "passed", "failed", "errors", "skipped", "timed_out")}
    out["total"] = rep.total
    out["first_failures"] = [f"{f.test}: {f.message[:200]}" for f in rep.failures[:3]]
    text = rep.output_tail + "".join(f.message for f in rep.failures)
    out["missing_modules"] = sorted(set(re.findall(r"No module named '([\w.]+)'", text)))
    return out


def _record(home: Path, name: str, kind: str, path: Path, info: dict) -> dict:
    index = _load_index(home)
    entry = {
        "name": name, "kind": kind, "path": str(path), "added_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ok": info["ok"], "summary": _summary(info),
    }
    index[name] = entry
    _save_index(home, index)
    return entry


def _summary(info: dict) -> str:
    t = info.get("tests")
    tests = f"{t['passed']}/{t['total']} tests pass" if t else "tests not run"
    return f"{info['python_files']} Python files, source {', '.join(info['source_dirs']) or 'none'}, {tests}"


def import_zip(data: bytes, filename: str, home: Path = FORGE_HOME, run_check: bool = True) -> dict:
    base = slugify(Path(filename).stem or "repo")
    name = _unique(home, base)
    dest = repos_dir(home) / name
    try:
        safe_extract(data, dest)
        info = inspect_repo(dest, run_check=run_check)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    entry = _record(home, name, "upload", dest, info)
    return {**entry, "check": info}


def add_local(path: str, home: Path = FORGE_HOME, run_check: bool = True) -> dict:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise RepoError(f"folder not found: {path}")
    if root in (Path("/"), Path.home().resolve()) or Path(home).resolve().is_relative_to(root):
        raise RepoError("pick the repository folder itself, not a parent folder")
    info = inspect_repo(root, run_check=run_check)
    index = _load_index(home)
    existing = next((n for n, e in index.items() if e["kind"] == "local" and e["path"] == str(root)), None)
    name = existing or _unique(home, slugify(root.name))
    entry = _record(home, name, "local", root, info)
    return {**entry, "check": info}


def list_repos(home: Path = FORGE_HOME) -> list[dict]:
    index = _load_index(home)
    out = []
    for entry in sorted(index.values(), key=lambda e: e["added_at"], reverse=True):
        out.append({**entry, "available": Path(entry["path"]).is_dir()})
    return out


def get_repo_path(name: str, home: Path = FORGE_HOME) -> Path | None:
    entry = _load_index(home).get(name)
    return Path(entry["path"]) if entry else None


def remove_repo(name: str, home: Path = FORGE_HOME) -> bool:
    """Forget a saved repo. Uploaded copies are deleted; local folders are left alone."""
    index = _load_index(home)
    entry = index.pop(name, None)
    if entry is None:
        return False
    if entry["kind"] == "upload":
        target = Path(entry["path"]).resolve()
        if target.is_relative_to(repos_dir(home).resolve()):
            shutil.rmtree(target, ignore_errors=True)
    _save_index(home, index)
    return True
