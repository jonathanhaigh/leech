# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import os
import pathlib
import shutil
import subprocess

import markdown_it
import markdown_it.token
import pytest

from tests import doc

_ROOT = pathlib.Path(__file__).parents[1]
_PAGE = _ROOT / "docs" / "guide" / "getting-started.md"
_PHASE_IDS = ("quickstart-setup", "quickstart-ir", "quickstart-native")


def _tested_bash_fences(markdown: str) -> list[markdown_it.token.Token]:
    fences = []
    for token in markdown_it.MarkdownIt("commonmark").parse(markdown):
        words = token.info.split()
        is_tested = any(word.startswith("test=") for word in words[1:])
        if token.type == "fence" and words[:1] == ["bash"] and is_tested:
            fences.append(token)
    expected = [f"bash test={phase_id}" for phase_id in _PHASE_IDS]
    assert [token.info for token in fences] == expected
    default = "LEECH_BUILD_DIR=${LEECH_BUILD_DIR:-build}"
    assert all(token.content.splitlines()[:1] == [default] for token in fences)
    return fences


def _hello_src(markdown: str) -> str:
    cases = doc.parse_doc_page(_PAGE, markdown)
    matches = [case for case in cases if case.test_id == "quickstart-hello"]
    assert len(matches) == 1
    hello = matches[0]
    assert hello.program.root.path == pathlib.Path("main.leech")
    return hello.program.root.src


def _run_phase(
    token: markdown_it.token.Token, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", token.content],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    message = (
        f"quickstart phase {token.info!r} failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert result.returncode == 0, message
    return result


def test_quickstart_commands(tmp_path):
    markdown = _PAGE.read_text(encoding="utf-8")
    setup, ir, native = _tested_bash_fences(markdown)
    build_dir = tmp_path / "build"
    env = os.environ.copy()
    env["LEECH_BUILD_DIR"] = str(build_dir)

    assert _run_phase(setup, env).stdout == ""
    (build_dir / "main.leech").write_text(_hello_src(markdown), encoding="utf-8")
    assert _run_phase(ir, env).stdout == "Hello, world!\n"

    missing = [tool for tool in ("llc", "cc") if shutil.which(tool) is None]
    if missing:
        message = f"native documentation build requires: {', '.join(missing)}"
        if os.environ.get("LEECH_REQUIRE_NATIVE_DOCS") == "1":
            pytest.fail(message)
        pytest.skip(message)
    assert _run_phase(native, env).stdout == "Hello, world!\n"
