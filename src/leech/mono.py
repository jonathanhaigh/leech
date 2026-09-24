# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Discover concrete function, struct and union instances requested by one compilation.

Function and struct instances are created on demand while type checking and lowering.
Discovery drains the compilation context's live request logs; resolving one request may
append more work to the same log. The result remains an over-approximation of reachability
because validation of an unused declaration can itself request an instance.
"""

import dataclasses

from leech import ir_module, typs, visibility


@dataclasses.dataclass(frozen=True)
class MonoResult:
    """Function definitions, imported declarations, and nominal type instances."""

    #: Instances whose bodies this module owns or specializes and must emit.
    fn_instances: tuple[ir_module.FnInstance, ...]
    #: Imported non-generic instances that need declarations but not bodies.
    imported_fn_instances: tuple[ir_module.FnInstance, ...]
    struct_instances: tuple[typs.StructTyp, ...]
    union_instances: tuple[typs.UnionTyp, ...]


def discover(mod: ir_module.Mod) -> MonoResult:
    """Drain and resolve the compilation's concrete function, struct and union requests."""
    # Fn instances first: forcing their bodies can request type
    # instantiations that only a subsequent type-discovery pass would catch.
    # That dependency runs one way, so one pass over functions is enough.
    fn_instances, imported_fn_instances = _discover_fn_instances(mod)
    struct_instances, union_instances = _discover_typ_instances(mod)
    return MonoResult(
        tuple(fn_instances),
        tuple(imported_fn_instances),
        tuple(struct_instances),
        tuple(union_instances),
    )


def _is_imported_fn_instance(inst: ir_module.FnInstance, mod: ir_module.Mod) -> bool:
    """Return whether ``inst`` is a non-generic body owned by another module."""
    src_fn = inst.src_fn
    return src_fn is not None and not inst.uses_generic_linkage and src_fn.mod_name != mod.name


def _discover_fn_instances(
    mod: ir_module.Mod,
) -> tuple[list[ir_module.FnInstance], list[ir_module.FnInstance]]:
    """Discover requested function instances while draining the live request log.

    Returns body-owning instances and imported declaration instances, each
    in discovery order.
    """
    for fn in mod.src_fn_symbols:
        if not fn.is_generic and (fn.is_main or fn.access == visibility.PUBLIC):
            fn.instantiate(())

    # Module variables are still emitted as ordinary module items. Lower
    # every local initializer before discovering function instances it requests.
    for item in mod.items:
        if isinstance(item.value, ir_module.ModVar):
            _ = item.value.cfg

    discovered = []
    requests = mod.loader.ctx.requested_fn_instances()
    cursor = 0
    while cursor < len(requests):
        inst = requests[cursor]
        cursor += 1
        if not inst.is_concrete():
            continue
        # Externs have no body to lower or definition to emit. Their module-item walk owns
        # their declarations independently of reachability, so including one here would
        # send a bodyless instance down the definition path.
        if not inst.has_body:
            continue
        if not _is_imported_fn_instance(inst, mod):
            _ = inst.cfg
        discovered.append(inst)

    local = []
    imported = []
    for inst in discovered:
        if _is_imported_fn_instance(inst, mod):
            imported.append(inst)
        else:
            local.append(inst)
    return local, imported


def _discover_typ_instances(
    mod: ir_module.Mod,
) -> tuple[list[typs.StructTyp], list[typs.UnionTyp]]:
    """Drain the struct and union request logs to a joint fixed point.

    The two logs are mutually recursive: forcing a struct's fields can request a union,
    and forcing that union's payloads can request a further struct. Draining one and then
    the other would let the second append to the log the first had already finished with,
    silently omitting a reachable instance, so each keeps its own cursor and they
    alternate until neither advances. Non-concrete instances created while checking
    generic definitions are ignored. Returns every instantiation discovered, in discovery
    order.
    """
    ctx = mod.loader.ctx
    struct_requests = ctx.requested_struct_instances()
    union_requests = ctx.requested_union_instances()
    structs: list[typs.StructTyp] = []
    unions: list[typs.UnionTyp] = []
    struct_cursor = 0
    union_cursor = 0

    while struct_cursor < len(struct_requests) or union_cursor < len(union_requests):
        while struct_cursor < len(struct_requests):
            struct_inst = struct_requests[struct_cursor]
            struct_cursor += 1
            if not struct_inst.is_concrete():
                continue
            _ = struct_inst.fields
            structs.append(struct_inst)
        while union_cursor < len(union_requests):
            union_inst = union_requests[union_cursor]
            union_cursor += 1
            if not union_inst.is_concrete():
                continue
            for variant in union_inst.variants:
                _ = variant.payload_typs
            unions.append(union_inst)

    return structs, unions
