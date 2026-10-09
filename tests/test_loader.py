# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib

import pytest

from leech import asserts, compilation, diag, errors, ir_env, ir_loader, ir_module, src, typs
from tests import harness


def load_main(compiler: harness.CompilerHarness, *mods: harness.ModSrc):
    """Write the given modules and load main.leech, returning its loader."""
    for mod in mods:
        compiler.write_mod(mod)
    loader = compilation.Ctx().loader
    loader.load_root(compiler.workspace / "main.leech", "main")
    return loader


def test_load_is_memoized(compiler):
    path = compiler.write_mod(harness.ModSrc("main", "pub fn main() i32 { return 0; }"))
    loader = compilation.Ctx().loader
    root = loader.load_root(path, "main")
    package = ir_loader.Package(compiler.workspace.resolve(), None)
    assert loader.load(ir_loader.ModId(package, ("main",))) is root
    # +1 for the bundled prelude module, always loaded before any other module.
    assert len(loader.mods) == 2


def test_one_compilation_shares_one_ctx(compiler):
    path = compiler.write_mod(harness.ModSrc("main", "pub fn main() i32 { return 0; }"))
    ctx = compilation.Ctx()

    mod = ctx.loader.load_root(path, "main")

    assert ctx.loader.ctx is ctx
    assert ctx.impl_registry.ctx is ctx
    assert ctx.builtins.size_of.env.ctx is ctx
    assert all(loaded.ctx is ctx for loaded in ctx.loader.mods)
    assert mod.env.new_child().ctx is ctx


def test_ctxs_are_distinct_compilations():
    first = compilation.Ctx()
    second = compilation.Ctx()

    assert first.loader is not second.loader
    assert first.impl_registry is not second.impl_registry
    assert first.builtins is not second.builtins


def test_constructing_a_ctx_loads_nothing():
    ctx = compilation.Ctx()

    assert len(ctx.loader.mods) == 0
    assert ctx.loader.prelude is not None
    assert len(ctx.loader.mods) == 1


def test_load_normalizes_paths(compiler):
    # The same file named two different ways is one module, which is what
    # keeps a cycle back to the file the CLI was invoked on from
    # reloading it.
    path = compiler.write_mod(harness.ModSrc("main", "pub fn main() i32 { return 0; }"))
    (compiler.workspace / "sub").mkdir()
    loader = compilation.Ctx().loader
    direct = loader.load_root(path, "main")
    package = ir_loader.Package(compiler.workspace / "sub" / "..", None)
    indirect = loader.load(ir_loader.ModId(package, ("main",)))
    assert direct is indirect
    # +1 for the bundled prelude module, always loaded before any other module.
    assert len(loader.mods) == 2


def test_declaration_checking_is_an_explicit_post_load_phase(compiler):
    path = compiler.write_mod(
        harness.ModSrc(
            "main",
            "fn invalid() i32 { return true; }\npub fn main() i32 { return 0; }",
        )
    )
    ctx = compilation.Ctx()

    ctx.loader.load_root(path, "main")
    assert ctx.diags.all() == ()
    ctx.loader.check_declarations()

    assert [type(d) for d in ctx.diags.all()] == [errors.InvalidRetTypError]


@pytest.mark.parametrize("mod_name", ("library", "main"))
def test_main_fn_is_not_entry_point_without_designation(compiler, mod_name):
    path = compiler.write_mod(harness.ModSrc(mod_name, "fn main() i32 { return 0; }"))
    loader = compilation.Ctx().loader

    mod = loader.load_root(path, mod_name)

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
    assert sorted(mod.name for mod in loader.mods) == ["a", "b", "c", "main", "std::prelude"]


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
    assert sorted(mod.name for mod in loader.mods) == ["a", "b", "main", "std::prelude"]


def test_prelude_is_the_std_prelude_module():
    prelude = compilation.Ctx().loader.prelude

    assert prelude is not None
    assert prelude.name == "std::prelude"


