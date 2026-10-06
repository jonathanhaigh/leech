# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The compiler driver: CLI argument handling and the top-level compile pipeline."""

import argparse
import dataclasses
import importlib.metadata
import pathlib
import sys
from typing import Optional

import llvmlite
from llvmlite import binding as llb

from leech import (
    codegen,
    compilation,
    diag,
    errors,
    ir_module,
    ll_emit,
    opt_util,
    parse,
    reserved,
    src,
    target,
)


def compile_to_ir(
    file: src.SrcFile,
    qualified_name: Optional[str] = None,
    entry: bool = False,
    diags: Optional[diag.Diags] = None,
) -> ir_module.Mod:
    """Parse and lower a source file and its imports into IR.

    ``qualified_name`` defaults to the file's stem and must match the file's location in its
    package, where it qualifies the module's symbols. With ``entry``, the module's ``main``
    function becomes the program entry point. ``import std::...`` resolves in the bundled
    library, and any other import in the file's package.

    Diagnostics that don't stop compilation are emitted to ``diags``, which defaults to a
    new collection, reachable through the returned module's ``ctx.diags``. The first error
    is raised.
    """
    qualified_name = opt_util.opt_or_default(qualified_name, file.path.stem)
    ctx = compilation.Ctx(diags)
    mod = ctx.loader.load_root(file.path, qualified_name)
    ctx.loader.check_declarations()
    if entry:
        mod.designate_entry()
    return mod


@dataclasses.dataclass(frozen=True)
class Compilation:
    """The outcome of compiling one module, and every diagnostic it produced."""

    #: The compiled module, unless an error stopped compilation before it was loaded.
    mod: Optional[ir_module.Mod]
    #: The module's textual LLVM IR, or ``None`` if any diagnostic is an error.
    llvm_ir: Optional[str]
    diags: diag.Diags


def compile_module(
    file: src.SrcFile, qualified_name: Optional[str] = None, entry: bool = False
) -> Compilation:
    """Compile a module to LLVM IR as ``compile_to_llvm_ir`` does, returning its diagnostics.

    Diagnostics are returned rather than raised, whether compilation raised or emitted them,
    so the module fails if any of them is an error.
    """
    diags = diag.Diags()
    mod = None
    llvm_ir = None
    try:
        mod = compile_to_ir(file, qualified_name, entry, diags)
        llvm_ir = lower_to_llvm_ir(mod)
    except errors.UserError as err:
        diags.error(err)
    if diags.has_errors:
        llvm_ir = None
    return Compilation(mod, llvm_ir, diags)


def compile_to_llvm_ir(
    file: src.SrcFile,
    qualified_name: Optional[str] = None,
    entry: bool = False,
    diags: Optional[diag.Diags] = None,
) -> str:
    """Compile a source file and its imports to textual LLVM IR.

    Arguments, diagnostics and errors are as for ``compile_to_ir``.
    """
    return lower_to_llvm_ir(compile_to_ir(file, qualified_name, entry, diags))


def lower_to_llvm_ir(mod: ir_module.Mod) -> str:
    """Generate textual LLVM IR for a module built by ``compile_to_ir``."""
    compiler = codegen.Compiler(mod)
    compiler.compile()
    return compiler.llvm_ir()


def _parse_module_name(value: str) -> str:
    segments = parse.parse_qualified_name(value)
    if segments is None or reserved.is_reserved(segments[-1]):
        raise argparse.ArgumentTypeError(f"invalid qualified module name: {value!r}")
    return value


def version_text(prog: str) -> str:
    """Describe the installed compiler version and its LLVM backend, prefixed by ``prog``."""
    llvm_version = ".".join(str(part) for part in llb.llvm_version_info)
    return (
        f"{prog} {importlib.metadata.version('leech')} "
        f"(LLVM {llvm_version} via llvmlite {llvmlite.__version__}; target {target.TRIPLE})"
    )


def _parse_args() -> argparse.Namespace:
    """Parse arguments, defaulting the output to the input path with the emitted format's
    suffix in place of ``.leech``."""
    parser = argparse.ArgumentParser(
        prog="leechc", description="Compile one Leech module.", allow_abbrev=False
    )
    parser.add_argument("--version", action="version", version=version_text("leechc"))
    parser.add_argument("filename", help="source file", type=pathlib.Path)
    parser.add_argument("-o", help="output file", metavar="FILENAME", type=pathlib.Path)
    parser.add_argument(
        "--module-name",
        help=(
            "the module's qualified name, matching its location: x::a for x/a.leech in the "
            "package directory (defaults to the source file stem)"
        ),
        metavar="NAME",
        type=_parse_module_name,
    )
    parser.add_argument(
        "--entry",
        action="store_true",
        help="make this module's main function the program entry point",
    )
    parser.add_argument(
        "--emit",
        choices=[kind.value for kind in ll_emit.EmitKind],
        default=ll_emit.EmitKind.LLVM_IR.value,
        help="output format (default: %(default)s)",
    )
    parser.add_argument(
        "-O",
        choices=ll_emit.OPT_LEVELS,
        default=0,
        type=int,
        help="optimization level (default: %(default)s)",
        dest="opt_level",
    )
    args = parser.parse_args()
    args.emit = ll_emit.EmitKind(args.emit)
    if args.filename.suffix != ".leech":
        parser.error(f"source file name must end in '.leech': {str(args.filename)!r}")
    if args.o is None:
        args.o = args.filename.with_suffix(args.emit.suffix)

    return args


def main() -> None:
    """Compile CLI input, render diagnostics, and exit with their severity."""
    args = _parse_args()
    compilation = compile_module(src.SrcFile(args.filename), args.module_name, args.entry)
    errors.TextErrorRenderer().display_errors(list(compilation.diags.all()))
    if compilation.llvm_ir is not None:
        output = ll_emit.emit_from_ir(compilation.llvm_ir, args.emit, args.opt_level)
        pathlib.Path(args.o).write_bytes(output)
    sys.exit(compilation.diags.level)
