# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Building a program and the modules it imports into a native executable."""

import dataclasses
import os
import pathlib
import shlex
import shutil
import subprocess
from typing import Final, Optional

from leech import diag, driver, errors, ll_emit, opt_util, src

OUT_DIR_NAME: Final[str] = "leech-out"
"""The build output directory, created beside the root module."""

_CACHEDIR_TAG: Final[str] = (
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This file is a cache directory tag created by leech.\n"
    "# For information about cache directory tags see https://bford.info/cachedir/\n"
)


@dataclasses.dataclass(frozen=True)
class BuildResult:
    """The outcome of a build, and every distinct diagnostic it produced in source order."""

    #: The absolute path of the built executable, or ``None`` if the build failed.
    exe: Optional[pathlib.Path]
    diags: tuple[errors.UserError, ...]


def resolve_cc() -> list[str]:
    """Return the command that links executables: ``$CC`` split like a shell, or ``cc``.

    An unset, empty or blank ``CC`` means ``cc``.
    """
    value = os.environ.get("CC", "")
    command = ["cc"]
    if value.strip():
        try:
            command = shlex.split(value)
        except ValueError as err:
            raise errors.CcInvalidError(value, str(err)) from err
    if shutil.which(command[0]) is None:
        raise errors.CcNotFoundError(command[0])
    return command


def build(
    root: pathlib.Path,
    *,
    output: Optional[pathlib.Path] = None,
    opt_level: int = 0,
    diags: Optional[diag.Diags] = None,
) -> BuildResult:
    """Build the program whose root module is ``root`` into an executable.

    The root module is named after its file stem, so its package is its own directory.
    Intermediate files go to ``leech-out/<stem>.obj/`` beside the root, and the executable
    to ``output`` or ``leech-out/<stem>``, replaced only once linking succeeds. Compilation
    stops at the first module with an error, without writing an executable.

    Diagnostics are also reported to ``diags``, if given, so that a caller can still render
    them if the build crashes.
    """
    diags = opt_util.opt_or_default(diags, diag.Diags())
    try:
        cc = resolve_cc()
        llvm_irs = _compile_program(root, diags)
        if llvm_irs is None:
            return BuildResult(None, diags.sorted())
        out_dir = root.parent / OUT_DIR_NAME
        obj_dir = out_dir / f"{root.stem}.obj"
        if output is None:
            output = out_dir / root.stem
        exe = output.absolute()
        try:
            _prepare_out_dir(out_dir, obj_dir)
            obj_path = _emit_object(llvm_irs, obj_dir, root.stem, opt_level)
            _link(cc, obj_path, exe)
        except OSError as err:
            raise errors.BuildOutputError(str(err)) from err
    except errors.UserError as err:
        diags.error(err)
        return BuildResult(None, diags.sorted())
    return BuildResult(exe, diags.sorted())


def check(root: pathlib.Path, diags: Optional[diag.Diags] = None) -> tuple[errors.UserError, ...]:
    """Check the program whose root module is ``root`` as ``build`` does, writing nothing.

    Modules are checked as ``build`` compiles them, until one has an error, which finds
    every diagnostic ``build`` would, but no IR is generated. Returns every distinct
    diagnostic in source order; the program is valid unless one of them is an error.
    Diagnostics are also reported to ``diags``, if given, as for ``build``.
    """
    diags = opt_util.opt_or_default(diags, diag.Diags())
    _compile_program(root, diags, generate_ir=False)
    return diags.sorted()


