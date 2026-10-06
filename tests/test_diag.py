# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib

import pytest

from leech import diag, driver, errors, ir_loader
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


def test_merge_keeps_one_of_each_in_emission_order():
    warning = errors.UnreachableCodeWarning("statement", None)
    error = errors.CcNotFoundError("cc")
    first = diag.Diags()
    first.warn(warning)
    first.error(error)
    second = diag.Diags()
    second.error(errors.CcNotFoundError("cc"))
    second.error(errors.CcNotFoundError("other"))
    merged = diag.Diags()

    merged.merge(first)
    merged.merge(second)

    assert [d.message.message for d in merged.all()] == [
        warning.message.message,
        error.message.message,
        errors.CcNotFoundError("other").message.message,
    ]
    assert merged.all()[:2] == (warning, error)


def test_merge_keeps_the_other_diags_proofs():
    first = diag.Diags()
    reported = first.error(errors.CcNotFoundError("cc"))
    merged = diag.Diags()

    merged.merge(first)

    assert merged.any_error() is reported
    assert merged.error(errors.CcNotFoundError("cc")) is reported


def test_merge_keeps_the_existing_proof_of_a_duplicate():
    merged = diag.Diags()
    existing = merged.error(errors.CcNotFoundError("cc"))
    other = diag.Diags()
    other_proof = other.error(errors.CcNotFoundError("cc"))

    merged.merge(other)

    assert existing is not other_proof
    assert merged.any_error() is existing
    assert merged.error(errors.CcNotFoundError("cc")) is existing


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


def test_loader_emits_to_the_given_diags():
    diags = diag.Diags()

    assert ir_loader.ModLoader(diags).ctx.diags is diags
    assert ir_loader.ModLoader().ctx.diags is not ir_loader.ModLoader().ctx.diags


def test_interleaved_compilations_have_independent_diags(tmp_path):
    warned_path = tmp_path / "warned.leech"
    warned_path.write_text("pub fn f() i32 { return 1; return 2; }\n")
    clean_path = tmp_path / "clean.leech"
    clean_path.write_text("pub fn f() i32 { return 1; }\n")
    warned_diags = diag.Diags()
    clean_diags = diag.Diags()

    warned = driver.compile_to_ir(leech_src.SrcFile(warned_path), diags=warned_diags)
    clean = driver.compile_to_ir(leech_src.SrcFile(clean_path), diags=clean_diags)
    driver.lower_to_llvm_ir(warned)
    driver.lower_to_llvm_ir(clean)

    assert [type(d) for d in warned_diags.all()] == [errors.UnreachableCodeWarning]
    assert clean_diags.all() == ()


def test_raised_error_leaves_earlier_warnings_in_the_given_diags(tmp_path):
    path = tmp_path / "app.leech"
    path.write_text(
        "enum E { A }\n"
        "pub fn f(e: E) i32 { return match (e) { E::A => 1i32, _ => 2i32, }; }\n"
        "pub fn g() i32 { return true; }\n"
    )
    diags = diag.Diags()

    with pytest.raises(errors.InvalidRetTypError):
        driver.compile_to_ir(leech_src.SrcFile(path), diags=diags)

    assert [type(d) for d in diags.all()] == [errors.UnreachableMatchArmWarning]
    assert not diags.has_errors
