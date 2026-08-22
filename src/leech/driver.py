# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The compiler driver: CLI argument handling and the top-level compile pipeline."""

import argparse
import pathlib
import sys
from typing import Optional

from leech import codegen, errors, ir_loader, ir_module, opt_util, src


def compile_to_ir(file: src.SrcFile, qualified_name: Optional[str] = None) -> ir_module.Mod:
    """Parse and lower a source file and its imports into IR.

    ``qualified_name`` defaults to the file's stem and qualifies its items' symbols.
    """
    qualified_name = opt_util.opt_or_default(qualified_name, file.path.stem)
    # TODO: thread a --search-path/env-var CLI option through to
    # ModLoader's extra_search_roots once one exists.
    loader = ir_loader.ModLoader()
    mod = loader.load(file.path, qualified_name)
    loader.check_declarations()
    return mod


def compile_to_llvm_ir(file: src.SrcFile, qualified_name: Optional[str] = None) -> str:
    """Compile a source file and its imports to textual LLVM IR."""
    mod = compile_to_ir(file, qualified_name)
    compiler = codegen.Compiler(mod)
    compiler.compile()
    return str(compiler.ll_mod) + "\n"


def _parse_args() -> argparse.Namespace:
    """Parse arguments, defaulting the output to the input path with an ``.ll`` suffix."""
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
    """Compile CLI input, render diagnostics, and exit with their severity."""
    args = _parse_args()
    file = src.SrcFile(args.filename)

    output = ""
    try:
        output = compile_to_llvm_ir(file)
    except errors.UserError as err:
        errors.register_error(err)

    if errors.all_errors():
        renderer = errors.TextErrorRenderer()
        renderer.display_errors(errors.all_errors())

    if errors.error_level() < errors.ERROR:
        with pathlib.Path(args.o).open("w", encoding="utf-8") as f:
            f.write(output)

    sys.exit(errors.error_level())
