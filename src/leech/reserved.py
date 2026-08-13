# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Names no declaration may take, in any namespace.

Reserving in every namespace is stricter than strictly necessary - ``let
i32 = 5;`` is harmless in principle - but a uniform rule is easier to
explain, and easier to relax later than to tighten.
"""

from typing import Final

from leech import ast, errors

#: The name of the type an impl block or trait method is written against,
#: bound per context rather than declared.
SELF_TYP_NAME: Final[str] = "Self"

#: Every keyword the grammar spells as a literal token. Written out rather
#: than derived from the parser, so it needs updating whenever the grammar
#: gains a keyword.
KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "and",
        "break",
        "continue",
        "else",
        "enum",
        "extern",
        "false",
        "fn",
        "for",
        "if",
        "impl",
        "import",
        "let",
        "mut",
        "not",
        "or",
        "pub",
        "return",
        "self",
        "struct",
        "trait",
        "true",
        "while",
    }
)

#: Type names the compiler binds itself. Sized integer types are matched by
#: spelling instead, being unbounded in number.
TYP_NAMES: Final[frozenset[str]] = frozenset({SELF_TYP_NAME, "bool", "isize", "never", "usize"})


def is_reserved(name: str) -> bool:
    """Return whether ``name`` is reserved and so unavailable to declarations.

    Reservation is a question about spelling alone: a sized integer type
    name stays reserved even when its width is too large to build a type
    from.
    """
    # Local because typs reaches this module back through ir_env.
    from leech import typs  # noqa: PLC0415

    return name in KEYWORDS or name in TYP_NAMES or typs.IntTyp.is_name(name)


def check_fn_params(fn_ast: ast.FnSpec) -> None:
    """Check a function's declared parameters, which exclude any receiver.

    :raises ReservedNameError: If a parameter takes a reserved name.
    """
    for param_ast in fn_ast.params:
        if is_reserved(param_ast.name.name):
            raise errors.ReservedNameError(param_ast.name.name, param_ast.name.span)
