# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import asserts, compilation, errors, ir_env, ir_loader, ir_module, ir_traits, typs
from tests import harness


def load_main(compiler: harness.CompilerHarness, *mods: harness.ModSrc):
    """Write the given modules and load main.leech, returning its loader."""
    for mod in mods:
        compiler.write_mod(mod)
    loader = ir_loader.ModLoader()
    loader.load(compiler.workspace / "main.leech", "main")
    return loader


def test_load_is_memoized(compiler):
    path = compiler.write_mod(harness.ModSrc("main", "pub fn main() i32 { return 0; }"))
    loader = ir_loader.ModLoader()
    assert loader.load(path, "main") is loader.load(path, "main")
    # +1 for the bundled prelude module, always loaded by ModLoader.__init__.
    assert len(loader.mods) == 2


def test_loader_scopes_and_impl_registry_share_compilation_ctx(compiler):
    path = compiler.write_mod(harness.ModSrc("main", "pub fn main() i32 { return 0; }"))
    loader = ir_loader.ModLoader()

    mod = loader.load(path, "main")

    assert mod.env.ctx is loader.ctx
    assert mod.env.new_child().ctx is loader.ctx
    assert loader.impl_registry.ctx is loader.ctx


def test_loaders_have_distinct_compilation_ctxs():
    assert ir_loader.ModLoader().ctx is not ir_loader.ModLoader().ctx


def test_env_and_registry_share_explicit_ctx():
    ctx = compilation.Ctx()
    env = ir_env.Env(ctx, ir_traits.ImplRegistry(ctx), None)
    assert env.impl_registry.ctx is env.ctx


def test_load_normalizes_paths(compiler):
    # The same file named two different ways is one module, which is what
    # keeps a cycle back to the file the CLI was invoked on from
    # reloading it.
    path = compiler.write_mod(harness.ModSrc("main", "pub fn main() i32 { return 0; }"))
    (compiler.workspace / "sub").mkdir()
    loader = ir_loader.ModLoader()
    direct = loader.load(path, "main")
    indirect = loader.load(compiler.workspace / "sub" / ".." / "main.leech", "main")
    assert direct is indirect
    # +1 for the bundled prelude module, always loaded by ModLoader.__init__.
    assert len(loader.mods) == 2


def test_declaration_checking_is_an_explicit_post_load_phase(compiler):
    path = compiler.write_mod(
        harness.ModSrc(
            "main",
            "fn invalid() i32 { return true; }\npub fn main() i32 { return 0; }",
        )
    )
    loader = ir_loader.ModLoader()

    loader.load(path, "main")

    with pytest.raises(errors.InvalidRetTypError):
        loader.check_declarations()


@pytest.mark.parametrize("mod_name", ("library", "main"))
def test_main_fn_is_not_entry_point_without_designation(compiler, mod_name):
    path = compiler.write_mod(harness.ModSrc(mod_name, "fn main() i32 { return 0; }"))
    loader = ir_loader.ModLoader()

    mod = loader.load(path, mod_name)

    fn = next(fn for fn in mod.src_fn_symbols if fn.name == "main")
    assert mod.entry_fn is None
    assert fn.instantiate(()).qualified_name == f"{mod_name}::main"


def test_diamond_loads_each_module_once(compiler):
    loader = load_main(
        compiler,
        harness.ModSrc("main", "import a;\nimport b;\npub fn main() i32 { return 0; }"),
        harness.ModSrc("a", "import c;\npub fn viaa() i32 { return c::base(); }"),
        harness.ModSrc("b", "import c;\npub fn viab() i32 { return c::base(); }"),
        harness.ModSrc("c", "pub fn base() i32 { return 5; }"),
    )
    assert sorted(mod.name for mod in loader.mods) == ["a", "b", "c", "main", "prelude"]


def test_diamond_shares_one_struct_typ(compiler):
    # The point of memoizing: both paths to c must yield the *same*
    # StructTyp object, since struct types are compared by identity.
    loader = load_main(
        compiler,
        harness.ModSrc("main", "import a;\nimport b;\npub fn main() i32 { return 0; }"),
        harness.ModSrc("a", "import c;\npub fn viaa() c::Foo { return c::mk(1); }"),
        harness.ModSrc("b", "import c;\npub fn viab() c::Foo { return c::mk(2); }"),
        harness.ModSrc(
            "c",
            "pub struct Foo { pub v: i32 }\npub fn mk(n: i32) Foo { return Foo{v: n}; }",
        ),
    )
    mods = {mod.name: mod for mod in loader.mods}
    # Each lookup is checked rather than chained straight through: a
    # failed lookup returns None, so two of them would otherwise both be
    # None and make the final assertion pass without proving anything.
    containers = ir_env.Env.Namespace.CONTAINERS
    c_via_a = asserts.checked_cast(mods["a"].env.get(containers, "c"), ir_module.Mod)
    c_via_b = asserts.checked_cast(mods["b"].env.get(containers, "c"), ir_module.Mod)
    foo_via_a = asserts.checked_cast(c_via_a.env.get(containers, "Foo"), typs.StructTyp)
    foo_via_b = asserts.checked_cast(c_via_b.env.get(containers, "Foo"), typs.StructTyp)
    assert foo_via_a is foo_via_b


def test_circular_import_loads_each_module_once(compiler):
    loader = load_main(
        compiler,
        harness.ModSrc("main", "import a;\npub fn main() i32 { return a::f(); }"),
        harness.ModSrc("a", "import b;\npub fn f() i32 { return b::g(); }"),
        harness.ModSrc("b", "import a;\npub fn g() i32 { return 1; }"),
    )
    assert sorted(mod.name for mod in loader.mods) == ["a", "b", "main", "prelude"]
