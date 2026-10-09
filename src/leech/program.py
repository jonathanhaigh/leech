# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Checking Leech programs and generating their code."""

import dataclasses
import pathlib
from typing import Final, NoReturn

from llvmlite import binding as llb

from leech import codegen, compilation, diag, errors, ir_module, ll_emit, mono
from leech import session as session_mod


class Program:
    """A root module and every module it imports, not yet checked.

    The root module is named after its file stem, so its package is its own directory. With
    ``entry``, its ``main`` becomes the program entry point. ``import std::...`` resolves in
    the bundled library, and any other import in the root's package.
    """

    root: Final[pathlib.Path]
    entry: Final[bool]

    def __init__(self, root: pathlib.Path, *, entry: bool) -> None:
        self.root = root
        self.entry = entry

    def check(self, session: session_mod.Session) -> CheckedProgram:
        """Load, check and discover the whole program in one compilation.

        Every diagnostic is reported to the session. Checking continues past an error in one
        declaration to find errors in others. If there are any errors,
        ``diag.CompilationError`` is raised with every diagnostic in source order.
        """
        ctx = compilation.Ctx(session)
        try:
            root = ctx.loader.load_root(self.root, self.root.stem)
        except errors.UserError as err:
            ctx.diags.error(err)
            _fail(ctx.diags)
        except diag.ReportedError:
            _fail(ctx.diags)
        ctx.loader.check_declarations()
        if self.entry:
            with ctx.recovering():
                root.designate_entry()
        instances = mono.discover(ctx)
        if ctx.diags.has_errors:
            _fail(ctx.diags)
        return CheckedProgram(ctx, root, instances)


def _fail(diags: diag.Diags) -> NoReturn:
    raise diag.CompilationError(diags.sorted())


@dataclasses.dataclass(frozen=True)
class CheckedProgram:
    """A program with no errors, so generating its code finds none."""

    ctx: compilation.Ctx
    root: ir_module.Mod
    instances: mono.MonoResult

    def llvm_ir(self) -> str:
        """Generate the program as one module of textual LLVM IR.

        A user error found while generating IR is a compiler bug, raised as
        ``diag.InternalError``.
        """
        diags = self.ctx.diags
        assert not diags.has_errors, "IR is only generated for a program without errors"
        try:
            compiler = codegen.Compiler(self.root, self.instances)
            compiler.compile()
        except errors.UserError as err:
            raise _user_error_while_generating_ir(err) from err
        except diag.ReportedError as err:
            raise _user_error_while_generating_ir(err.reported.diag) from err
        reported = diags.any_error()
        if reported is not None:
            raise _user_error_while_generating_ir(reported.diag)
        return compiler.llvm_ir()

    def llvm_module(self) -> llb.ModuleRef:
        """Generate the program, parsed and optimized at the session's level.

        The module is named after the root module. Generated IR that fails to parse or verify
        is a compiler bug, raised as ``diag.InternalError``.
        """
        try:
            module = ll_emit.parse(self.llvm_ir())
        except RuntimeError as err:
            raise diag.InternalError(
                f"the generated LLVM IR failed to parse or verify: {str(err).strip()}"
            ) from err
        module.name = self.root.name
        ll_emit.optimize(module, self.ctx.session.opt_level)
        return module


def _user_error_while_generating_ir(err: errors.UserError) -> diag.InternalError:
    return diag.InternalError(f"a user error was found while generating IR: {err}")
