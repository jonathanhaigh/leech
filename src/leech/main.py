"""The compiler driver: CLI argument handling and the top-level compile pipeline."""

import argparse
import pathlib
import sys

from leech.codegen import Compiler
from leech.leecherrors import (
    ERROR,
    UserError,
    TextErrorRenderer,
    error_level,
    errors,
    register_error,
)
from leech import ir_module
from leech.ir_loader import ModLoader
from leech.src import SrcFile


def compile_to_ir(file: SrcFile) -> ir_module.Mod:
    """Parse and lower a source file, and everything it imports, into IR.

    :param file: The source file to compile.
    :return: The resulting IR module, whose
        :attr:`~leech.ir_module.Mod.loader` holds every module reached from
        it.
    :raises UnexpectedCharacterError: If a character in ``file`` can't
        start any valid token.
    :raises UnexpectedTokenError: If a token in ``file`` doesn't fit the
        grammar at that point (including the input ending too soon).
    """
    return ModLoader().load(file.path)


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
    """Parse the ``leechc`` command-line arguments.

    Defaults the output path (``-o``) to the input filename with its
    suffix replaced by ``.ll``, unless the input filename already ends in
    ``.ll``, in which case ``-o`` must be given explicitly.

    :return: The parsed arguments, with ``filename`` and ``o`` populated.
    """
    parser = argparse.ArgumentParser(prog="leechc", description="Leech compiler")
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
    """Run the ``leechc`` CLI: parse arguments, compile, and report or write output.

    On a user-facing compile error, the error is registered and rendered
    to stderr instead of raising; the process then exits with the
    resulting error level (see :mod:`leech.leecherrors`), writing the compiled
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
