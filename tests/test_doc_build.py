# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import os
import pathlib
import signal
import subprocess
import sys
import time

import markdown_it
import markdown_it.token
import pytest

from tests import doc

_ROOT = pathlib.Path(__file__).parents[1]
_PAGE = _ROOT / "docs" / "guide" / "getting-started.md"
_BUILD_FENCE_ID = "quickstart-build"
_HELLO_TEST_ID = "quickstart-hello"
_TIMEOUT_SECONDS = 60


def _tested_bash_fence(markdown: str) -> markdown_it.token.Token:
    """Return the page's one executed Bash fence, which must be ``bash test=quickstart-build``.

    Bash fences without ``test=`` are explanatory and ignored.
    """
    fences = []
    for token in markdown_it.MarkdownIt("commonmark").parse(markdown):
        words = token.info.split()
        is_tested = any(word.startswith("test=") for word in words[1:])
        if token.type == "fence" and words[:1] == ["bash"] and is_tested:
            fences.append(token)
    infos = [token.info.strip() for token in fences]
    expected = f"bash test={_BUILD_FENCE_ID}"
    if infos != [expected]:
        raise ValueError(f"expected exactly one tested Bash fence, {expected!r}; got {infos!r}")
    return fences[0]


def _run_fence(
    token: markdown_it.token.Token, cwd: pathlib.Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    """Run a Bash fence in ``cwd`` with the checkout's ``leech`` first on ``PATH``.

    The fence runs in its own session, so a timeout kills every process it started.
    """
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(pathlib.Path(sys.executable).parent), env["PATH"]))
    command = ["bash", "-eu", "-o", "pipefail", "-c", token.content]
    with subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"Bash fence {token.info.strip()!r} timed out after {timeout} seconds\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            ) from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def test_quickstart_build(tmp_path):
    markdown = _PAGE.read_text(encoding="utf-8")
    fence = _tested_bash_fence(markdown)
    (hello,) = [
        case for case in doc.parse_doc_page(_PAGE, markdown) if case.test_id == _HELLO_TEST_ID
    ]
    assert hello.program.mods == ()
    root = hello.program.root
    (tmp_path / root.path).write_text(root.src, encoding="utf-8")

    result = _run_fence(fence, tmp_path, _TIMEOUT_SECONDS)

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.stdout == hello.expected_output


@pytest.mark.parametrize(
    "markdown",
    (
        "```bash\nleech build hello.leech\n```\n",
        "```bash test=quickstart-build\none\n```\n```bash test=quickstart-build\ntwo\n```\n",
        "```bash test=quickstart-run\nleech run hello.leech\n```\n",
        "```bash test=quickstart-build\none\n```\n```bash test=other\ntwo\n```\n",
    ),
)
def test_tested_bash_fence_rejects_page(markdown):
    with pytest.raises(ValueError, match="expected exactly one tested Bash fence"):
        _tested_bash_fence(markdown)


def test_tested_bash_fence_ignores_untested_fences():
    fence = _tested_bash_fence(
        "```bash\nleech doctor\n```\n```bash test=quickstart-build\n./hello\n```\n"
    )

    assert fence.content == "./hello\n"


def _process_is_gone(pid: int) -> bool:
    """Return whether ``pid`` has exited, counting an unreaped zombie as exited."""
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return True
    return stat.rpartition(")")[2].split()[0] == "Z"


def test_run_fence_kills_every_process_on_timeout(tmp_path):
    (fence,) = markdown_it.MarkdownIt("commonmark").parse(
        "```bash\necho started\nsleep 60 &\necho $! >sleep.pid\nwait\n```\n"
    )

    with pytest.raises(AssertionError, match=r"(?s)timed out after 1 seconds.*started"):
        _run_fence(fence, tmp_path, 1)

    pid = int((tmp_path / "sleep.pid").read_text())
    deadline = time.monotonic() + 5
    while not _process_is_gone(pid):
        assert time.monotonic() < deadline, f"process {pid} survived the timeout"
        time.sleep(0.05)
