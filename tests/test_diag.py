# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import dataclasses
import pathlib
from typing import Optional

import pytest

from leech import compilation, diag, diag_kinds, diag_text, program, session, typs
from leech import src as leech_src


def _span(path: pathlib.Path) -> leech_src.SrcSpan:
    return leech_src.SrcSpan(leech_src.SrcFile(path), 0, 1, 1, 1, 1, 2)


def _error(program: str) -> diag.Diag:
    return diag.Diag.new(diag_kinds.MISSING_C_COMPILER, None, program=program)


def _warning(span: Optional[leech_src.SrcSpan]) -> diag.Diag:
    return diag.Diag.new(diag_kinds.UNREACHABLE_CODE, span, code="statement")


def test_only_errors_have_proofs():
    diags = diag.Diags()

    diags.warn(_warning(None))
    assert not diags.has_errors
    assert diags.any_error() is None

    reported = diags.error(_error("cc"))
    assert isinstance(reported, diag.ReportProof)
    assert diags.has_errors
    assert diags.any_error() is reported


def test_any_error_is_the_first_error():
    diags = diag.Diags()

    first = diags.error(_error("first"))
    diags.error(_error("second"))

    assert diags.any_error() is first


def test_diags_keep_emission_order():
    diags = diag.Diags()
    warning = _warning(None)
    error = _error("cc")

    diags.warn(warning)
    diags.error(error)

    assert diags.all() == (warning, error)


def test_duplicates_are_dropped_and_keep_the_original_proof():
    diags = diag.Diags()
    first = _error("cc")

    reported = diags.error(first)
    again = diags.error(_error("cc"))

    assert again is reported
    assert diags.all() == (first,)


def test_duplicates_compare_source_files_by_resolved_path(tmp_path):
    path = tmp_path / "app.leech"
    diags = diag.Diags()
    first = _warning(_span(path))

    diags.warn(first)
    diags.warn(_warning(_span(tmp_path / "." / "app.leech")))
    diags.warn(_warning(_span(tmp_path / "other.leech")))

    assert len(diags.all()) == 2
    assert diags.all()[0] is first


def test_level_is_the_highest_level():
    diags = diag.Diags()
    assert diags.level == diag.NOTE

    diags.warn(_warning(None))
    assert diags.level == diag.WARNING

    diags.error(_error("cc"))
    assert diags.level == diag.ERROR


def test_proof_holds_the_recorded_error():
    diags = diag.Diags()
    first = _error("cc")

    reported = diags.error(first)
    again = diags.error(_error("cc"))

    assert reported.diag is first
    assert again is reported


def test_error_warn_and_note_reject_the_wrong_level():
    diags = diag.Diags()

    with pytest.raises(AssertionError, match="not an error"):
        diags.error(_warning(None))
    with pytest.raises(AssertionError, match="not a warning"):
        diags.warn(_error("cc"))
    with pytest.raises(AssertionError, match="not a note"):
        diags.note(_warning(None))
    assert diags.all() == ()


def test_a_note_is_recorded_without_failing():
    diags = diag.Diags()

    diags.note(diag_kinds.C_COMPILER_HINT, None)

    assert [d.kind for d in diags.all()] == [diag_kinds.C_COMPILER_HINT]
    assert diags.level == diag.NOTE
    assert not diags.has_errors


def test_report_proof_cannot_be_created_outside_diags():
    with pytest.raises(AssertionError):
        diag.ReportProof(object(), _error("cc"))


def test_reported_error_carries_its_proof():
    reported = diag.Diags().error(_error("cc"))

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

    assert [d.kind for d in warned_diags.all()] == [diag_kinds.UNREACHABLE_CODE]
    assert clean_diags.all() == ()


def test_compilation_error_holds_every_diagnostic_in_the_session(tmp_path):
    path = tmp_path / "app.leech"
    path.write_text(
        "enum E { A }\n"
        "pub fn f(e: E) i32 { return match (e) { E::A => 1i32, _ => 2i32, }; }\n"
        "pub fn g() i32 { return true; }\n"
    )
    diags = diag.Diags()

    with pytest.raises(diag.CompilationError) as exc_info:
        program.Program(path, entry=False).check(session.Session(diags))
    assert exc_info.value.kinds == (
        diag_kinds.UNREACHABLE_MATCH_ARM,
        diag_kinds.RETURN_TYPE_MISMATCH,
    )
    assert exc_info.value.diags == diags.sorted()


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
        .with_label(_NOTE_KIND, other, name="x")
        .with_note(_NOTE_KIND, other, name="x")
        .with_note(diag.MsgKind("a spanless note"))
    )

    assert d.kind is _ARG_KIND
    assert d.level == diag.ERROR
    assert d.span is span
    assert d.promoted_by is None
    assert d.msg.text() == 'cannot use "x" of type "bool"'
    assert d.primary_label is not None and d.primary_label.text() == 'has type "bool"'
    assert [(label.span, label.msg.text()) for label in d.labels] == [(other, '"x" defined here')]
    assert [(note.msg.text(), note.span) for note in d.notes] == [
        ('"x" defined here', other),
        ("a spanless note", None),
    ]


def test_label_without_a_span_is_left_out(tmp_path):
    d = diag.Diag.new(_ARG_KIND, _span(tmp_path / "a.leech"), name="x", typ="i32")

    assert d.with_label(_NOTE_KIND, None, name="x") is d


