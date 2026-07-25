import argparse
import os
import pathlib
import sys

from lark import Lark


from l0.codegen import Compiler
from l0.l0errors import (
    ERROR,
    UserError,
    TextErrorRenderer,
    error_level,
    errors,
    register_error,
)
from l0 import ir_module
from l0 import l0ast as ast
from l0.src import SrcFile

GRAMMAR_PATH = os.path.join(os.path.dirname(__file__), "l0.lark")


def build_parser(start_rule: str) -> Lark:
    return Lark.open(
        GRAMMAR_PATH,
        start=start_rule,
        strict=True,
        propagate_positions=True,
        parser="lalr",
    )


def compile_to_ir(file: SrcFile) -> ir_module.Mod:
    parser = build_parser("mod")
    tree = parser.parse(file.src)
    mod_ast = ast.Mod(file, tree)
    return ir_module.Mod(file.path.stem, mod_ast)


def compile(file: SrcFile) -> str:
    mod = compile_to_ir(file)
    compiler = Compiler(mod)
    compiler.compile()
    return str(compiler.ll_mod) + "\n"


def parse_args() -> argparse.Namespace:
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
