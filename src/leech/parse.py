# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Turning source text into an AST: grammar loading and parse-error reporting."""

import pathlib
from collections.abc import Iterable

import lark

from leech import ast, errors, src

_GRAMMAR_PATH = pathlib.Path(__file__).parent / "leech.lark"


def build_parser(start_rule: str) -> lark.Lark:
    """Build a Lark LALR parser for the Leech grammar.

    :param start_rule: The grammar rule to use as the parse entry point.
    :return: A configured, ready-to-use parser.
    """
    return lark.Lark.open(
        str(_GRAMMAR_PATH),
        start=start_rule,
        strict=True,
        propagate_positions=True,
        parser="lalr",
    )


def _describe_expected_tokens(parser: lark.Lark, names: Iterable[str]) -> list[str]:
    """Describe a set of Lark terminal names for a diagnostic message.

    Literal-string terminals (punctuation and keywords) are shown as
    their literal text, e.g. ``'";"'``; other terminals (identifiers,
    literals) are shown by name, e.g. ``'IDENT'``. Lark's end-of-input
    marker is described in words.

    :param parser: The parser ``names`` came from, used to look up each
        terminal's definition.
    :param names: The Lark terminal names to describe.
    :return: The descriptions, sorted and de-duplicated.
    """
    terminals_by_name = {t.name: t for t in parser.terminals}
    descriptions = set[str]()
    for name in names:
        if name == "$END":
            descriptions.add("end of input")
            continue
        term = terminals_by_name.get(name)
        if term is not None and term.pattern.type == "str":
            descriptions.add(f'"{term.pattern.value}"')
        else:
            descriptions.add(name)
    return sorted(descriptions)


def parse_mod_ast(file: src.SrcFile) -> ast.Mod:
    """Parse a source file into a module AST.

    :param file: The source file to parse.
    :return: The parsed module.
    :raises UnexpectedCharacterError: If a character in ``file`` can't
        start any valid token.
    :raises UnexpectedTokenError: If a token in ``file`` doesn't fit the
        grammar at that point (including the input ending too soon).
    """
    parser = build_parser("mod")
    try:
        tree = parser.parse(file.src)
    except lark.UnexpectedCharacters as err:
        span = src.SrcSpan.single_char(file, err.pos_in_stream, err.line, err.column)
        raise errors.UnexpectedCharacterError(err.char, span) from err
    except lark.UnexpectedToken as err:
        span = src.SrcSpan.from_lark_meta(file, err.token)
        found = "end of input" if err.token.type == "$END" else f'token "{err.token}"'
        expected = _describe_expected_tokens(parser, err.accepts or err.expected)
        raise errors.UnexpectedTokenError(found, span, expected) from err
    return ast.Mod(file, tree)
