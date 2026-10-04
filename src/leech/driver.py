# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The compiler driver: CLI argument handling and the top-level compile pipeline."""

import argparse
import importlib.metadata
import os
import pathlib
import sys
from collections.abc import Mapping, Sequence
from typing import Optional

import llvmlite
from llvmlite import binding as llb

from leech import (
    codegen,
    errors,
    ir_loader,
    ir_module,
    ll_emit,
    opt_util,
    parse,
    reserved,
    src,
    target,
)

IMPORT_PATH_ENV_VAR = "LEECH_PATH"
"""Environment variable listing extra import directories, separated by ``os.pathsep``."""


def resolve_import_paths(
    cli_paths: Sequence[pathlib.Path], environ: Mapping[str, str] = os.environ
) -> tuple[pathlib.Path, ...]:
    """Return the directories to search for imports.

    Command-line paths come before ``LEECH_PATH`` entries; empty ``LEECH_PATH`` entries are
    ignored. Relative paths are resolved against the working directory. An entry that isn't a
    directory is dropped, and a warning is registered once per distinct resolved path.
    """
    env_paths = [
        pathlib.Path(entry)
        for entry in environ.get(IMPORT_PATH_ENV_VAR, "").split(os.pathsep)
        if entry
    ]
    candidates = [(path.resolve(), "the command line") for path in cli_paths]
    candidates += [(path.resolve(), IMPORT_PATH_ENV_VAR) for path in env_paths]
    roots: list[pathlib.Path] = []
    missing: set[pathlib.Path] = set()
    for path, origin in candidates:
        if path.is_dir():
            roots.append(path)
        elif path not in missing:
            missing.add(path)
            errors.register_error(errors.ImportPathNotFoundWarning(path, origin))
    return tuple(roots)


def compile_to_ir(
    file: src.SrcFile,
    qualified_name: Optional[str] = None,
    entry: bool = False,
    search_roots: Sequence[pathlib.Path] = (),
) -> ir_module.Mod:
    """Parse and lower a source file and its imports into IR.

    ``qualified_name`` defaults to the file's stem and qualifies its items' symbols. With
    ``entry``, the module's ``main`` function becomes the program entry point. Imports that
    match neither the importing file's directory nor the bundled library are looked up in
    ``search_roots``, in order.
    """
    qualified_name = opt_util.opt_or_default(qualified_name, file.path.stem)
    loader = ir_loader.ModLoader(search_roots)
    mod = loader.load(file.path, qualified_name)
    loader.check_declarations()
    if entry:
        mod.designate_entry()
    return mod


def compile_to_llvm_ir(
    file: src.SrcFile,
    qualified_name: Optional[str] = None,
    entry: bool = False,
    search_roots: Sequence[pathlib.Path] = (),
) -> str:
    """Compile a source file and its imports to textual LLVM IR."""
    mod = compile_to_ir(file, qualified_name, entry, search_roots)
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
        help="qualified name used for emitted symbols (defaults to the source file stem)",
        metavar="NAME",
        type=_parse_module_name,
    )
    parser.add_argument(
        "--entry",
        action="store_true",
        help="make this module's main function the program entry point",
    )
    parser.add_argument(
        "-I",
        "--import-path",
        action="append",
        default=[],
        help=(
            "directory to search for imported modules, after the importing file's directory "
            f"and the bundled library (repeatable; also read from {IMPORT_PATH_ENV_VAR})"
        ),
        metavar="DIR",
        type=pathlib.Path,
        dest="import_paths",
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
    file = src.SrcFile(args.filename)

    roots = resolve_import_paths(args.import_paths)

    output = b""
    try:
        llvm_ir = compile_to_llvm_ir(file, args.module_name, args.entry, roots)
        output = ll_emit.emit_from_ir(llvm_ir, args.emit, args.opt_level)
    except errors.UserError as err:
        errors.register_error(err)

    if errors.all_errors():
        renderer = errors.TextErrorRenderer()
        renderer.display_errors(errors.all_errors())

    if errors.error_level() < errors.ERROR:
        pathlib.Path(args.o).write_bytes(output)

    sys.exit(errors.error_level())
