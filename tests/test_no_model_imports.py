"""The discipline test.

The central claim of this library is that the verdict is produced without a model
and without the network. A claim like that decays the moment someone adds a
convenient import, so it is enforced here rather than promised in the README.

Any module that participates in producing a verdict must not import anything that
could reach a model, a network, or a source of randomness.
"""

from __future__ import annotations

import ast
import pathlib

#: Every module in the package is on the verdict path; there is no other path.
VERDICT_MODULES = sorted(
    p.name for p in (pathlib.Path(__file__).resolve().parents[1] / "tallystick").glob("*.py")
)

FORBIDDEN_ROOTS = {
    # model access
    "anthropic", "openai", "cohere", "transformers", "torch", "tensorflow",
    "sentence_transformers", "litellm", "instructor", "langchain", "llama_index",
    # network
    "requests", "httpx", "urllib", "urllib3", "http", "socket", "aiohttp",
    # anything whose output can differ between two identical runs
    "random", "secrets", "time", "datetime", "uuid",
    # escape hatches
    "importlib", "subprocess", "os", "ctypes", "pickle",
}

#: Dynamic import machinery would let a module dodge the static check above.
FORBIDDEN_CALLS = {"__import__", "import_module", "exec", "eval"}

PKG = pathlib.Path(__file__).resolve().parents[1] / "tallystick"


def _imported_roots(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _called_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def test_every_module_is_scanned():
    assert {"ledger.py", "verify.py", "normalize.py"} <= set(VERDICT_MODULES)


def test_verdict_path_imports_nothing_that_could_call_a_model():
    offences = {}
    for name in VERDICT_MODULES:
        bad = _imported_roots(PKG / name) & FORBIDDEN_ROOTS
        if bad:
            offences[name] = sorted(bad)
    assert not offences, (
        f"the verdict path must stay deterministic and offline; found {offences}"
    )


def test_verdict_path_uses_no_dynamic_imports():
    offences = {}
    for name in VERDICT_MODULES:
        bad = _called_names(PKG / name) & FORBIDDEN_CALLS
        if bad:
            offences[name] = sorted(bad)
    assert not offences, f"dynamic import machinery found: {offences}"
