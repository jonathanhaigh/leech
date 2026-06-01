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
from l0 import ir, l0ast as ast
from l0 import naming

GRAMMAR_PATH = os.path.join(os.path.dirname(__file__), "l0.lark")

var_name = naming.VarNamer()


def build_parser(start_rule: str):
    return Lark.open(
        GRAMMAR_PATH,
        start=start_rule,
        strict=True,
        propagate_positions=True,
        parser="lalr",
    )


def compile(src: str):
    parser = build_parser("mod")
    tree = parser.parse(src)
    mod_ast = ast.Mod(tree)
    mod = ir.Mod(mod_ast)
    compiler = Compiler(mod)
    compiler.compile()
    return str(compiler.ll_mod) + "\n"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="l0c", description="l0 compiler")
    parser.add_argument("filename", help="source file")
    parser.add_argument("-o", help="output file", metavar="FILENAME")
    args = parser.parse_args()
    if args.o is None:
        if args.filename == "-":
            args.o = "-"
        else:
            in_path = pathlib.Path(args.filename)
            if in_path.suffix != ".ll":
                args.o = str(in_path.with_suffix(".ll"))
            else:
                print("-o option must be given if source file name ends in '.ll'", file=sys.stderr)
                sys.exit(2)

    return args


def run(src: str):
    args = parse_args()
    if args.filename == "-":
        src = sys.stdin.read()
    else:
        with open(args.filename) as f:
            src = f.read()

    try:
        output = compile(src)
    except UserError as err:
        register_error(err)

    if errors():
        renderer = TextErrorRenderer(src)
        renderer.display_errors(errors())

    if error_level() < ERROR:
        if args.o == "-":
            sys.stdout.write(output)
        else:
            with open(args.o, "w") as f:
                f.write(output)

    sys.exit(error_level())
