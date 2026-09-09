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

PKG = pathlib.Path(__file__).resolve().parents[1] / "tallystick"

#: Every module in the package except propose/ and adapters/ is on the verdict
#: path. Recursive, so any other future subpackage is covered the day it appears.
NOT_VERDICT = {"propose", "adapters"}
VERDICT_MODULES = sorted(
    str(p.relative_to(PKG)) for p in PKG.rglob("*.py")
    if not (set(p.relative_to(PKG).parts) & NOT_VERDICT)
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


def _model_side_imports(tree: ast.AST):
    """Every node that imports tallystick.propose by any spelling."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("tallystick.propose"):
                    yield node
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = {a.name for a in node.names}
            if node.level == 0 and mod.startswith("tallystick.propose"):
                yield node
            elif node.level == 0 and mod == "tallystick" and "propose" in names:
                yield node   # `from tallystick import propose`
            elif node.level > 0 and mod.split(".")[0] == "propose":
                yield node
            elif node.level > 0 and not mod and "propose" in names:
                yield node   # `from . import propose`


def _nodes_inside_functions(tree: ast.AST) -> set:
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                inside.add(id(sub))
    return inside


def test_verdict_path_never_imports_the_model_side():
    """`tallystick/propose/` may call models. Nothing outside it may reach in.
    cli.py is the one exception, and only inside a function body (a lazy import
    for the `propose` subcommand) - never anywhere that runs at import time,
    including inside a module-level `try:` or `if:`."""
    offences = []
    for name in VERDICT_MODULES:
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        hits = list(_model_side_imports(tree))
        if not hits:
            continue
        if name != "cli.py":
            offences.append(name)
            continue
        lazy = _nodes_inside_functions(tree)
        if any(id(h) not in lazy for h in hits):
            offences.append(f"{name} (import at module import time)")
    assert not offences, f"verdict path imports the model side: {offences}"


def test_only_the_model_side_may_import_an_sdk():
    propose_dir = PKG / "propose"
    sdk_importers = {
        p.name for p in propose_dir.glob("*.py")
        if _imported_roots(p) & {"anthropic", "openai"}
    }
    assert sdk_importers == {"anthropic_proposer.py"}, sdk_importers


def test_the_boundary_test_catches_every_known_bypass(tmp_path):
    """Meta-test: each spelling that once slipped past must now be flagged."""
    bypasses = [
        "import tallystick.propose.anthropic_proposer\n",
        "from . import propose\n",
        "from tallystick import propose\n",
        "from .propose import post_run\n",
        "try:\n    from .propose import post_run\nexcept ImportError:\n    pass\n",
        "if True:\n    from .propose import post_run\n",
    ]
    for src in bypasses:
        tree = ast.parse(src)
        hits = list(_model_side_imports(tree))
        assert hits, f"not caught: {src!r}"
        assert all(id(h) not in _nodes_inside_functions(tree) for h in hits), src
    lazy = "def f():\n    from .propose import post_run\n"
    tree = ast.parse(lazy)
    hits = list(_model_side_imports(tree))
    assert hits and all(id(h) in _nodes_inside_functions(tree) for h in hits)


def test_adapters_never_import_an_sdk():
    """An adapter records; it must not be able to call a model. Frameworks
    (langchain_core) are allowed, model SDKs are not."""
    sdk_roots = {"anthropic", "openai", "cohere", "litellm", "transformers", "torch"}
    for p in (PKG / "adapters").rglob("*.py"):
        bad = _imported_roots(p) & sdk_roots
        assert not bad, f"adapters/{p.name} imports {sorted(bad)}"


def test_adapters_never_import_the_model_side_and_the_core_never_imports_adapters():
    """Recording and proposing are separate jobs. An adapter that proposed would be
    a framework-specific verdict; a core that recorded would drag in frameworks."""
    for p in (PKG / "adapters").rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        assert not list(_model_side_imports(tree)), f"adapters/{p.name} imports propose/"
    for name in VERDICT_MODULES:
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and \
                    "adapters" in node.module.split("."):
                raise AssertionError(f"{name} imports adapters/")
            if isinstance(node, ast.ImportFrom) and node.level > 0 and \
                    any(a.name == "adapters" for a in node.names):
                raise AssertionError(f"{name} imports adapters/")
