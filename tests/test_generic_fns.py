# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import asserts, ast, errors, ir_env, ir_module, ir_values, mono, typs


def _get_fn(mod, name: str) -> ir_module.SrcFnSymbol:
    """Get the plain (non-generic) function ``name`` declares in ``mod``."""
    item = mod.get_item(ir_env.Env.Namespace.VARS, name)
    assert item is not None
    return asserts.checked_cast(item.value, ir_module.SrcFnSymbol)


def _get_generic_fn(mod, name: str) -> ir_module.SrcFnSymbol:
    """Get the generic function ``name`` declares in ``mod``."""
    item = mod.get_item(ir_env.Env.Namespace.VARS, name)
    assert item is not None
    return asserts.checked_cast(item.value, ir_module.SrcFnSymbol)


def _lower_main(tmp_path, src):
    """Build and lower ``main``'s body, without compiling it to LLVM IR.

    Codegen doesn't emit generic function instances yet, so this is how
    a test confirms a call to one type-checks and lowers correctly -
    including that its type arguments resolve, and that the call's
    callee is the right instance - short of actually running it.

    :return: ``main``'s lowered control-flow graph.
    """
    mod = util.build_ir_mod(tmp_path, src)
    return _get_fn(mod, "main").instantiate(()).cfg


def _get_extern_decl(tmp_path) -> ir_module.ExternFnSymbol:
    """Build and return a representative extern declaration."""
    mod = util.build_ir_mod(
        tmp_path,
        "extern fn exit(code: i32);\nfn f(x: i32) i32 { x }\npub fn main() i32 { f(1) }",
    )
    decl_item = mod.get_item(ir_env.Env.Namespace.VARS, "exit")
    assert decl_item is not None
    return asserts.checked_cast(decl_item.value, ir_module.ExternFnSymbol)


def test_extern_instance_cfg_is_rejected(tmp_path):
    decl = _get_extern_decl(tmp_path)

    with pytest.raises(AssertionError, match="extern instances have no body"):
        _ = decl.instantiate(()).cfg


def test_generic_fn_body_typechecks_with_identity_only_ops(tmp_path):
    # A generic function's body is checked eagerly, whether or not it's
    # ever called - so this only has to compile and run, never invoking
    # `id`.
    src = """
    fn id[T](x: T) T {
        let y = x;
        let p = &y;
        return y;
    }

    pub fn main() i32 {
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_uncalled_private_fn_body_is_still_typechecked(tmp_path):
    src = """
    fn invalid() i32 { return true; }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InvalidRetTypError):
        util.compile_str(tmp_path, src)


def test_uncalled_private_fn_is_not_emitted(tmp_path):
    src = """
    fn unused_private() i32 { return 1; }
    pub fn main() i32 { return 0; }
    """
    llir_path = util.compile_str(tmp_path, src)
    assert 'define private i32 @"main::unused_private"' not in llir_path.read_text()


def test_uncalled_public_generic_fn_has_no_instance_to_emit(tmp_path):
    src = """
    pub fn unused_generic[T](x: T) T { return x; }
    pub fn main() i32 { return 0; }
    """
    llir_path = util.compile_str(tmp_path, src)
    assert "unused_generic[" not in llir_path.read_text()


def test_fn_candidate_applies_impl_args_before_fn_args(tmp_path):
    mod = util.build_ir_mod(tmp_path, "fn id[T](x: T) T { x }")
    fn = _get_generic_fn(mod, "id")
    candidate = ir_module.FnCandidate(fn, (typs.BOOL,), (typs.I32,))

    assert candidate.apply((typs.I32,)) == ir_module.AppliedFn(fn, (typs.BOOL, typs.I32))


def test_explicit_fn_application_is_recorded_before_instantiation(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        "fn id[T](x: T) T { x } pub fn main() i32 { id[i32](1) }",
    )
    fn = _get_generic_fn(mod, "id")
    main = _get_fn(mod, "main")
    main_ast = asserts.checked_cast(main.ast, ast.FnDefn)
    call_ast = asserts.checked_cast(main_ast.block.expr, ast.CallExpr)
    callee_ast = asserts.checked_cast(call_ast.callee, ast.VarExpr)

    candidate = main.typ_check_results.resolutions.var(callee_ast)
    assert candidate == ir_module.FnCandidate(fn, (), (typs.I32,))
    assert main.typ_check_results.applied_fn(callee_ast) == ir_module.AppliedFn(fn, (typs.I32,))
    assert all(inst.src_fn is not fn for inst in mod.loader.ctx.requested_fn_instances())

    _ = main.instantiate(()).cfg

    instances = [inst for inst in mod.loader.ctx.requested_fn_instances() if inst.src_fn is fn]
    assert [inst.args for inst in instances] == [(typs.I32,)]