def test_diag_message_needs_a_diag_kind():
    with pytest.raises(AssertionError):
        diag.Diag(diag.Msg(_NOTE_KIND, {"name": "x"}), None, diag.ERROR)


def test_compilation_error_lists_kinds_in_order():
    warning = _warning(None)
    error = _error("cc")

    failed = diag.CompilationError([warning, error])

    assert failed.diags == (warning, error)
    assert failed.kinds == (diag_kinds.UNREACHABLE_CODE, diag_kinds.MISSING_C_COMPILER)


def test_compilation_error_needs_an_error():
    with pytest.raises(AssertionError):
        diag.CompilationError([_warning(None)])


def test_diag_str_is_its_message(tmp_path):
    d = diag.Diag.new(_ARG_KIND, _span(tmp_path / "a.leech"), name="x", typ=typs.BOOL)

    assert str(d) == 'cannot use "x" of type "bool"'


def test_diags_with_the_same_kind_messages_and_spans_are_duplicates(tmp_path):
    span = _span(tmp_path / "a.leech")
    other = _span(tmp_path / "b.leech")

    def make(name: str, note_span: leech_src.SrcSpan) -> diag.Diag:
        return (
            diag.Diag.new(_ARG_KIND, span, name=name, typ=typs.BOOL)
            .with_label(_NOTE_KIND, other, name=name)
            .with_note(_NOTE_KIND, note_span, name=name)
        )

    diags = diag.Diags()
    reported = diags.error(make("x", other))

    # Arguments are compared as rendered, and source files by resolved path.
    duplicate = make("x", _span(tmp_path / "." / "b.leech"))
    duplicate = dataclasses.replace(
        duplicate, msg=diag.Msg(_ARG_KIND, {"name": "x", "typ": "bool"})
    )
    assert diags.error(duplicate) is reported
    assert diags.error(make("y", other)) is not reported
    assert diags.error(make("x", span)) is not reported
    assert len(diags.all()) == 3


def test_spanless_diags_sort_after_spanned_ones(tmp_path):
    path = tmp_path / "a.leech"
    file = leech_src.SrcFile(path)
    late = _error("cc")
    later_span = leech_src.SrcSpan(file, 5, 6, 2, 2, 1, 2)
    later = _warning(later_span)
    early = diag.Diag.new(_ARG_KIND, _span(path), name="x", typ="i32")
    diags = diag.Diags()

    diags.error(late)
    diags.warn(later)
    diags.error(early)

    assert diags.sorted() == (early, later, late)


def test_text_renderer_shows_a_diags_labels_and_notes_as_notes(tmp_path, capsys):
    path = tmp_path / "a.leech"
    path.write_text("fn f() {}\n")
    file = leech_src.SrcFile(path)
    fn_span = leech_src.SrcSpan(file, 0, 2, 1, 1, 1, 3)
    name_span = leech_src.SrcSpan(file, 3, 4, 1, 1, 4, 5)
    d = (
        diag.Diag.new(_ARG_KIND, name_span, name="f", typ="i32")
        .with_primary_label(_NOTE_KIND, name="p")
        .with_label(_NOTE_KIND, fn_span, name="l")
        .with_note(_NOTE_KIND, name="n")
        .with_note(_NOTE_KIND, fn_span, name="s")
    )

    diag_text.TextRenderer().display_diags([d])

    excerpt = "1| fn f() {}\n"
    assert capsys.readouterr().err == (
        'ERROR: cannot use "f" of type "i32"\n'
        f"{excerpt}------^\n"
        'NOTE: "p" defined here\n'
        f"{excerpt}------^\n"
        'NOTE: "l" defined here\n'
        f"{excerpt}---^\n"
        'NOTE: "n" defined here\n'
        'NOTE: "s" defined here\n'
        f"{excerpt}---^\n"
    )


def test_error_and_warn_can_build_the_diagnostic(tmp_path):
    span = _span(tmp_path / "a.leech")
    diags = diag.Diags()

    reported = diags.error(_ARG_KIND, span, name="x", typ=typs.BOOL)
    diags.warn(diag_kinds.UNREACHABLE_CODE, span, code="statement")

    error, warning = diags.all()
    assert reported.diag is error
    assert (error.kind, error.span, str(error)) == (
        _ARG_KIND,
        span,
        'cannot use "x" of type "bool"',
    )
    assert (warning.kind, warning.span) == (diag_kinds.UNREACHABLE_CODE, span)


@pytest.mark.parametrize("built", [False, True])
def test_raise_error_records_the_error_and_raises_its_proof(built):
    diags = diag.Diags()
    d = diag.Diag.new(_ARG_KIND, None, name="x", typ="i32")

    with pytest.raises(diag.ReportedError) as exc_info:
        if built:
            diags.raise_error(d)
        else:
            diags.raise_error(_ARG_KIND, None, name="x", typ="i32")

    assert exc_info.value.reported is diags.any_error()
    (recorded,) = diags.all()
    assert exc_info.value.reported.diag is recorded
    assert str(recorded) == str(d)


def test_a_built_diagnostic_takes_no_other_arguments(tmp_path):
    d = diag.Diag.new(_ARG_KIND, None, name="x", typ="i32")
    diags = diag.Diags()

    with pytest.raises(AssertionError):
        diags.error(d, _span(tmp_path / "a.leech"))  # pyright: ignore[reportArgumentType]
    with pytest.raises(AssertionError):
        diags.error(d, name="y")  # pyright: ignore[reportCallIssue]