def test_root_package_is_the_directory_implied_by_the_root_name(tmp_path):
    (tmp_path / "x").mkdir()
    path = tmp_path / "x" / "b.leech"
    path.write_text("import x::a;\npub fn f() i32 { return a::g(); }\n")
    (tmp_path / "x" / "a.leech").write_text("pub fn g() i32 { return 1; }\n")
    loader = compilation.Ctx().loader

    loader.load_root(path, "x::b")

    assert sorted(mod.name for mod in loader.mods) == ["std::prelude", "x::a", "x::b"]


@pytest.mark.parametrize(
    ("rel_path", "name"),
    (("a.leech", "x::a"), ("x/a.leech", "y::a"), ("x/a.leech", "x::b"), ("a.txt", "a")),
)
def test_root_name_must_match_its_location(tmp_path, rel_path, name):
    path = tmp_path / rel_path
    path.parent.mkdir(exist_ok=True)
    path.write_text("pub fn f() i32 { return 0; }\n")

    with pytest.raises(errors.ModNameLocationMismatchError) as exc_info:
        compilation.Ctx().loader.load_root(path, name)

    assert str(exc_info.value).startswith(f'Module name "{name}" does not match the location ')


def test_std_names_are_reserved_for_the_bundled_library(tmp_path):
    (tmp_path / "std").mkdir()
    path = tmp_path / "std" / "io.leech"
    path.write_text("pub fn f() i32 { return 0; }\n")

    with pytest.raises(errors.StdModNameReservedError):
        compilation.Ctx().loader.load_root(path, "std::io")


def test_bundled_module_root_is_named_in_the_std_package():
    path = pathlib.Path(ir_loader.__file__).parent / "std" / "io.leech"
    loader = compilation.Ctx().loader

    assert loader.load_root(path, "std::io").name == "std::io"
    with pytest.raises(errors.ModNameLocationMismatchError):
        compilation.Ctx().loader.load_root(path, "io")


def test_syntax_error_in_a_bundled_module_is_an_internal_error(tmp_path):
    path = tmp_path / "broken.leech"
    path.write_text("fn broken(")

    with pytest.raises(diag.InternalError, match="has a syntax error: unexpected end of input"):
        ir_loader._parse_bundled_mod_ast(path)


def test_root_may_share_a_name_with_a_bundled_module(tmp_path):
    path = tmp_path / "prelude.leech"
    path.write_text("pub fn f() i32 { return 0; }\n")
    loader = compilation.Ctx().loader

    loader.load_root(path, "prelude")

    assert sorted(mod.name for mod in loader.mods) == ["prelude", "std::prelude"]


def _span(compiler) -> src.SrcSpan:
    path = compiler.write_mod(harness.ModSrc("main", "pub fn main() i32 { return 0; }"))
    return src.SrcSpan(src.SrcFile(path), 0, 1, 1, 1, 1, 2)


def test_env_binding_binds_the_value_after_the_block(compiler):
    env = ir_env.Env(compilation.Ctx())

    with env.binding(ir_env.Env.Namespace.CONTAINERS, "t", _span(compiler)) as binding:
        assert env.get(ir_env.Env.Namespace.CONTAINERS, "t") is None
        binding.bind(typs.BOOL)

    assert env.get(ir_env.Env.Namespace.CONTAINERS, "t") is typs.BOOL


def test_env_binding_binds_nothing_if_the_block_raises(compiler):
    env = ir_env.Env(compilation.Ctx())

    with (
        pytest.raises(RuntimeError),
        env.binding(ir_env.Env.Namespace.CONTAINERS, "t", _span(compiler)),
    ):
        raise RuntimeError("build failed")

    assert env.get(ir_env.Env.Namespace.CONTAINERS, "t") is None


def test_env_binding_checks_the_name_before_the_block(compiler):
    env = ir_env.Env(compilation.Ctx())
    env.add_container("t", typs.BOOL)
    ran = False

    with (
        pytest.raises(errors.DuplicateItemDefnError),
        env.binding(ir_env.Env.Namespace.CONTAINERS, "t", _span(compiler)),
    ):
        ran = True

    assert not ran
