"""The compiler driver: CLI argument handling and the top-level compile pipeline."""

import argparse
from collections.abc import Iterable
import os
import pathlib
import sys

from lark import Lark, UnexpectedCharacters, UnexpectedToken


from l0.codegen import Compiler
from l0.l0errors import (
    ERROR,
    UserError,
    TextErrorRenderer,
    UnexpectedCharacterError,
    UnexpectedTokenError,
    error_level,
    errors,
    register_error,
)
from l0 import ir_module
from l0 import l0ast as ast
from l0.src import SrcFile, SrcSpan

GRAMMAR_PATH = os.path.join(os.path.dirname(__file__), "l0.lark")


def build_parser(start_rule: str) -> Lark:
    """Build a Lark LALR parser for the l0 grammar.

    :param start_rule: The grammar rule to use as the parse entry point.
    :return: A configured, ready-to-use parser.
    """
    return Lark.open(
        GRAMMAR_PATH,
        start=start_rule,
        strict=True,
        propagate_positions=True,
        parser="lalr",
    )


def _describe_expected_tokens(parser: Lark, names: Iterable[str]) -> list[str]:
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


def compile_to_ir(file: SrcFile) -> ir_module.Mod:
    """Parse and lower a source file into an IR module.

    :param file: The source file to compile.
    :return: The resulting IR module.
    :raises UnexpectedCharacterError: If a character in ``file`` can't
        start any valid token.
    :raises UnexpectedTokenError: If a token in ``file`` doesn't fit the
        grammar at that point (including the input ending too soon).
    """
    parser = build_parser("mod")
    try:
        tree = parser.parse(file.src)
    except UnexpectedCharacters as err:
        span = SrcSpan.single_char(file, err.pos_in_stream, err.line, err.column)
        raise UnexpectedCharacterError(err.char, span) from err
    except UnexpectedToken as err:
        span = SrcSpan(file, err.token)
        found = "end of input" if err.token.type == "$END" else f'token "{err.token}"'
        expected = _describe_expected_tokens(parser, err.accepts or err.expected)
        raise UnexpectedTokenError(found, span, expected) from err
    mod_ast = ast.Mod(file, tree)
    return ir_module.Mod(file.path.stem, mod_ast)


def compile(file: SrcFile) -> str:
    """Compile a source file all the way down to textual LLVM IR.

    :param file: The source file to compile.
    :return: The generated LLVM IR, as text.
    """
    mod = compile_to_ir(file)
    compiler = Compiler(mod)
    compiler.compile()
    return str(compiler.ll_mod) + "\n"


def parse_args() -> argparse.Namespace:
    """Parse the ``l0c`` command-line arguments.

    Defaults the output path (``-o``) to the input filename with its
    suffix replaced by ``.ll``, unless the input filename already ends in
    ``.ll``, in which case ``-o`` must be given explicitly.

    :return: The parsed arguments, with ``filename`` and ``o`` populated.
    """
    parser = argparse.ArgumentParser(prog="l0c", description="l0 compiler")
    parser.add_argument("filename", help="source file", type=pathlib.Path)
    parser.add_argument("-o", help="output file", metavar="FILENAME", type=pathlib.Path)
    args = parser.parse_args()
    if args.o is None:
        if args.filename.suffix != ".ll":
            args.o = str(args.filename.with_suffix(".ll"))
        else:
            print(
                "-o option must be given if source file name ends in '.ll'",
                file=sys.stderr,
            )
            sys.exit(2)

    return args


def run() -> None:
    """Run the ``l0c`` CLI: parse arguments, compile, and report or write output.

    On a user-facing compile error, the error is registered and rendered
    to stderr instead of raising; the process then exits with the
    resulting error level (see :mod:`l0.l0errors`), writing the compiled
    output to the ``-o`` path only if no error occurred.
    """
    args = parse_args()
    file = SrcFile(args.filename)

    try:
        output = compile(file)
    except UserError as err:
        register_error(err)

    if errors():
        renderer = TextErrorRenderer()
        renderer.display_errors(errors())

    if error_level() < ERROR:
        with open(args.o, "w", encoding="utf-8") as f:
            f.write(output)

    sys.exit(error_level())
