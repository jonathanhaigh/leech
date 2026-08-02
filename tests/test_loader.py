# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import util

from leech import asserts, ir_env, ir_loader, ir_module, typs


def load_main(tmp_path, **modules):
    """Write the given modules and load main.leech, returning its loader."""
    for mod_name, mod_src in modules.items():
        util.write_whole_file(tmp_path / f"{mod_name}.leech", mod_src)
    loader = ir_loader.ModLoader()
    loader.load(tmp_path / "main.leech")
    return loader


def test_load_is_memoized(tmp_path):
    util.write_whole_file(tmp_path / "main.leech", "pub fn main() i32 { return 0; }")
    loader = ir_loader.ModLoader()
    path = tmp_path / "main.leech"
    assert loader.load(path) is loader.load(path)
    assert len(loader.mods) == 1


def test_load_normalizes_paths(tmp_path):
    # The same file named two different ways is one module, which is what
    # keeps a cycle back to the file the CLI was invoked on from
    # reloading it.
    util.write_whole_file(tmp_path / "main.leech", "pub fn main() i32 { return 0; }")
    loader = ir_loader.ModLoader()
    direct = loader.load(tmp_path / "main.leech")
    indirect = loader.load(tmp_path / "." / "main.leech")
    assert direct is indirect
    assert len(loader.mods) == 1


def test_diamond_loads_each_module_once(tmp_path):
    loader = load_main(
        tmp_path,
        main="import a;\nimport b;\npub fn main() i32 { return 0; }",
        a="import c;\npub fn viaa() i32 { return c::base(); }",
        b="import c;\npub fn viab() i32 { return c::base(); }",
        c="pub fn base() i32 { return 5; }",
    )
    assert sorted(mod.name for mod in loader.mods) == ["a", "b", "c", "main"]


def test_diamond_shares_one_struct_typ(tmp_path):
    # The point of memoizing: both paths to c must yield the *same*
    # StructTyp object, since struct types are compared by identity.
    loader = load_main(
        tmp_path,
        main="import a;\nimport b;\npub fn main() i32 { return 0; }",
        a="import c;\npub fn viaa() c::Foo { return c::mk(1); }",
        b="import c;\npub fn viab() c::Foo { return c::mk(2); }",
        c="pub struct Foo { pub v: i32 }\npub fn mk(n: i32) Foo { return Foo{v: n}; }",
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


def test_circular_import_loads_each_module_once(tmp_path):
    loader = load_main(
        tmp_path,
        main="import a;\npub fn main() i32 { return a::f(); }",
        a="import b;\npub fn f() i32 { return b::g(); }",
        b="import a;\npub fn g() i32 { return 1; }",
    )
    assert sorted(mod.name for mod in loader.mods) == ["a", "b", "main"]
