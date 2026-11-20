"""Structural checks on the integration package.

Catches a whole family of "works in isolation, explodes in Home Assistant"
failures: a relative import that names a module which does not exist. This
regressed once already (``camera.py`` imported ``.const`` while the constants
live in ``core/const.py``) and only surfaced when Home Assistant actually
loaded the platform.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "custom_components" / "ezykam"

#: `from .name import x` / `from .name.sub import x`
_RELATIVE_IMPORT = re.compile(r"^\s*from\s+\.+(?P<module>[\w.]+)\s+import\s", re.M)


def _integration_sources() -> list[Path]:
    return sorted(
        p
        for p in PACKAGE.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def test_package_layout_is_complete() -> None:
    """The expected modules exist."""
    for required in (
        PACKAGE / "__init__.py",
        PACKAGE / "config_flow.py",
        PACKAGE / "coordinator.py",
        PACKAGE / "camera.py",
        PACKAGE / "binary_sensor.py",
        PACKAGE / "diagnostics.py",
        PACKAGE / "manifest.json",
        PACKAGE / "strings.json",
        PACKAGE / "core" / "client.py",
        PACKAGE / "core" / "auth.py",
        PACKAGE / "core" / "discovery.py",
        PACKAGE / "core" / "mqtt.py",
        PACKAGE / "core" / "webrtc.py",
        PACKAGE / "core" / "const.py",
    ):
        assert required.is_file(), f"missing {required.relative_to(REPO_ROOT)}"


def test_every_relative_import_resolves() -> None:
    """No ``from .x import name`` may reference a missing module *or* name.

    Relative imports resolve against the *containing package*, so the base is
    the source file's own directory (``core/client.py`` -> ``core/``).

    Checking names as well as modules matters: ``diagnostics.py`` once imported
    ``fingerprint`` from ``redact`` when it actually lives in ``models``, and a
    module-only check passed while Home Assistant failed to load the platform
    (surfacing as an unexplained HTTP 404 on the diagnostics download).
    """
    failures: list[str] = []
    checked = 0

    for source in _integration_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        base = source.parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            module = node.module or ""
            checked += 1
            # `base` is already the package directory, so one leading dot is
            # consumed by `node.level` and the rest of the module path follows.
            target = base.joinpath(*module.split(".")) if module else base

            if not target.with_suffix(".py").is_file() and not (
                target / "__init__.py"
            ).is_file():
                rel = source.relative_to(REPO_ROOT)
                failures.append(f"{rel}: from .{module} import ... (module missing)")
                continue

            exported = _exported_names(target)
            if exported is None:
                continue  # module exists but is dynamic; cannot verify names
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name not in exported:
                    rel = source.relative_to(REPO_ROOT)
                    failures.append(
                        f"{rel}: from .{module} import {alias.name} "
                        f"(not defined; module has {sorted(exported)[:12]}...)"
                    )

    assert checked > 10, "no relative imports found - the check is not running"
    assert not failures, "unresolvable relative imports:\n  " + "\n  ".join(failures)


def _exported_names(target: Path) -> set[str] | None:
    """Top-level names defined by a module, or None if it cannot be parsed."""
    path = target.with_suffix(".py")
    if not path.is_file():
        init = target / "__init__.py"
        path = init if init.is_file() else path
    if not path.is_file():
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None

    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.If):
            # `if TYPE_CHECKING:` blocks etc. - recurse shallowly
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        names.add((alias.asname or alias.name).split(".")[0])
    return names


def test_core_does_not_import_homeassistant() -> None:
    """core/ must stay importable without Home Assistant installed."""
    offenders: list[str] = []
    for source in (PACKAGE / "core").rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            if any(n == "homeassistant" or n.startswith("homeassistant.") for n in names):
                offenders.append(f"{source.name}: {names}")
    assert not offenders, "core/ must not import homeassistant:\n  " + "\n  ".join(
        offenders
    )


def test_manifest_declares_config_flow_and_requirements() -> None:
    import json

    manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["domain"] == "ezykam"
    assert manifest["config_flow"] is True
    assert manifest["version"], "manifest needs an explicit version for HACS"

    # Every requirement must be a bare specifier HA can resolve against its
    # own constraint file - a tight upper bound that HA pins past is fatal.
    for requirement in manifest["requirements"]:
        assert not requirement.startswith("av<"), "av upper bound conflicts with HA"
        assert "==" not in requirement.split(">=")[0] or requirement.count("=") == 2, (
            f"over-pinned requirement: {requirement}"
        )


def test_strings_and_translation_stay_in_sync() -> None:
    import json

    strings = json.loads((PACKAGE / "strings.json").read_text(encoding="utf-8"))
    translation = json.loads(
        (PACKAGE / "translations" / "en.json").read_text(encoding="utf-8")
    )
    assert strings == translation, "strings.json and translations/en.json diverged"


def _rendered_steps(flow_text: str) -> tuple[set[str], set[str]]:
    """Steps that actually render UI (form or progress).

    ``async_step_confirm`` only ever returns an entry or an abort, so it never
    renders and needs no translation — it is deliberately excluded by looking
    at what is passed to ``async_show_form`` / ``async_show_progress`` rather
    than at every ``_STEP_*`` constant.
    """
    const_map = dict(re.findall(r'(_STEP_\w+)\s*=\s*"(\w+)"', flow_text))

    def steps_in_calls(text: str) -> set[str]:
        found: set[str] = set()
        for func in ("async_show_form", "async_show_progress"):
            for match in re.finditer(rf"{func}\(([^)]*)", text, re.S):
                args = match.group(1)
                lit = re.search(r'step_id\s*=\s*"(\w+)"', args)
                if lit:
                    found.add(lit.group(1))
                    continue
                named = re.search(r"step_id\s*=\s*(_STEP_\w+)", args)
                if named:
                    found.add(const_map.get(named.group(1), named.group(1)))
        return found

    marker = "class EzykamOptionsFlow"
    if marker in flow_text:
        config_part, _, options_part = flow_text.partition(marker)
    else:
        config_part, options_part = flow_text, ""

    return steps_in_calls(config_part), steps_in_calls(options_part)


def test_flow_step_ids_referenced_in_strings() -> None:
    """Every step that renders UI must be translated."""
    import json

    strings = json.loads((PACKAGE / "strings.json").read_text(encoding="utf-8"))
    flow_text = (PACKAGE / "config_flow.py").read_text(encoding="utf-8")

    config_steps, options_steps = _rendered_steps(flow_text)

    missing_config = config_steps - set(strings["config"]["step"])
    assert not missing_config, (
        f"config steps used but not translated: {sorted(missing_config)}"
    )

    missing_options = options_steps - set(strings["options"]["step"])
    assert not missing_options, (
        f"options steps used but not translated: {sorted(missing_options)}"
    )


def test_abort_reasons_are_translated() -> None:
    import json

    strings = json.loads((PACKAGE / "strings.json").read_text(encoding="utf-8"))
    abort = set(strings["config"]["abort"])
    flow_text = (PACKAGE / "config_flow.py").read_text(encoding="utf-8")

    used = set(re.findall(r'async_abort\(\s*reason="(\w+)"', flow_text))
    missing = used - abort
    assert not missing, f"abort reasons used but not translated: {sorted(missing)}"


def _render_calls(text: str) -> dict[str, set[str]]:
    """Map each rendered step_id to the placeholders its own call provides.

    Uses balanced-paren scanning so ``description_placeholders`` is read from
    the *same* call that names the step. A global "is this placeholder provided
    anywhere" check is not enough: ``qr_image`` is legitimately provided by the
    ``scan`` step, so a naive check passed even when ``reauth_confirm`` also
    referenced it without providing it.
    """
    const_map = dict(re.findall(r'(_STEP_\w+)\s*=\s*"(\w+)"', text))
    provided: dict[str, set[str]] = {}

    for func in ("async_show_form", "async_show_progress"):
        for match in re.finditer(rf"{func}\(", text):
            start = match.end() - 1
            depth = 0
            index = start
            while index < len(text):
                if text[index] == "(":
                    depth += 1
                elif text[index] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                index += 1
            args = text[start + 1 : index]

            literal = re.search(r'step_id\s*=\s*"(\w+)"', args)
            if literal:
                step_id = literal.group(1)
            else:
                named = re.search(r"step_id\s*=\s*(_STEP_\w+)", args)
                if not named:
                    continue
                step_id = const_map.get(named.group(1))
                if step_id is None:
                    continue

            keys: set[str] = set()
            ph = re.search(r"description_placeholders\s*=\s*\{([^}]*)\}", args)
            if ph:
                keys.update(re.findall(r'"(\w+)"\s*:', ph.group(1)))
            provided.setdefault(step_id, set()).update(keys)

    return provided


def _missing_placeholders(
    strings_section: dict, flow_text: str, prefix: str
) -> list[str]:
    provided = _render_calls(flow_text)
    missing: list[str] = []
    for step, spec in strings_section.items():
        step_provided = provided.get(step, set())
        for field in ("title", "description"):
            text = spec.get(field) or ""
            for placeholder in re.findall(r"\{(\w+)\}", text):
                if placeholder not in step_provided:
                    missing.append(
                        f"{prefix}.{step}.{field} uses {{{placeholder}}} but "
                        f"step '{step}' provides {sorted(step_provided) or 'nothing'}"
                    )
    return missing


def test_config_step_placeholders_are_provided() -> None:
    """Every ``{placeholder}`` in a config-step description must be supplied
    *by the step that renders it*.

    A description referencing a variable its step never passes raises
    ``formatjs MISSING_VALUE`` and renders a raw error in the UI. This happened
    for real: ``reauth_confirm`` embedded ``{qr_image}`` even though that step
    renders before any QR code exists.
    """
    import json

    strings = json.loads((PACKAGE / "strings.json").read_text(encoding="utf-8"))
    flow_text = (PACKAGE / "config_flow.py").read_text(encoding="utf-8")

    missing = _missing_placeholders(
        strings.get("config", {}).get("step", {}), flow_text, "config.step"
    )
    assert not missing, "unprovided placeholders:\n  " + "\n  ".join(missing)


def test_options_step_placeholders_are_provided() -> None:
    import json

    strings = json.loads((PACKAGE / "strings.json").read_text(encoding="utf-8"))
    _, _, options_part = (PACKAGE / "config_flow.py").read_text(
        encoding="utf-8"
    ).partition("class EzykamOptionsFlow")

    missing = _missing_placeholders(
        strings.get("options", {}).get("step", {}), options_part, "options.step"
    )
    assert not missing, "unprovided placeholders:\n  " + "\n  ".join(missing)