def test_function_instance_symbols_and_linkage(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    struct Foo {}
    impl Show for Foo { fn show(*self) i32 { 1 } }
    impl Foo { fn get(*self) i32 { 2 } }

    struct Box[T] { val: T }
    impl[T] Box[T] { fn unwrap(*self) T { self.*.val } }

    fn private_fn() i32 { 3 }
    pub fn public_fn() i32 { 4 }
    fn id[T](x: T) T { x }

    pub fn main() i32 {
        let foo = Foo {};
        let box = Box[i32] { val: 5 };
        return foo.show() + foo.get() + box.unwrap() + private_fn()
            + public_fn() + id[i32](6) - 21;
    }
    """
    ir_text = util.compile_str(tmp_path, src).read_text()

    assert 'define i32 @"main"' in ir_text
    assert '@"main::main"' not in ir_text
    assert 'define private i32 @"<main::Foo as main::Show>::show"' in ir_text
    assert 'define private i32 @"main::Foo::get"' in ir_text
    assert 'define private i32 @"main::private_fn"' in ir_text
    assert 'define i32 @"main::public_fn"' in ir_text
    assert 'define linkonce_odr i32 @"main::id[i32]"' in ir_text
    assert 'define linkonce_odr i32 @"main::Box[i32]::unwrap"' in ir_text


def test_generic_fn_with_multiple_typ_params_typechecks(tmp_path):
    src = """
    fn pair_first[T, U](x: T, y: U) T {
        let a: T = x;
        let b: U = y;
        return a;
    }

    pub fn main() i32 {
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_let_stmt_declared_typ_param_is_substituted_when_invoked(tmp_path):
    # The declared type `T` TypCheck resolved (still abstract) must be
    # substituted to the concrete instantiation's own type.
    src = """
    fn first[T](x: T, y: T) T {
        let a: T = x;
        return a;
    }
    pub fn main() i32 {
        return first(42i32, 0) - 42;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_typ_arg_inferred_through_array_param(tmp_path):
    # Exercises ArrayTyp.infer_typ_args: T must be inferred from the
    # actual argument's element type, not just from a bare-T parameter.
    src = """
    fn first[T](arr: array[T, 3]) T {
        return arr.[0usize];
    }
    pub fn main() i32 {
        return first(array[i32, 3]{1, 2, 3}) - 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_let_stmt_declared_generic_struct_typ_is_substituted(tmp_path):
    # The declared type can itself be a generic struct instantiation that
    # depends on the enclosing function's own type parameter, not just a
    # bare type parameter.
    src = """
    struct Box[T] { mut val: T }
    fn wrap[T](x: T) T {
        let b: Box[T] = Box[T] { val: x };
        return b.val;
    }
    pub fn main() i32 {
        return wrap(5i32) - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_body_rejects_arithmetic_on_typ_param(tmp_path):
    src = """
    fn f[T](x: T) T {
        return x + x;
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InvalidBinOpArgTypError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"T"' in str(exc_info.value)


def test_generic_fn_body_rejects_field_access_on_typ_param(tmp_path):
    src = """
    fn f[T](x: T) i32 {
        return x.field;
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.FieldAccessIntoInvalidTypError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"T"' in str(exc_info.value)


def test_generic_fn_body_rejects_indexing_typ_param(tmp_path):
    src = """
    fn f[T](x: T) T {
        return x.[0];
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.IndexIntoInvalidTypError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"T"' in str(exc_info.value)


def test_generic_fn_body_rejects_calling_typ_param(tmp_path):
    src = """
    fn f[T](x: T) i32 {
        x();
        return 0;
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.NotCallableError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"T"' in str(exc_info.value)


def test_generic_fn_body_rejects_deref_of_typ_param(tmp_path):
    src = """
    fn f[T](x: T) T {
        return x.*;
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.DerefInvalidTypError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"T"' in str(exc_info.value)


def test_generic_fn_distinct_typ_params_are_incompatible(tmp_path):
    # T and U are different types even though neither is concrete yet -
    # nothing but identity coerces, so a U can't be used as a T.
    src = """
    fn f[T, U](x: T, y: U) T {
        let z: T = y;
        return x;
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.IncompatibleLetTypError) as exc_info:
        util.compile_str(tmp_path, src)
    msg = str(exc_info.value)
    assert '"T"' in msg
    assert '"U"' in msg


def test_bare_reference_to_generic_fn_requires_typ_args(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }
    pub fn main() i32 {
        let f = id;
        return 0;
    }
    """
    with pytest.raises(errors.MissingComptimeArgsError) as exc_info:
        util.compile_str(tmp_path, src)

    assert '"id"' in str(exc_info.value)
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "id;")


def test_address_of_generic_fn_requires_typ_args(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }
    pub fn main() i32 {
        let p = &id;
        return 0;
    }
    """
    with pytest.raises(errors.MissingComptimeArgsError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"id"' in str(exc_info.value)


def test_generic_fn_instance_as_function_pointer(tmp_path):
    # id[i32], applied but never called directly, is itself a value - an
    # ordinary function pointer from here on.
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        let f = id[i32];
        return f(5) - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_instance_pointer_called_multiple_times(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        let f = id[i32];
        let a = f(3);
        let b = f(4);
        return (a + b) - 7;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_address_of_explicit_generic_fn_instance_is_a_noop(tmp_path):
    # Taking the address of a function reference is a no-op (it's
    # already a pointer) - true of an explicitly-applied generic one too.
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        let f = &id[i32];
        return f(5) - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_wrong_number_of_explicit_typ_args_on_bare_generic_fn_reference(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }
    pub fn main() i32 {
        let g = id[i32, bool];
        return 0;
    }
    """
    with pytest.raises(errors.WrongNumberOfComptimeArgsError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"id"' in str(exc_info.value)


def test_explicit_typ_args_on_non_generic_fn_reference(tmp_path):
    src = """
    fn f(x: i32) i32 { return x; }
    pub fn main() i32 {
        let g = f[i32];
        return 0;
    }
    """
    with pytest.raises(errors.ComptimeArgsOnNonGenericItemError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"f"' in str(exc_info.value)


def test_calling_generic_fn_infers_typ_args_from_argument(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        let n: i32 = 5;
        return id(n) - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_inferred_generic_arg_block_local_binding_survives_probe(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        let x = id({ let y = 1i32; y });
        return x - 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_inferred_generic_arg_match_binding_survives_probe(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        let x = id(match (1) {
            let y => y - 0,
        });
        return x;
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_calling_generic_fn_with_explicit_typ_args(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        return id[i32](5) - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_calling_generic_fn_infers_typ_args_across_multiple_params(tmp_path):
    src = """
    fn pair_first[T, U](x: T, y: U) T {
        return x;
    }

    pub fn main() i32 {
        let a: i32 = 3;
        let b: bool = true;
        return pair_first(a, b) - 3;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_calling_generic_fn_from_another_module(tmp_path):
    a_src = "pub fn id[T](x: T) T { return x; }"
    main_src = """
    import a;

    pub fn main() i32 {
        let n: i32 = 7;
        return a::id(n) - 7;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src)


def test_generic_fn_instance_merges_across_modules(tmp_path):
    # Both a (compiled as its own root, since it's given as a separate
    # module below) and main independently request id[i32] - each emits
    # its own linkonce_odr copy, and the linker has to merge them rather
    # than reject the pair as a duplicate definition.
    a_src = """
    pub fn id[T](x: T) T { return x; }

    pub fn use_id_internally() i32 {
        return id[i32](9) - 9;
    }
    """
    main_src = """
    import a;

    pub fn main() i32 {
        let n: i32 = 7;
        return (a::id(n) - 7) + a::use_id_internally();
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src)


def test_multiple_typ_args_coexist_for_the_same_generic_fn(tmp_path):
    # id[i32] and id[bool] are different instances of the same generic
    # function, each with their own body and symbol name - using both in
    # the same program shouldn't let one clobber the other.
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        let n = id[i32](5);
        let b = id[bool](true);
        if (b) { return n - 5; };
        return 99;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_over_pointer_typ_param(tmp_path):
    src = """
    fn deref[T](p: *T) T { return p.*; }

    pub fn main() i32 {
        let n: i32 = 42;
        return deref(&n) - 42;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_with_struct_typ_arg(tmp_path):
    # Structs aren't generic themselves, but a struct type can still be
    # used as the concrete type argument applying a generic function.
    src = """
    struct Point { x: i32, y: i32 }

    fn id[T](v: T) T { return v; }

    pub fn main() i32 {
        let p = id(Point { x: 3, y: 4 });
        return (p.x + p.y) - 7;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_infers_typ_arg_through_generic_struct_arg(tmp_path):
    # The declared parameter type is itself a generic struct application,
    # `Box[T]` - inferring T has to look inside the argument's own type
    # arguments, not just match the whole argument type as an opaque unit.
    src = """
    struct Box[T] { mut val: T }

    fn unwrap[T](b: Box[T]) T { return b.val; }

    pub fn main() i32 {
        let b = Box[i32] { val: 42 };
        return unwrap(b) - 42;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_recursion_runtime_output(tmp_path):
    src = """
    fn depth[T](x: T, n: i32) T {
        if (n <= 0) {
            return x;
        };
        return depth(x, n - 1);
    }

    pub fn main() i32 {
        let start: i32 = 11;
        return depth(start, 3) - 11;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_mod_var_initializer_calls_generic_fn_with_explicit_typ_args(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    let x = id[i32](5);

    pub fn main() i32 {
        return x - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_mod_var_initializer_calls_generic_fn_with_inferred_typ_args(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    let n: i32 = 5;
    let x = id(n);

    pub fn main() i32 {
        return x - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_mod_var_initializer_stores_generic_fn_instance_as_function_pointer(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    let f = id[i32];

    pub fn main() i32 {
        return f(5) - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_two_mod_var_initializers_use_different_typ_args_of_same_generic_fn(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    let x = id[i32](5);
    let b = id[bool](true);

    pub fn main() i32 {
        if (b) { return x - 5; };
        return 99;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_calling_generic_fn_cannot_infer_typ_arg_from_bare_int_lit(tmp_path):
    # A bare integer literal has no type of its own until it has a
    # target, so it can't drive inference on its own.
    src = """
    fn id[T](x: T) T { return x; }
    pub fn main() i32 {
        id(5);
        return 0;
    }
    """
    with pytest.raises(errors.CannotInferComptimeArgError) as exc_info:
        util.compile_str(tmp_path, src)
    msg = str(exc_info.value)
    assert '"T"' in msg
    assert '"id"' in msg


def test_calling_generic_fn_with_wrong_number_of_explicit_typ_args(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }
    pub fn main() i32 {
        id[i32, bool](5);
        return 0;
    }
    """
    with pytest.raises(errors.WrongNumberOfComptimeArgsError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"id"' in str(exc_info.value)


def test_explicit_typ_args_on_non_generic_fn(tmp_path):
    src = """
    fn f(x: i32) i32 { return x; }
    pub fn main() i32 {
        f[i32](5);
        return 0;
    }
    """
    with pytest.raises(errors.ComptimeArgsOnNonGenericItemError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"f"' in str(exc_info.value)


def test_generic_assoc_fn_not_yet_supported(tmp_path):
    # Generic associated functions aren't supported yet; this is a
    # deliberate, loud failure rather than silently mistreating the
    # method's one literal FnTyp as real.
    src = """
    struct Foo {}
    impl Foo {
        fn id[T](x: T) T { return x; }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(NotImplementedError):
        util.compile_str(tmp_path, src)


def test_fn_instance_caches_by_typ_args(tmp_path):
    mod = util.build_ir_mod(tmp_path, "fn id[T](x: T) T { return x; }")
    fn = _get_generic_fn(mod, "id")

    i32_inst = fn.instantiate((typs.I32,))

    assert i32_inst is fn.instantiate((typs.I32,))
    assert i32_inst is not fn.instantiate((typs.BOOL,))
    assert tuple(mod.loader.ctx.requested_fn_instances()).count(i32_inst) == 1


def test_fn_instance_caches_one_reference(tmp_path):
    mod = util.build_ir_mod(tmp_path, "fn id[T](x: T) T { return x; }")
    fn = _get_generic_fn(mod, "id")

    inst = fn.instantiate((typs.I32,))

    assert inst.ref is inst.ref
    assert inst.ref.instance is inst


def test_fn_instance_signature_is_substituted(tmp_path):
    mod = util.build_ir_mod(tmp_path, "fn id[T](x: T) T { return x; }")
    fn = _get_generic_fn(mod, "id")

    inst = fn.instantiate((typs.I32,))
    assert inst.fn_typ.param_typs == (typs.I32,)
    assert inst.fn_typ.ret_typ is typs.I32


def test_fn_instance_mangled_name(tmp_path):
    mod = util.build_ir_mod(tmp_path, "fn id[T](x: T) T { return x; }")
    fn = _get_generic_fn(mod, "id")

    inst = fn.instantiate((typs.I32,))
    assert inst.name == "id[i32]"
    assert inst.qualified_name == "main::id[i32]"


def test_fn_instance_mangled_name_with_multiple_typ_args(tmp_path):
    mod = util.build_ir_mod(tmp_path, "fn pair_first[T, U](x: T, y: U) T { return x; }")
    fn = _get_generic_fn(mod, "pair_first")

    inst = fn.instantiate((typs.I32, typs.BOOL))
    assert inst.name == "pair_first[i32, bool]"


def test_fn_instance_body_lowers(tmp_path):
    # The body's sole statement is a bare `return x;`, with no tail
    # expression - so the block's own type is `never`, coerced to the
    # declared return type T. Lowering that coercion has to substitute
    # the instance's concrete type argument for T rather than leaving a
    # raw type parameter behind for codegen to choke on.
    mod = util.build_ir_mod(tmp_path, "fn id[T](x: T) T { return x; }")
    fn = _get_generic_fn(mod, "id")

    _ = fn.instantiate((typs.I32,)).cfg


def test_fn_instance_body_lowers_for_multiple_typ_args(tmp_path):
    mod = util.build_ir_mod(tmp_path, "fn pair_first[T, U](x: T, y: U) T { return x; }")
    fn = _get_generic_fn(mod, "pair_first")

    _ = fn.instantiate((typs.I32, typs.BOOL)).cfg


def test_fn_instance_recursive_call_lowers(tmp_path):
    # depth's own recursive call binds its type parameter to itself (the
    # argument x has type T, depth's own type parameter), so the type
    # argument TypCheck records for that call site is itself a type
    # parameter, not a concrete type - lowering has to resolve that
    # against the instance actually being built before asking for it.
    mod = util.build_ir_mod(
        tmp_path,
        """
        fn depth[T](x: T, n: i32) T {
            if (n <= 0) {
                return x;
            };
            return depth(x, n - 1);
        }
        """,
    )
    fn = _get_generic_fn(mod, "depth")

    _ = fn.instantiate((typs.I32,)).cfg


def test_mono_discovers_requests_appended_while_lowering_generic_fn(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        """
        fn inner[T](x: T) T { x }
        fn outer[T](x: T) T {
            __size_of[T]();
            return inner(x);
        }
        """,
    )
    outer = _get_generic_fn(mod, "outer")
    outer.instantiate((typs.I32,))

    result = mono.discover(mod)
    names = {inst.qualified_name for inst in result.fn_instances}

    assert names == {"main::outer[i32]", "main::inner[i32]", "__size_of[i32]"}


def test_mono_discovers_recursive_instance_once(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        """
        fn depth[T](x: T, n: i32) T {
            if (n == 0) { return x; };
            return depth(x, n - 1);
        }
        """,
    )
    depth = _get_generic_fn(mod, "depth")
    instance = depth.instantiate((typs.I32,))

    result = mono.discover(mod)

    assert result.fn_instances.count(instance) == 1


def test_calling_generic_fn_lowers_to_a_fn_ref(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        let n: i32 = 5;
        return id(n) - 5;
    }
    """
    cfg = _lower_main(tmp_path, src)

    # Filtered to the generic instance under test specifically: `- 5`'s
    # compiler-synthesized overflow check also references the imported
    # non-generic `panic` function.
    instance_calls = []
    for bb in cfg.nodes:
        for instr in bb.instrs:
            if (
                isinstance(instr, ir_values.CallInstr)
                and isinstance(instr.callee, ir_module.FnRef)
                and instr.callee.instance.name == "id[i32]"
            ):
                instance_calls.append(instr)
    (call,) = instance_calls
    callee = asserts.checked_cast(call.callee, ir_module.FnRef)
    assert callee.instance.name == "id[i32]"


def test_source_function_call_kinds_lower_to_fn_refs(tmp_path):
    util.write_whole_file(
        tmp_path / "helper.leech",
        "pub fn imported() i32 { 0 }",
    )
    cfg = _lower_main(
        tmp_path,
        """
        import helper;
        trait Show { fn show(*self) i32; }
        struct Foo {}
        impl Foo { fn inherent(*self) i32 { 0 } }
        impl Show for Foo { fn show(*self) i32 { 0 } }
        extern fn extern_fn(x: i32) i32;
        fn ordinary() i32 { 0 }

        pub fn main() i32 {
            let foo = Foo {};
            ordinary();
            __size_of[i32]();
            foo.inherent();
            foo.show();
            helper::imported();
            extern_fn(0);
            return 0;
        }
        """,
    )

    callees = {}
    for bb in cfg.nodes:
        for instr in bb.instrs:
            if not isinstance(instr, ir_values.CallInstr):
                continue
            if isinstance(instr.callee, ir_module.FnRef):
                callees[instr.callee.instance.name] = instr.callee

    expected_names = {
        "ordinary",
        "__size_of[i32]",
        "inherent",
        "show",
        "imported",
        "extern_fn",
    }
    assert expected_names <= callees.keys()
    assert all(isinstance(callees[name], ir_module.FnRef) for name in expected_names)


def test_value_param_declaration_typechecks(tmp_path):
    src = """
    fn f[value N: usize]() usize { return N; }
    pub fn main() i32 { return 0; }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_calling_generic_fn_with_explicit_value_arg(tmp_path):
    src = """
    fn f[value N: i32]() i32 { return N; }
    pub fn main() i32 {
        return f[4]() - 4;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_calling_generic_fn_with_explicit_bool_arg(tmp_path):
    src = """
    fn f[value B: bool]() bool { return B; }
    pub fn main() i32 {
        if (f[true]()) { return 0; };
        return 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_value_and_typ_params_coexist(tmp_path):
    src = """
    fn f[T, value N: i32](x: T) i32 { return N; }
    pub fn main() i32 {
        return f[i32, 3](9) - 3;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_equal_value_args_share_one_instance(tmp_path):
    src = """
    fn f[value N: i32]() i32 { return N; }
    pub fn main() i32 {
        return f[4]() - f[4]();
    }
    """
    ir_text = util.compile_str(tmp_path, src).read_text()
    assert ir_text.count('define linkonce_odr i32 @"main::f[4]"') == 1


def test_distinct_value_args_get_distinct_instances(tmp_path):
    src = """
    fn f[value N: i32]() i32 { return N; }
    pub fn main() i32 {
        return f[4]() - f[5]() + 1;
    }
    """
    ir_text = util.compile_str(tmp_path, src).read_text()
    assert 'define linkonce_odr i32 @"main::f[4]"' in ir_text
    assert 'define linkonce_odr i32 @"main::f[5]"' in ir_text


def test_value_param_with_unsupported_typ_is_rejected(tmp_path):
    src = """
    struct Foo {}
    fn f[value N: Foo]() {}
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InvalidValueParamTypError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Foo"' in str(exc_info.value)


def test_value_param_declared_as_ptr_typ_is_rejected(tmp_path):
    src = """
    fn f[value N: *usize]() {}
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InvalidValueParamTypError):
        util.compile_str(tmp_path, src)


def test_unmarked_value_param_is_a_typ_param(tmp_path):
    # Without the "value" marker this declares a type parameter bound by
    # "usize", whatever "usize" turns out to name - no lookup decides the
    # parameter's kind.
    src = """
    fn f[N: usize]() {}
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.PathTargetKindError, match="names a type, not a trait"):
        util.compile_str(tmp_path, src)


def test_typ_arg_given_for_value_param_is_rejected(tmp_path):
    src = """
    fn f[value N: usize]() usize { return N; }
    pub fn main() i32 {
        let _ = f[i32]();
        return 0;
    }
    """
    with pytest.raises(errors.PathTargetKindError, match="names a type, not a comptime value"):
        util.compile_str(tmp_path, src)


def test_value_arg_given_for_typ_param_is_rejected(tmp_path):
    src = """
    fn f[T](x: T) T { return x; }
    pub fn main() i32 {
        let _ = f[4](9);
        return 0;
    }
    """
    with pytest.raises(errors.WrongKindOfComptimeArgError):
        util.compile_str(tmp_path, src)


def test_wrong_typ_value_arg_is_rejected(tmp_path):
    src = """
    fn f[value N: usize]() usize { return N; }
    pub fn main() i32 {
        let _ = f[true]();
        return 0;
    }
    """
    with pytest.raises(errors.WrongComptimeValueTypError):
        util.compile_str(tmp_path, src)


def test_wrong_value_arg_on_bare_generic_fn_reference_is_rejected(tmp_path):
    src = """
    fn f[value N: usize]() usize { return N; }
    pub fn main() i32 {
        let g = f[true];
        return 0;
    }
    """
    with pytest.raises(errors.WrongComptimeValueTypError):
        util.compile_str(tmp_path, src)


def test_typ_arg_on_bare_generic_value_param_fn_reference_is_rejected(tmp_path):
    src = """
    fn f[value N: usize]() usize { return N; }
    pub fn main() i32 {
        let g = f[i32];
        return 0;
    }
    """
    with pytest.raises(errors.PathTargetKindError, match="names a type, not a comptime value"):
        util.compile_str(tmp_path, src)


def test_value_param_used_as_param_typ_is_rejected(tmp_path):
    src = """
    fn f[value N: usize](x: N) usize { return N; }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ValueUsedAsTypError):
        util.compile_str(tmp_path, src)


def test_value_param_used_as_let_typ_is_rejected(tmp_path):
    src = """
    fn f[value N: usize]() usize {
        let x: N = 5;
        return x;
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ValueUsedAsTypError):
        util.compile_str(tmp_path, src)


def test_value_param_used_as_ptr_pointee_typ_is_rejected(tmp_path):
    src = """
    fn f[value N: usize](x: *N) i32 { return 0; }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ValueUsedAsTypError):
        util.compile_str(tmp_path, src)


def test_out_of_range_value_arg_is_rejected(tmp_path):
    src = """
    fn f[value N: u8]() u8 { return N; }
    pub fn main() i32 {
        let _ = f[300]();
        return 0;
    }
    """
    with pytest.raises(errors.IntLitOverflowError):
        util.compile_str(tmp_path, src)


def test_int_lit_against_bool_value_param_is_rejected(tmp_path):
    src = """
    fn f[value B: bool]() bool { return B; }
    pub fn main() i32 {
        let _ = f[4]();
        return 0;
    }
    """
    with pytest.raises(errors.WrongKindOfComptimeArgError):
        util.compile_str(tmp_path, src)


def test_value_param_inferred_from_array_arg(tmp_path):
    src = """
    fn first[T, value N: usize](x: array[T, N]) T { return x.[0]; }
    pub fn main() i32 {
        return first(array[i32, 3]{7, 8, 9}) - 7;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_value_param_inference_distinguishes_array_lengths(tmp_path):
    # N must stay usize here (array lengths are always usize), so the
    # result is compared rather than turned into main's i32 return value -
    # there's no int-to-int cast operator in this language.
    src = """
    fn len_times_2[T, value N: usize](x: array[T, N]) usize { return N * 2; }
    pub fn main() i32 {
        if (len_times_2(array[i32, 3]{1, 2, 3}) == 6) { return 0; };
        return 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_first_from_generic_struct_with_value_param(tmp_path):
    src = """
    struct Buf[T, value N: usize] {
        data: array[T, N],
    }

    fn first[T, value N: usize](b: *Buf[T, N]) T {
        return b.*.data.[0];
    }

    pub fn main() i32 {
        let buf = Buf[i32, 4] { data: array[i32, 4]{11, 22, 33, 44} };
        return first(&buf) - 11;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)
