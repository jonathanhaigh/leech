# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Discover the concrete function, struct and union instances a program needs.

Function and struct instances are created on demand while type checking and lowering.
Discovery drains the compilation context's live request logs; resolving one request may
append more work to the same log. The result remains an over-approximation of reachability
because validation of an unused declaration can itself request an instance.

Discovery is part of checking: resolving a request can find a user error that depends on
the instance, such as an instance with infinite size. Each request is resolved separately,
so an error in one is reported and discovery continues with the rest, leaving the failed
instance out of the result.
"""

import dataclasses

from leech import compilation, ir_module, typs, visibility


@dataclasses.dataclass(frozen=True)
class MonoResult:
    """The function instances to define, and the nominal type instances, in discovery order."""

    fn_instances: tuple[ir_module.FnInstance, ...]
    struct_instances: tuple[typs.StructTyp, ...]
    union_instances: tuple[typs.UnionTyp, ...]


def discover(ctx: compilation.Ctx) -> MonoResult:
    """Drain and resolve the compilation's concrete function, struct and union requests.

    Discovery starts from every loaded module's public non-generic functions and module
    variables, and the entry point.
    """
    # Fn instances first: forcing their bodies can request type
    # instantiations that only a subsequent type-discovery pass would catch.
    # That dependency runs one way, so one pass over functions is enough.
    fn_instances = _discover_fn_instances(ctx)
    struct_instances, union_instances = _discover_typ_instances(ctx)
    return MonoResult(tuple(fn_instances), tuple(struct_instances), tuple(union_instances))


def _discover_fn_instances(ctx: compilation.Ctx) -> list[ir_module.FnInstance]:
    """Discover requested function instances while draining the live request log."""
    for mod in ctx.loader.mods:
        for fn in mod.src_fn_symbols:
            if not fn.is_generic and fn.access == visibility.PUBLIC:
                fn.instantiate(())
        if mod.entry_fn is not None:
            mod.entry_fn.instantiate(())

    # Module variables are emitted as ordinary module items. Lower every
    # initializer before discovering the function instances it requests.
    for mod in ctx.loader.mods:
        for item in mod.items:
            if isinstance(item.value, ir_module.ModVar):
                with ctx.recovering():
                    _ = item.value.cfg

    discovered = []
    requests = ctx.requested_fn_instances()
    cursor = 0
    while cursor < len(requests):
        inst = requests[cursor]
        cursor += 1
        with ctx.recovering() as recovery:
            if not _needs_code(inst):
                continue
            # Lowering the body may append further requests.
            _ = inst.cfg
        if recovery.failure is None:
            discovered.append(inst)
    return discovered


def _needs_code(inst: ir_module.FnInstance) -> bool:
    """Return whether code generation needs to define ``inst``.

    A non-concrete instance, created while checking a generic body, has no code of its own.
    An extern has no body to define, and the module-item walk declares it independently of
    reachability, so including one would send a bodyless instance down the definition path.
    """
    return inst.is_concrete() and inst.has_body


def _discover_typ_instances(
    ctx: compilation.Ctx,
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
            with ctx.recovering() as recovery:
                struct_inst.check()
            if recovery.failure is None:
                structs.append(struct_inst)
        while union_cursor < len(union_requests):
            union_inst = union_requests[union_cursor]
            union_cursor += 1
            if not union_inst.is_concrete():
                continue
            with ctx.recovering() as recovery:
                union_inst.check()
            if recovery.failure is None:
                unions.append(union_inst)

    return structs, unions
