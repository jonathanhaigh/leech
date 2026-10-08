# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Checking Leech programs and generating their code."""

import dataclasses
import pathlib
from typing import Final, NoReturn, Optional

from llvmlite import binding as llb

from leech import codegen, compilation, diag, errors, ir_module, ll_emit, opt_util
from leech import session as session_mod


class Program:
    """A root module and every module it imports, not yet checked.

    The root module is named after its file stem, so its package is its own directory. With
    ``entry``, its ``main`` becomes the program entry point.
    """

    root: Final[pathlib.Path]
    entry: Final[bool]

    def __init__(self, root: pathlib.Path, *, entry: bool) -> None:
        self.root = root
        self.entry = entry

    def check(self, session: session_mod.Session) -> CheckedProgram:
        """Check every module of the program in a compilation of its own.

        The root module is compiled first, then every module it loaded, stopping at the first
        module with an error, which is raised as ``check_module`` raises it. Each compilation
        reloads the whole program, so one problem is often found by several compilations;
        the session's diagnostics keep one of each.
        """
        root = _check_in_child_session(self.root, self.root.stem, self.entry, session)
        modules = [root]
        for mod in root.mod.ctx.loader.mods:
            if mod is not root.mod:
                modules.append(
                    _check_in_child_session(mod.ast.span.file.path, mod.name, False, session)
                )
        return CheckedProgram(session, tuple(modules))


@dataclasses.dataclass(frozen=True)
class CheckedProgram:
    """A program with no errors, each of its modules checked in its own compilation."""

    session: session_mod.Session
    #: The root module first, then the others in load order.
    modules: tuple[CheckedModule, ...]

    def llvm_module(self) -> llb.ModuleRef:
        """Generate the program as one LLVM module, optimized at the session's level.

        The module is named after the root module. Generated IR that fails to parse, link or
        verify is a compiler bug, raised as ``diag.InternalError``.
        """
        llvm_irs = [module.llvm_ir() for module in self.modules]
        try:
            linked = ll_emit.link([ll_emit.parse(llvm_ir) for llvm_ir in llvm_irs])
        except RuntimeError as err:
            raise diag.InternalError(
                f"the generated LLVM IR failed to link or verify: {str(err).strip()}"
            ) from err
        linked.name = self.modules[0].mod.name
        ll_emit.optimize(linked, self.session.opt_level)
        return linked


def _check_in_child_session(
    path: pathlib.Path, qualified_name: str, entry: bool, parent: session_mod.Session
) -> CheckedModule:
    """Check a module with its own diagnostics, merged into ``parent``'s even if it crashes."""
    child = dataclasses.replace(parent, diags=diag.Diags())
    try:
        return check_module(path, child, qualified_name=qualified_name, entry=entry)
    finally:
        parent.diags.merge(child.diags)


def check_module(
    path: pathlib.Path,
    session: session_mod.Session,
    *,
    qualified_name: Optional[str] = None,
    entry: bool = False,
) -> CheckedModule:
    """Load and check a module's whole program, and discover the instances the module needs.

    ``qualified_name`` defaults to the file's stem and must match the file's location in its
    package, where it qualifies the module's symbols. With ``entry``, the module's ``main``
    function becomes the program entry point. ``import std::...`` resolves in the bundled
    library, and any other import in the file's package.

    Every diagnostic is reported to the session. Checking continues past an error in one
    declaration to find errors in others. If there are any errors, the first one in source
    order is raised.
    """
    qualified_name = opt_util.opt_or_default(qualified_name, path.stem)
    ctx = compilation.Ctx(session)
    try:
        mod = ctx.loader.load_root(path, qualified_name)
    except errors.UserError as err:
        ctx.diags.error(err)
        _raise_first_error(ctx.diags)
    except diag.ReportedError:
        _raise_first_error(ctx.diags)
    ctx.loader.check_declarations()
    if entry:
        with ctx.recovering():
            mod.designate_entry()
    mod.discover_instances()
    if ctx.diags.has_errors:
        _raise_first_error(ctx.diags)
    return CheckedModule(mod)


def _raise_first_error(diags: diag.Diags) -> NoReturn:
    """Raise the first reported error, in source order."""
    raise next(err for err in diags.sorted() if err.level == errors.ERROR)


@dataclasses.dataclass(frozen=True)
class CheckedModule:
    """A module whose compilation found no errors, so generating its code finds none."""

    mod: ir_module.Mod

    def llvm_ir(self) -> str:
        """Generate the module's textual LLVM IR.

        A user error found while generating IR is a compiler bug, raised as
        ``diag.InternalError``.
        """
        diags = self.mod.ctx.diags
        assert not diags.has_errors, "IR is only generated for a module without errors"
        try:
            compiler = codegen.Compiler(self.mod)
            compiler.compile()
        except errors.UserError as err:
            raise _user_error_while_generating_ir(err) from err
        except diag.ReportedError as err:
            raise _user_error_while_generating_ir(err.reported.diag) from err
        reported = diags.any_error()
        if reported is not None:
            raise _user_error_while_generating_ir(reported.diag)
        return compiler.llvm_ir()


def _user_error_while_generating_ir(err: errors.UserError) -> diag.InternalError:
    return diag.InternalError(f"a user error was found while generating IR: {err}")
