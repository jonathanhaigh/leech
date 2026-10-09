# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib

import pytest

from leech import compilation, diag, diag_kinds, errors, program, session, typs
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


_ARG_KIND = diag.DiagKind("test-arg", diag.ERROR, 'cannot use "{name}" of type "{typ}"')
_NOTE_KIND = diag.MsgKind('"{name}" defined here')


def test_template_fields_are_the_replacement_field_names():
    assert diag.template_fields('cannot use "{name}" of type "{typ}"') == {"name", "typ"}
    assert diag.template_fields("no fields") == frozenset()


@pytest.mark.parametrize("template", ["{a.b}", "{a[0]}", "{a!r}", "{a:>4}", "{0}"])
def test_template_fields_reject_formatting_and_non_names(template):
    with pytest.raises(AssertionError):
        diag.template_fields(template)


def test_msg_arguments_must_match_the_template_fields():
    with pytest.raises(AssertionError, match="do not match"):
        diag.Msg(_ARG_KIND, {"name": "x"})
    with pytest.raises(AssertionError, match="do not match"):
        diag.Msg(_ARG_KIND, {"name": "x", "typ": "i32", "extra": 1})


def test_msg_arguments_cannot_change_after_construction():
    args: dict[str, diag.DiagArgValue] = {"name": "x", "typ": "i32"}
    msg = diag.Msg(_ARG_KIND, args)

    args["name"] = "y"
    with pytest.raises(TypeError):
        msg.args["name"] = "y"  # pyright: ignore[reportIndexIssue]

    assert msg.text() == 'cannot use "x" of type "i32"'


def test_msg_text_renders_types_with_diag_str():
    assert diag.Msg(_ARG_KIND, {"name": "x", "typ": typs.I32}).text() == (
        'cannot use "x" of type "i32"'
    )
    assert diag.Msg(diag.MsgKind("got {n}"), {"n": 3}).text() == "got 3"


def test_types_have_no_report_proof():
    assert typs.I32.report_proof() is None


def test_diag_new_takes_the_kind_level_and_adds_labels_and_notes_in_order(tmp_path):
    span = _span(tmp_path / "a.leech")
    other = _span(tmp_path / "b.leech")

    d = (
        diag.Diag.new(_ARG_KIND, span, name="x", typ=typs.BOOL)
        .with_primary_label(diag.MsgKind('has type "{typ}"'), typ=typs.BOOL)
        .with_label(other)
        .with_label(other, _NOTE_KIND, name="x")
        .with_note(_NOTE_KIND, other, name="x")
        .with_note(diag.MsgKind("a spanless note"))
    )

    assert d.kind is _ARG_KIND
    assert d.level == diag.ERROR
    assert d.span is span
    assert d.promoted_by is None
    assert d.msg.text() == 'cannot use "x" of type "bool"'
    assert d.primary_label is not None and d.primary_label.text() == 'has type "bool"'
    assert [(label.span, label.msg and label.msg.text()) for label in d.labels] == [
        (other, None),
        (other, '"x" defined here'),
    ]
    assert [(note.msg.text(), note.span) for note in d.notes] == [
        ('"x" defined here', other),
        ("a spanless note", None),
    ]


def test_diag_message_needs_a_diag_kind():
    with pytest.raises(AssertionError):
        diag.Diag(diag.Msg(_NOTE_KIND, {"name": "x"}), None, diag.ERROR)


def test_unlabelled_span_takes_no_arguments(tmp_path):
    d = diag.Diag.new(_ARG_KIND, None, name="x", typ="i32")
    with pytest.raises(AssertionError):
        d.with_label(_span(tmp_path / "a.leech"), None, name="x")


def test_compilation_failed_lists_kinds_in_order():
    error = diag.Diag.new(_ARG_KIND, None, name="x", typ="i32")
    warning = errors.UnreachableCodeWarning("statement", None)

    failed = diag.CompilationError([warning, error])

    assert failed.diags == (warning, error)
    assert failed.kinds == (diag_kinds.UNREACHABLE_CODE, _ARG_KIND)


def test_compilation_failed_needs_an_error():
    with pytest.raises(AssertionError):
        diag.CompilationError([errors.UnreachableCodeWarning("statement", None)])
