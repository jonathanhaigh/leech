# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Turning an LLVM module into files: output kinds, the linker, and writing outputs."""

import enum
import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from typing import Final, Optional

from llvmlite import binding as llb

from leech import errors, ll_emit


class OutputKind(enum.Enum):
    """An output a build can write, named as on the command line.

    Outputs are produced in the order the kinds are defined.
    """

    LLVM_IR = "llvm-ir"
    LLVM_BC = "llvm-bc"
    ASM = "asm"
    OBJ = "obj"
    EXE = "exe"

    @property
    def suffix(self) -> str:
        """The file suffix of this kind's default path."""
        if self is OutputKind.EXE:
            return ""
        return ll_emit.EmitKind(self.value).suffix


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
        """Link ``obj`` into the executable ``exe``, which must not exist yet."""
        command = [*self.command, str(obj), "-o", str(exe)]
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
        if exe.is_symlink() or not exe.is_file():
            raise errors.LinkFailedError(
                shlex.join(command), "succeeded but wrote no executable", proc.stdout + proc.stderr
            )


def write_outputs(
    module: llb.ModuleRef,
    outputs: Mapping[OutputKind, pathlib.Path],
    opt_level: int,
    linker: Optional[Linker],
) -> None:
    """Write each requested kind of ``module``'s output to its path, generating machine code at
    ``opt_level`` and linking an executable with ``linker``.

    Every output is produced in a temporary directory before any path is replaced, so a failed
    link changes nothing. Each path is then replaced atomically, in the order of the kinds, so
    it holds either its old content or its complete new content; if replacing one fails, the
    ones already replaced keep their new content. ``module`` must not be used afterwards.
    """
    assert outputs, "no outputs requested"
    assert (linker is not None) == (OutputKind.EXE in outputs), "a linker is needed for exe only"
    try:
        for path in outputs.values():
            if path.is_dir():
                raise errors.BuildOutputError(f"{path} is a directory")
        with tempfile.TemporaryDirectory(prefix="leech-") as tmp_dir:
            staged = _stage(module, outputs.keys(), opt_level, linker, pathlib.Path(tmp_dir))
            for kind in OutputKind:
                if kind in outputs:
                    _commit(staged[kind], outputs[kind])
    except OSError as err:
        raise errors.BuildOutputError(str(err)) from err


def _stage(
    module: llb.ModuleRef,
    kinds: Iterable[OutputKind],
    opt_level: int,
    linker: Optional[Linker],
    directory: pathlib.Path,
) -> dict[OutputKind, pathlib.Path]:
    """Write each kind of output into ``directory``, with an object for the executable."""
    to_stage = set(kinds)
    if OutputKind.EXE in to_stage:
        to_stage.add(OutputKind.OBJ)
    staged = dict[OutputKind, pathlib.Path]()
    for kind in OutputKind:
        if kind not in to_stage:
            continue
        path = directory / f"program{kind.suffix}"
        match kind:
            case OutputKind.EXE:
                assert linker is not None
                linker.link(staged[OutputKind.OBJ], path)
            case OutputKind.ASM:
                # Generating machine code changes the module, so the object, generated
                # next, must not see what generating assembly did.
                emitted = ll_emit.emit(module.clone(), ll_emit.EmitKind.ASM, opt_level)
                path.write_bytes(emitted)
            case _:
                path.write_bytes(ll_emit.emit(module, ll_emit.EmitKind(kind.value), opt_level))
        staged[kind] = path
    return staged


def _commit(staged: pathlib.Path, path: pathlib.Path) -> None:
    """Replace ``path`` with a copy of ``staged``, keeping ``staged``'s mode bits.

    The copy is made beside ``path`` first, because ``staged`` may be on another file system,
    from which it can't be renamed into place.
    """
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".leech-tmp", dir=path.parent)
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as tmp_file, staged.open("rb") as staged_file:
            shutil.copyfileobj(staged_file, tmp_file)
        shutil.copymode(staged, tmp)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
