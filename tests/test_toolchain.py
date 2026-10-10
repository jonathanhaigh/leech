# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import diag, diag_kinds, toolchain


def test_missing_c_compiler_suggests_installing_one(monkeypatch):
    monkeypatch.setenv("CC", "/nonexistent/cc --flag")
    diags = diag.Diags()

    with pytest.raises(diag.ReportedError) as exc_info:
        toolchain.Linker.from_env(diags)

    d = exc_info.value.reported.diag
    assert d.kind is diag_kinds.MISSING_C_COMPILER
    assert d.msg.args["program"] == "/nonexistent/cc"
    assert [note.msg.kind for note in d.notes] == [diag_kinds.INSTALL_CC]
    assert diags.all() == (d,)


def test_link_failure_shows_the_c_compilers_output(tmp_path):
    linker = toolchain.Linker(("sh", "-c", "echo cannot link; exit 3", "cc"))
    diags = diag.Diags()

    with pytest.raises(diag.ReportedError) as exc_info:
        linker.link(tmp_path / "app.o", tmp_path / "app", diags)

    d = exc_info.value.reported.diag
    assert d.kind is diag_kinds.LINK_FAILURE
    assert "status 3" in str(d)
    (note,) = d.notes
    assert note.msg.kind is diag_kinds.LINKER_OUTPUT
    assert note.msg.args["output"] == "cannot link"


def test_link_failure_without_output_has_no_note(tmp_path):
    diags = diag.Diags()

    with pytest.raises(diag.ReportedError) as exc_info:
        toolchain.Linker(("false",)).link(tmp_path / "app.o", tmp_path / "app", diags)

    d = exc_info.value.reported.diag
    assert d.kind is diag_kinds.LINK_FAILURE
    assert d.notes == ()
