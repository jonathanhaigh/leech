# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Monomorphization: discovering every concrete generic instance a program reaches.

Generic function/method and struct instances are created on demand — see
ir_module.Fn.instantiate and typs.StructTyp.instance — whenever
type checking, or a previously discovered instance's own body, references
them. This module forces that on-demand machinery to a fixpoint and
collects whatever it produces.

The result over-approximates reachability: it includes any instance any
template's cache holds, however it got there — not only ones a call or
field use within a program's own code actually reaches. It never omits an
instance the program needs, but can include one requested only as a side
effect of validating an unrelated, never-instantiated generic template.
"""

import dataclasses
from collections.abc import Callable, Iterator

from leech import ir_module, typs, visibility


@dataclasses.dataclass(frozen=True)
class MonoResult:
    """Function definitions, external declarations, and struct instances for one module."""

    #: Instances whose bodies this module owns or specializes and must emit.
    fn_instances: tuple[ir_module.FnInstance, ...]
    #: Imported non-generic instances that need declarations but not bodies.
    external_fn_instances: tuple[ir_module.FnInstance, ...]
    struct_instances: tuple[typs.StructTyp, ...]


def discover(mod: ir_module.Mod) -> MonoResult:
    """Discover every concrete fn/method and struct instantiation reachable
    from ``mod``'s own items and its imports' public items.
    """
    # Fn instances first: forcing their bodies can request struct
    # instantiations that only a subsequent struct-discovery pass would catch.
    fn_instances, external_fn_instances = _discover_fn_instances(mod)
    struct_instances = _discover_struct_instances(mod)
    return MonoResult(tuple(fn_instances), tuple(external_fn_instances), tuple(struct_instances))


def _fixpoint[T](newly_requested: Callable[[], list[T]], resolve: Callable[[T], None]) -> list[T]:
    """Resolve newly requested items to a fixpoint and return them in discovery order."""
    instances: list[T] = []
    pending = newly_requested()
    while pending:
        instances.extend(pending)
        for inst in pending:
            resolve(inst)
        pending = newly_requested()
    return instances


def _fn_templates(mod: ir_module.Mod) -> Iterator[ir_module.FnSpec]:
    """Yield every source and compiler-intrinsic function declaration."""
    for loaded_mod in mod.loader.mods:
        yield from loaded_mod.fns
    yield from mod.loader.builtins


def _is_external_fn_instance(inst: ir_module.FnInstance, mod: ir_module.Mod) -> bool:
    """Return whether ``inst`` is a non-generic body owned by another module."""
    source_fn = inst.source_fn
    return (
        source_fn is not None and not inst.uses_generic_linkage and source_fn.mod_name != mod.name
    )


def _discover_fn_instances(
    mod: ir_module.Mod,
) -> tuple[list[ir_module.FnInstance], list[ir_module.FnInstance]]:
    """Discover reachable function instances to a fixpoint.

    Lowering an instance can request more instances, so both kinds
    must participate in the same fixpoint.

    :return: Body-owning instances and external declaration instances,
        each in discovery order.
    """
    seen: set[ir_module.FnInstance] = set()

    def newly_requested() -> list[ir_module.FnInstance]:
        new = []
        for template in _fn_templates(mod):
            for inst in template.instances:
                if inst.is_concrete() and inst not in seen:
                    seen.add(inst)
                    new.append(inst)
        return new

    def lower(inst: ir_module.FnInstance) -> None:
        if not _is_external_fn_instance(inst, mod):
            _ = inst.cfg

    for fn in mod.fns:
        if not fn.is_generic and (fn.is_main or fn.access == visibility.PUBLIC):
            fn.instantiate(())

    # Module variables are still emitted as ordinary module items. Lower
    # every local initializer before discovering function instances it requests.
    for item in mod.items:
        if isinstance(item.value, ir_module.ModVar):
            _ = item.value.cfg

    discovered = _fixpoint(newly_requested, lower)
    local = []
    external = []
    for inst in discovered:
        if _is_external_fn_instance(inst, mod):
            external.append(inst)
        else:
            local.append(inst)
    return local, external


def _discover_struct_instances(mod: ir_module.Mod) -> list[typs.StructTyp]:
    """Find every generic struct instantiation reachable from the program.

    Resolving fields can request further instances. Non-concrete
    instances created while checking generic definitions are ignored.

    :return: Every instantiation discovered, in discovery order.
    """
    seen: set[typs.StructTyp] = set()

    def newly_requested() -> list[typs.StructTyp]:
        new = []
        for loaded_mod in mod.loader.mods:
            for item in loaded_mod.items:
                if isinstance(item.value, typs.StructTyp) and item.value.is_generic_template:
                    for inst in item.value.instances:
                        if inst.is_concrete() and inst not in seen:
                            seen.add(inst)
                            new.append(inst)
        return new

    def resolve_fields(inst: typs.StructTyp) -> None:
        _ = inst.fields

    return _fixpoint(newly_requested, resolve_fields)
