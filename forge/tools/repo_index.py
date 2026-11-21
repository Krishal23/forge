"""Read a Python repo with `ast` (no code is run): files, functions, imports."""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".pytest_cache", ".ruff_cache"}
STOPWORDS = {
    "the", "and", "for", "with", "must", "should", "when", "then", "that", "this", "from", "into", "only",
    "each", "add", "new", "are", "not", "all", "any", "can", "get", "set", "has", "its", "use", "via",
    "test", "tests", "self", "none", "true", "false", "return", "returns", "init", "main",
}


@dataclass
class Symbol:
    name: str
    kind: str  # function | class | method
    lineno: int
    signature: str
    doc: str = ""


@dataclass
class ModuleInfo:
    path: str
    module: str
    lines: int
    is_test: bool
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)  # internal module names
    parse_error: str | None = None


def _module_name(rel: str) -> str:
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _first_doc_line(node: ast.AST) -> str:
    doc = ast.get_docstring(node)  # type: ignore[arg-type]
    return doc.strip().splitlines()[0][:160] if doc else ""


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){ret}"


def _collect_symbols(tree: ast.Module) -> list[Symbol]:
    out: list[Symbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(Symbol(node.name, "function", node.lineno, _signature(node), _first_doc_line(node)))
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            out.append(Symbol(node.name, "class", node.lineno, f"class {node.name}({bases})", _first_doc_line(node)))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(
                        Symbol(f"{node.name}.{item.name}", "method", item.lineno, _signature(item),
                               _first_doc_line(item))
                    )
    return out


def _raw_imports(tree: ast.Module, module: str, is_package: bool) -> list[str]:
    """All module names this file imports, with relative imports resolved."""
    names: list[str] = []
    pkg_parts = module.split(".") if is_package else module.split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
                base = ".".join([*base_parts, *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            if base:
                names.append(base)
            # `from pkg import submodule` imports a module too.
            names += [f"{base}.{a.name}" if base else a.name for a in node.names]
    return names


class RepoIndex:
    def __init__(self, root: Path, modules: dict[str, ModuleInfo]):
        self.root = Path(root)
        self.modules = modules  # keyed by relative path
        self._by_module = {m.module: m.path for m in modules.values()}

    @classmethod
    def build(cls, root: Path, source_dirs: list[str], test_dirs: list[str]) -> RepoIndex:
        root = Path(root).resolve()
        modules: dict[str, ModuleInfo] = {}
        raw: dict[str, list[str]] = {}
        for top in dict.fromkeys([*source_dirs, *test_dirs]):
            base = root / top
            if not base.is_dir():
                continue
            for file in sorted(base.rglob("*.py")):
                rel_parts = file.relative_to(root).parts
                if any(p in SKIP_DIRS or p.startswith(".") for p in rel_parts):
                    continue
                rel = "/".join(rel_parts)
                text = file.read_text(errors="replace")
                module = _module_name(rel)
                info = ModuleInfo(rel, module, text.count("\n") + 1, top in test_dirs)
                try:
                    tree = ast.parse(text)
                except SyntaxError as exc:
                    info.parse_error = f"line {exc.lineno}: {exc.msg}"
                    modules[rel] = info
                    continue
                info.symbols = _collect_symbols(tree)
                raw[rel] = _raw_imports(tree, module, rel.endswith("__init__.py"))
                modules[rel] = info
        known = {m.module for m in modules.values()}
        for rel, names in raw.items():
            internal = sorted({n for n in names if n in known and n != modules[rel].module})
            modules[rel].imports = internal
        return cls(root, modules)

    def path_of(self, module: str) -> str | None:
        return self._by_module.get(module)

    def dependencies(self, path: str) -> list[str]:
        info = self.modules.get(path)
        return sorted({p for m in (info.imports if info else []) if (p := self.path_of(m))})

    def dependents(self, path: str) -> list[str]:
        info = self.modules.get(path)
        if not info:
            return []
        return sorted(p for p, m in self.modules.items() if info.module in m.imports)

    def impact(self, paths: list[str]) -> dict[str, dict]:
        return {p: {"imports": self.dependencies(p), "imported_by": self.dependents(p)} for p in paths}

    def guarded_names(self, high_risk_paths: list[str]) -> set[str]:
        """Public top-level functions defined in high-risk modules (e.g. authorization checks)."""
        names: set[str] = set()
        for path in high_risk_paths:
            info = self.modules.get(path)
            if info:
                names |= {s.name for s in info.symbols if s.kind == "function" and not s.name.startswith("_")}
        return names

    def rank(self, text: str, limit: int = 8) -> list[dict]:
        """Rank source files by how directly the text refers to them."""
        lowered = text.lower()
        words = {w for w in re.findall(r"[a-z_][a-z0-9_]{2,}", lowered) if w not in STOPWORDS}
        results = []
        for path, info in self.modules.items():
            if info.is_test or not info.symbols:
                continue
            score, reasons = 0, []
            if info.module.lower() in lowered and "." in info.module:
                score += 6
                reasons.append(f"module {info.module} named")
            stem = info.module.split(".")[-1].lower()
            if stem in words or stem.rstrip("s") in words or f"{stem}s" in words:
                score += 2
                reasons.append(f"'{stem}' mentioned")
            for sym in info.symbols:
                short = sym.name.split(".")[-1].lower()
                if short.startswith("_") or short in STOPWORDS or len(short) < 4:
                    continue
                if f"{info.module}.{sym.name}".lower() in lowered:
                    score += 8
                    reasons.append(f"{info.module}.{sym.name} named")
                elif short in words:
                    score += 3
                    reasons.append(f"symbol {sym.name} mentioned")
            if score:
                results.append({"path": path, "score": score, "reasons": reasons[:4]})
        results.sort(key=lambda r: (-r["score"], r["path"]))
        return results[:limit]

    def summary(self, max_chars: int = 7000) -> str:
        lines: list[str] = []
        for path in sorted(self.modules):
            info = self.modules[path]
            tag = " [test]" if info.is_test else ""
            lines.append(f"{path} ({info.lines} lines){tag}")
            if info.parse_error:
                lines.append(f"  !! syntax error {info.parse_error}")
            if info.imports:
                lines.append(f"  imports: {', '.join(info.imports)}")
            for sym in info.symbols:
                if info.is_test and sym.kind == "function":
                    continue
                indent = "    " if sym.kind == "method" else "  "
                doc = f"  # {sym.doc}" if sym.doc else ""
                lines.append(f"{indent}{sym.signature}{doc}")
            if info.is_test:
                tests = [s.name for s in info.symbols if s.name.startswith("test_")]
                if tests:
                    lines.append(f"  tests: {', '.join(tests)}")
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[: max_chars - 40] + "\n... (index truncated)"

    def to_dict(self) -> dict:
        return {
            "files": [asdict(m) for m in self.modules.values()],
            "graph": {p: self.dependencies(p) for p in self.modules},
        }


def read_numbered(root: Path, path: str, max_lines: int = 400) -> str:
    """File content with line numbers, for prompts. Marked as data, not instructions."""
    file = Path(root) / path
    if not file.exists():
        return f"<<file {path} does not exist>>"
    lines = file.read_text(errors="replace").splitlines()
    shown = lines[:max_lines]
    body = "\n".join(f"{i + 1:4d}| {line}" for i, line in enumerate(shown))
    more = f"\n... ({len(lines) - max_lines} more lines)" if len(lines) > max_lines else ""
    return f"<<<FILE {path}>>>\n{body}{more}\n<<<END FILE {path}>>>"