def _compile_program(
    root: pathlib.Path, diags: diag.Diags, *, generate_ir: bool = True
) -> Optional[dict[str, str]]:
    """Compile every module of the program separately, returning LLVM IR by module name.

    Each compilation reloads the whole program, so one problem is often reported by several
    compilations; ``diags`` keeps one of each. Returns ``None`` once a module fails, without
    compiling the rest. Without ``generate_ir``, every module is only checked, and the
    result maps no module to IR.
    """
    root_compilation = _compile_module(src.SrcFile(root), root.stem, True, diags, generate_ir)
    root_mod = root_compilation.mod
    if root_mod is None or root_compilation.diags.has_errors:
        return None
    compilations = [root_compilation]
    for mod in root_mod.ctx.loader.mods:
        if mod is not root_mod:
            compilation = _compile_module(mod.ast.span.file, mod.name, False, diags, generate_ir)
            if compilation.diags.has_errors:
                return None
            compilations.append(compilation)
    return {
        opt_util.opt_unwrap(compilation.mod).name: compilation.llvm_ir
        for compilation in compilations
        if compilation.llvm_ir is not None
    }


def _compile_module(
    file: src.SrcFile, qualified_name: str, entry: bool, diags: diag.Diags, generate_ir: bool
) -> driver.Compilation:
    """Compile one module, merging its diagnostics into ``diags`` even if it crashes."""
    module_diags = diag.Diags()
    try:
        return driver.compile_module(
            file, qualified_name, entry, diags=module_diags, generate_ir=generate_ir
        )
    finally:
        diags.merge(module_diags)


def _prepare_out_dir(out_dir: pathlib.Path, obj_dir: pathlib.Path) -> None:
    """Create the output directory, marked as ignorable, with an empty intermediates dir.

    The output directory must be a real directory, so that clearing old intermediates can
    only ever delete files inside it.
    """
    if out_dir.is_symlink() or (out_dir.exists() and not out_dir.is_dir()):
        raise errors.BuildOutputError(f"{out_dir} exists but is not a directory")
    out_dir.mkdir(exist_ok=True)
    gitignore = out_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n")
    cachedir_tag = out_dir / "CACHEDIR.TAG"
    if not cachedir_tag.exists():
        cachedir_tag.write_text(_CACHEDIR_TAG)
    if obj_dir.is_symlink() or obj_dir.is_file():
        obj_dir.unlink()
    elif obj_dir.exists():
        shutil.rmtree(obj_dir)
    obj_dir.mkdir()


def _emit_object(
    llvm_irs: dict[str, str], obj_dir: pathlib.Path, stem: str, opt_level: int
) -> pathlib.Path:
    """Save each module's IR, then link, optimize and emit them as one object file."""
    for name, llvm_ir in llvm_irs.items():
        ir_path = obj_dir.joinpath(*name.split("::")).with_suffix(".ll")
        ir_path.parent.mkdir(parents=True, exist_ok=True)
        ir_path.write_text(llvm_ir)
    try:
        linked = ll_emit.link([ll_emit.parse(llvm_ir) for llvm_ir in llvm_irs.values()])
    except RuntimeError as err:
        raise diag.InternalError(
            f"the generated LLVM IR in {obj_dir} failed to link or verify: {str(err).strip()}"
        ) from err
    ll_emit.optimize(linked, opt_level)
    obj_path = obj_dir / f"{stem}.o"
    obj_path.write_bytes(ll_emit.emit(linked, ll_emit.EmitKind.OBJ, opt_level))
    return obj_path


def _link(cc: list[str], obj_path: pathlib.Path, exe: pathlib.Path) -> None:
    """Link ``exe``, replacing any old file only once the linker has produced a new one."""
    tmp_exe = exe.with_name(f".{exe.name}.leech-tmp")
    command = [*cc, str(obj_path), "-o", str(tmp_exe)]
    try:
        try:
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as err:
            raise errors.LinkFailedError(shlex.join(command), "could not run", str(err)) from err
        if proc.returncode != 0:
            raise errors.LinkFailedError(
                shlex.join(command),
                f"exited with status {proc.returncode}",
                proc.stdout + proc.stderr,
            )
        if tmp_exe.is_symlink() or not tmp_exe.is_file():
            raise errors.LinkFailedError(
                shlex.join(command), "succeeded but wrote no executable", proc.stdout + proc.stderr
            )
        tmp_exe.replace(exe)
    finally:
        tmp_exe.unlink(missing_ok=True)
