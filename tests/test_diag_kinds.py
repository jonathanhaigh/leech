# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import re

import pytest

from leech import diag, diag_kinds, errors

_KEBAB_CASE = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
_ABBREVIATIONS = {
    "arg",
    "args",
    "cond",
    "decl",
    "defn",
    "expr",
    "fn",
    "lit",
    "mod",
    "op",
    "param",
    "ptr",
    "ret",
    "typ",
    "typs",
    "var",
}
_VAGUE_WORDS = {"bad", "error", "invalid", "warning", "wrong"}
_TEMPLATES = [kind.template for kind in (*diag_kinds.DIAG_KINDS, *diag_kinds.MSG_KINDS)]


def _user_error_classes() -> list[type[errors.UserError]]:
    classes = []
    pending = list(errors.UserError.__subclasses__())
    while pending:
        cls = pending.pop()
        classes.append(cls)
        pending.extend(cls.__subclasses__())
    return classes


def test_names_and_aliases_are_unique_kebab_case():
    names = [name for kind in diag_kinds.DIAG_KINDS for name in (kind.name, *kind.aliases)]
    assert len(names) == len(set(names))
    for name in names:
        assert _KEBAB_CASE.fullmatch(name), name


@pytest.mark.parametrize("template", _TEMPLATES)
def test_template_fields_are_plain_names(template):
    diag.template_fields(template)


@pytest.mark.parametrize("template", _TEMPLATES)
def test_template_starts_with_a_lowercase_word_or_a_quote(template):
    assert re.match(r'[a-z"]', template), template


@pytest.mark.parametrize("template", _TEMPLATES)
def test_template_has_no_trailing_period(template):
    assert not template.endswith("."), template


def test_label_and_note_templates_are_distinct():
    templates = [kind.template for kind in diag_kinds.MSG_KINDS]
    assert len(templates) == len(set(templates))


def test_lookup_finds_a_kind_by_name():
    assert diag_kinds.lookup("unknown-name") is diag_kinds.UNKNOWN_NAME
    assert diag_kinds.lookup("no-such-diagnostic") is None


def test_lookup_finds_a_kind_by_alias(monkeypatch):
    renamed = diag.DiagKind("new-name", diag.ERROR, "message", aliases=("old-name",))
    monkeypatch.setattr(diag_kinds, "DIAG_KINDS", (*diag_kinds.DIAG_KINDS, renamed))

    assert diag_kinds.lookup("old-name") is renamed
    assert diag_kinds.lookup("new-name") is renamed


def test_every_user_error_class_has_its_own_kind():
    classes = _user_error_classes()
    for cls in classes:
        assert "kind" in vars(cls), cls
        assert cls.kind in diag_kinds.DIAG_KINDS, cls
    assert len({cls.kind for cls in classes}) == len(classes)


def test_constant_is_named_after_its_kind():
    for constant, value in vars(diag_kinds).items():
        if isinstance(value, diag.DiagKind):
            assert constant == value.name.upper().replace("-", "_"), constant


@pytest.mark.parametrize("kind", diag_kinds.DIAG_KINDS, ids=lambda kind: kind.name)
def test_name_uses_whole_words_and_says_what_is_wrong(kind):
    words = set(kind.name.split("-"))
    assert not words & _ABBREVIATIONS, kind.name
    assert not words & _VAGUE_WORDS, kind.name
