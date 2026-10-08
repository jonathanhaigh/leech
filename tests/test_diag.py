# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib

import pytest

from leech import compilation, diag, errors, program, session
from leech import src as leech_src


def _span(path: pathlib.Path) -> leech_src.SrcSpan:
    return leech_src.SrcSpan(leech_src.SrcFile(path), 0, 1, 1, 1, 1, 2)


def test_only_errors_have_proofs():
    diags = diag.Diags()

    diags.warn(errors.UnreachableCodeWarning("statement", None))
    assert not diags.has_errors
    assert diags.any_error() is None

    reported = diags.error(errors.CcNotFoundError("cc"))
    assert isinstance(reported, diag.ReportProof)
    assert diags.has_errors
    assert diags.any_error() is reported


def test_any_error_is_the_first_error():
    diags = diag.Diags()

    first = diags.error(errors.CcNotFoundError("first"))
    diags.error(errors.CcNotFoundError("second"))

    assert diags.any_error() is first


def test_diags_keep_emission_order():
    diags = diag.Diags()
    warning = errors.UnreachableCodeWarning("statement", None)
    error = errors.CcNotFoundError("cc")

    diags.warn(warning)
    diags.error(error)

    assert diags.all() == (warning, error)


def test_duplicates_are_dropped_and_keep_the_original_proof():
    diags = diag.Diags()
    first = errors.CcNotFoundError("cc")

    reported = diags.error(first)
    again = diags.error(errors.CcNotFoundError("cc"))

    assert again is reported
    assert diags.all() == (first,)


def test_duplicates_compare_source_files_by_resolved_path(tmp_path):
    path = tmp_path / "app.leech"
    diags = diag.Diags()
    first = errors.UnreachableCodeWarning("statement", _span(path))

    diags.warn(first)
    diags.warn(errors.UnreachableCodeWarning("statement", _span(tmp_path / "." / "app.leech")))
    diags.warn(errors.UnreachableCodeWarning("statement", _span(tmp_path / "other.leech")))

    assert len(diags.all()) == 2
    assert diags.all()[0] is first


def test_level_is_the_highest_level():
    diags = diag.Diags()
    assert diags.level == diag.NOTE

    diags.warn(errors.UnreachableCodeWarning("statement", None))
    assert diags.level == diag.WARNING

    diags.error(errors.CcNotFoundError("cc"))
    assert diags.level == diag.ERROR


def test_errors_reexports_diag_levels():
    assert errors.Level is diag.Level
    assert (errors.NOTE, errors.WARNING, errors.ERROR) == (diag.NOTE, diag.WARNING, diag.ERROR)


def test_proof_holds_the_recorded_error():
    diags = diag.Diags()
    first = errors.CcNotFoundError("cc")

    reported = diags.error(first)
    again = diags.error(errors.CcNotFoundError("cc"))

    assert reported.diag is first
    assert again is reported


def test_error_and_warn_reject_the_wrong_level():
    diags = diag.Diags()

    with pytest.raises(AssertionError, match="not an error"):
        diags.error(errors.UnreachableCodeWarning("statement", None))
    with pytest.raises(AssertionError, match="not a warning"):
        diags.warn(errors.CcNotFoundError("cc"))
    assert diags.all() == ()


def test_report_proof_cannot_be_created_outside_diags():
    with pytest.raises(AssertionError):
        diag.ReportProof(object(), errors.CcNotFoundError("cc"))


def test_reported_error_carries_its_proof():
    reported = diag.Diags().error(errors.CcNotFoundError("cc"))

    assert diag.ReportedError(reported).reported is reported


def test_ctx_reports_to_its_sessions_diags():
    compiler_session = session.Session()

    assert compilation.Ctx(compiler_session).session is compiler_session
    assert compilation.Ctx(compiler_session).diags is compiler_session.diags
    assert compilation.Ctx().diags is not compilation.Ctx().diags


def test_programs_have_independent_diags(tmp_path):
    warned_path = tmp_path / "warned.leech"
    warned_path.write_text("pub fn f() i32 { return 1; return 2; }\n")
    clean_path = tmp_path / "clean.leech"
    clean_path.write_text("pub fn f() i32 { return 1; }\n")
    warned_diags = diag.Diags()
    clean_diags = diag.Diags()

    warned = program.Program(warned_path, entry=False).check(session.Session(warned_diags))
    clean = program.Program(clean_path, entry=False).check(session.Session(clean_diags))
    warned.llvm_ir()
    clean.llvm_ir()

    assert [type(d) for d in warned_diags.all()] == [errors.UnreachableCodeWarning]
    assert clean_diags.all() == ()


def test_raised_error_is_reported_after_earlier_warnings(tmp_path):
    path = tmp_path / "app.leech"
    path.write_text(
        "enum E { A }\n"
        "pub fn f(e: E) i32 { return match (e) { E::A => 1i32, _ => 2i32, }; }\n"
        "pub fn g() i32 { return true; }\n"
    )
    diags = diag.Diags()

    with pytest.raises(errors.InvalidRetTypError):
        program.Program(path, entry=False).check(session.Session(diags))

    assert [type(d) for d in diags.all()] == [
        errors.UnreachableMatchArmWarning,
        errors.InvalidRetTypError,
    ]
    assert diags.has_errors
