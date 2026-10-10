# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib
from collections.abc import Iterator
from typing import Final, Optional

import pytest

from leech import diag, diag_kinds
from tests import doc, harness

pytest_plugins = ("pytester",)


@pytest.fixture(autouse=True)
def _private_cache_home(monkeypatch, tmp_path_factory) -> None:
    """Keep ``leech run``'s executables, in this process and its children, out of the user's
    cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("cache")))


@pytest.fixture
def compiler(tmp_path: pathlib.Path) -> harness.CompilerHarness:
    return harness.CompilerHarness(tmp_path)


_EMITTED_KINDS: Final = pytest.StashKey[set[diag.DiagKind | diag.MsgKind]]()
_UNEMITTED_KINDS: Final = pytest.StashKey[list[diag.DiagKind | diag.MsgKind]]()
_DESELECTED: Final = pytest.StashKey[bool]()


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_EMITTED_KINDS] = set()
    config.stash[_DESELECTED] = False


@pytest.fixture(autouse=True, scope="session")
def _recording_emitted_kinds(request: pytest.FixtureRequest) -> Iterator[None]:
    """Record the kind of every diagnostic reported to any sink, and of its labels and
    notes."""
    emitted = request.config.stash[_EMITTED_KINDS]
    record = diag.Diags._record

    def recording(diags: diag.Diags, d: diag.Diag) -> Optional[diag.ReportProof]:
        emitted.add(d.kind)
        if d.primary_label is not None:
            emitted.add(d.primary_label.kind)
        emitted.update(label.msg.kind for label in d.labels)
        emitted.update(note.msg.kind for note in d.notes)
        return record(diags, d)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(diag.Diags, "_record", recording)
        yield


def pytest_deselected(items: list[pytest.Item]) -> None:
    if items:
        items[0].config.stash[_DESELECTED] = True


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail an unfiltered run in which a catalogue entry was never emitted."""
    config = session.config
    if not _ran_every_test(session, exitstatus):
        return
    emitted = config.stash[_EMITTED_KINDS]
    unemitted = [
        kind for kind in (*diag_kinds.DIAG_KINDS, *diag_kinds.MSG_KINDS) if kind not in emitted
    ]
    if unemitted:
        config.stash[_UNEMITTED_KINDS] = unemitted
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _ran_every_test(session: pytest.Session, exitstatus: int) -> bool:
    """Whether the run collected every test, selected all of them, and ran them all."""
    config = session.config
    return (
        all(pathlib.Path(arg).resolve() == config.rootpath for arg in config.args)
        and not config.stash[_DESELECTED]
        and not config.option.collectonly
        and not session.shouldfail
        and not session.shouldstop
        and exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED)
    )


def pytest_terminal_summary(terminalreporter, exitstatus: int, config: pytest.Config) -> None:
    del exitstatus
    unemitted = config.stash.get(_UNEMITTED_KINDS, [])
    if unemitted:
        terminalreporter.section("unemitted diagnostic kinds", red=True)
        for kind in unemitted:
            terminalreporter.write_line(_kind_description(kind))


def _kind_description(kind: diag.DiagKind | diag.MsgKind) -> str:
    match kind:
        case diag.DiagKind():
            return f"no test emits the diagnostic {kind.name}"
        case diag.MsgKind():
            return f"no test emits a label or note {kind.template!r}"


def pytest_collect_file(file_path: pathlib.Path, parent: pytest.Collector) -> Optional[pytest.File]:
    if doc.is_public_doc_path(file_path, parent.config.rootpath):
        return doc.DocFile.from_parent(parent, path=file_path)
    return None
