# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Parsing, optimizing and emitting generated LLVM IR in-process."""

import enum
import functools

from llvmlite import binding as llb

from leech import asserts, target

OPT_LEVELS = (0, 1, 2, 3)
"""Supported optimization levels, as for ``clang -O<n>``."""


class EmitKind(enum.Enum):
    """An output format, named as on the command line."""

    LLVM_IR = "llvm-ir"
    LLVM_BC = "llvm-bc"
    ASM = "asm"
    OBJ = "obj"

    @property
    def suffix(self) -> str:
        """The conventional file suffix for this format."""
        match self:
            case EmitKind.LLVM_IR:
                return ".ll"
            case EmitKind.LLVM_BC:
                return ".bc"
            case EmitKind.ASM:
                return ".s"
            case EmitKind.OBJ:
                return ".o"


@functools.cache
def _initialize_llvm() -> None:
    llb.initialize_all_targets()
    llb.initialize_all_asmprinters()


@functools.cache
def _target_machine(opt_level: int) -> llb.TargetMachine:
    """Return a machine emitting position-independent code, so objects link into PIEs."""
    _initialize_llvm()
    return llb.Target.from_triple(target.TRIPLE).create_target_machine(
        opt=opt_level, reloc="pic", codemodel="default"
    )


def parse(llvm_ir: str) -> llb.ModuleRef:
    """Parse and verify textual LLVM IR."""
    _initialize_llvm()
    mod = llb.parse_assembly(llvm_ir)
    mod.verify()
    return mod


def optimize(mod: llb.ModuleRef, opt_level: int) -> None:
    """Run LLVM's default module pipeline for ``opt_level`` in place; level 0 does nothing."""
    asserts.assert_in(opt_level, OPT_LEVELS)
    if opt_level == 0:
        return
    tuning = llb.create_pipeline_tuning_options(speed_level=opt_level, size_level=0)
    pass_builder = llb.create_pass_builder(_target_machine(opt_level), tuning)
    pass_builder.getModulePassManager().run(mod, pass_builder)


def emit(mod: llb.ModuleRef, kind: EmitKind, opt_level: int) -> bytes:
    """Serialize ``mod`` as ``kind``, generating machine code at ``opt_level``."""
    match kind:
        case EmitKind.LLVM_IR:
            return str(mod).encode()
        case EmitKind.LLVM_BC:
            return mod.as_bitcode()
        case EmitKind.ASM:
            return _target_machine(opt_level).emit_assembly(mod).encode()
        case EmitKind.OBJ:
            return _target_machine(opt_level).emit_object(mod)
