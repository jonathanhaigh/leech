from l0.ir_loader import ModLoader

from util import write_whole_file


def load_main(tmp_path, **modules):
    """Write the given modules and load main.l0, returning its loader."""
    for mod_name, mod_src in modules.items():
        write_whole_file(tmp_path / f"{mod_name}.l0", mod_src)
    loader = ModLoader()
    loader.load(tmp_path / "main.l0")
    return loader


def test_load_is_memoized(tmp_path):
    write_whole_file(tmp_path / "main.l0", "pub fn main() i32 { return 0; }")
    loader = ModLoader()
    path = tmp_path / "main.l0"
    assert loader.load(path) is loader.load(path)
    assert len(loader.mods) == 1


def test_load_normalizes_paths(tmp_path):
    # The same file named two different ways is one module, which is what
    # keeps a cycle back to the file the CLI was invoked on from
    # reloading it.
    write_whole_file(tmp_path / "main.l0", "pub fn main() i32 { return 0; }")
    loader = ModLoader()
    direct = loader.load(tmp_path / "main.l0")
    indirect = loader.load(tmp_path / "." / "main.l0")
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
    foo_via_a = mods["a"].env.get_typ("c").env.get_typ("Foo")
    foo_via_b = mods["b"].env.get_typ("c").env.get_typ("Foo")
    assert foo_via_a is foo_via_b


def test_circular_import_loads_each_module_once(tmp_path):
    loader = load_main(
        tmp_path,
        main="import a;\npub fn main() i32 { return a::f(); }",
        a="import b;\npub fn f() i32 { return b::g(); }",
        b="import a;\npub fn g() i32 { return 1; }",
    )
    assert sorted(mod.name for mod in loader.mods) == ["a", "b", "main"]
