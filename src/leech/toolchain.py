# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Turning LLVM IR into files: object emission, the build output directory and the linker."""

import os
import pathlib
import shlex
import shutil
import subprocess
from collections.abc import Mapping
from typing import Final

from leech import diag, errors, ll_emit

OUT_DIR_NAME: Final[str] = "leech-out"
"""The build output directory, created beside the root module."""

_CACHEDIR_TAG: Final[str] = (
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This file is a cache directory tag created by leech.\n"
    "# For information about cache directory tags see https://bford.info/cachedir/\n"
)


class Linker:
    """The command that links object files into executables."""

    command: Final[tuple[str, ...]]

    def __init__(self, command: tuple[str, ...]) -> None:
        self.command = command

    @classmethod
    def from_env(cls) -> Linker:
        """Return the linker named by ``$CC``, split like a shell, or ``cc``.

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
        return cls(tuple(command))

    def link(self, obj: pathlib.Path, exe: pathlib.Path) -> None:
        """Link ``exe``, replacing any old file only once the linker has produced a new one."""
        tmp_exe = exe.with_name(f".{exe.name}.leech-tmp")
        command = [*self.command, str(obj), "-o", str(tmp_exe)]
        try:
            try:
                proc = subprocess.run(command, capture_output=True, text=True, check=False)
            except OSError as err:
                raise errors.LinkFailedError(
                    shlex.join(command), "could not run", str(err)
                ) from err
            if proc.returncode != 0:
                raise errors.LinkFailedError(
                    shlex.join(command),
                    f"exited with status {proc.returncode}",
                    proc.stdout + proc.stderr,
                )
            if tmp_exe.is_symlink() or not tmp_exe.is_file():
                raise errors.LinkFailedError(
                    shlex.join(command),
                    "succeeded but wrote no executable",
                    proc.stdout + proc.stderr,
                )
            tmp_exe.replace(exe)
        finally:
            tmp_exe.unlink(missing_ok=True)


class OutputDir:
    """The build output directory beside a root module, ``leech-out/``.

    It holds the default executable, ``<stem>``, and the intermediate files of building it,
    in ``<stem>.obj/``.
    """

    path: Final[pathlib.Path]
    _stem: Final[str]
    _obj_dir: Final[pathlib.Path]

    def __init__(self, root: pathlib.Path) -> None:
        self.path = root.parent / OUT_DIR_NAME
        self._stem = root.stem
        self._obj_dir = self.path / f"{root.stem}.obj"

    @property
    def default_exe(self) -> pathlib.Path:
        return self.path / self._stem

    def prepare(self) -> None:
        """Create the directory, marked as ignorable, with an empty intermediates directory.

        The output directory must be a real directory, so that clearing old intermediates can
        only ever delete files inside it.
        """
        if self.path.is_symlink() or (self.path.exists() and not self.path.is_dir()):
            raise errors.BuildOutputError(f"{self.path} exists but is not a directory")
        self.path.mkdir(exist_ok=True)
        gitignore = self.path / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n")
        cachedir_tag = self.path / "CACHEDIR.TAG"
        if not cachedir_tag.exists():
            cachedir_tag.write_text(_CACHEDIR_TAG)
        if self._obj_dir.is_symlink() or self._obj_dir.is_file():
            self._obj_dir.unlink()
        elif self._obj_dir.exists():
            shutil.rmtree(self._obj_dir)
        self._obj_dir.mkdir()

    def emit_object(self, llvm_irs: Mapping[str, str], opt_level: int) -> pathlib.Path:
        """Save each module's IR, by qualified module name, then link, optimize and emit them
        as one object file.

        IR that fails to link or verify is a compiler bug, raised as ``diag.InternalError``.
        """
        for name, llvm_ir in llvm_irs.items():
            ir_path = self._obj_dir.joinpath(*name.split("::")).with_suffix(".ll")
            ir_path.parent.mkdir(parents=True, exist_ok=True)
            ir_path.write_text(llvm_ir)
        try:
            linked = ll_emit.link([ll_emit.parse(llvm_ir) for llvm_ir in llvm_irs.values()])
        except RuntimeError as err:
            raise diag.InternalError(
                f"the generated LLVM IR in {self._obj_dir} failed to link or verify: "
                f"{str(err).strip()}"
            ) from err
        ll_emit.optimize(linked, opt_level)
        obj_path = self._obj_dir / f"{self._stem}.o"
        obj_path.write_bytes(ll_emit.emit(linked, ll_emit.EmitKind.OBJ, opt_level))
        return obj_path
