"""Pin local-verify pyright to CI's full-tree scope so it can't silently re-drift.

Both `scripts/verify.sh` and `scripts/verify.ps1` must run a *full-tree* pyright
(`pyright` with no per-file path arguments), exactly like the CI static job in
`.github/workflows/ci.yml`. Neither verify script may delegate to the fast
changed-files helper `scripts/ci_pyright_changed.sh`.

This is a plain unit test — it only reads repo files, needs no database, and is
robust to whitespace/formatting. It FAILS the moment verify is reverted to a
changed-files pyright invocation. Repo-root paths are resolved relative to this
file so the test runs from any working directory.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VERIFY_SH = _REPO_ROOT / "scripts" / "verify.sh"
_VERIFY_PS1 = _REPO_ROOT / "scripts" / "verify.ps1"
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Punctuation/whitespace that carries no path/file meaning. What remains after
# stripping these from the text trailing a `pyright` token distinguishes a bare
# full-tree call ("") from a changed-files call (`$pyFiles`, `${files[@]}`, a
# `src/...py` path, etc., which leave non-empty residue).
_HARMLESS = " \t\r\"'()@;`,"


def _pyright_command_lines(text: str) -> list[str]:
    """Lines that actually *invoke* pyright (not comments, echoed messages, or YAML step names)."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if not line or line.startswith("#"):
            continue
        if low.startswith(("echo ", 'echo"', "write-host", "- name:", "name:")):
            continue
        if "pyright" in low:
            out.append(line)
    return out


def _args_after_pyright(line: str) -> str:
    """Whatever trails the last `pyright` token on a command line."""
    idx = line.lower().rfind("pyright")
    return line[idx + len("pyright") :]


def _is_full_tree(line: str) -> bool:
    """True when the pyright invocation passes no file/path arguments."""
    tail = _args_after_pyright(line)
    for ch in _HARMLESS:
        tail = tail.replace(ch, "")
    return tail == ""


def _assert_full_tree_pyright(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    cmd_lines = _pyright_command_lines(text)
    assert cmd_lines, f"{path.name}: expected at least one pyright invocation, found none"
    for line in cmd_lines:
        assert _is_full_tree(line), f"{path.name}: pyright must run full-tree (no per-file args), got: {line!r}"


def test_verify_sh_runs_full_tree_pyright() -> None:
    _assert_full_tree_pyright(_VERIFY_SH)


def test_verify_ps1_runs_full_tree_pyright() -> None:
    _assert_full_tree_pyright(_VERIFY_PS1)


def test_ci_yml_runs_full_tree_pyright() -> None:
    _assert_full_tree_pyright(_CI_YML)


def test_verify_scripts_do_not_use_changed_files_helper() -> None:
    for path in (_VERIFY_SH, _VERIFY_PS1):
        text = path.read_text(encoding="utf-8")
        assert "ci_pyright_changed" not in text, (
            f"{path.name}: verify must not delegate to the changed-files helper "
            f"(ci_pyright_changed.sh) — that breaks CI parity"
        )
