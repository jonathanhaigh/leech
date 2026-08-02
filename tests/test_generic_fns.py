import pytest
from util import build_ir_mod, check_prog_output, compile_str, find_pos

from leech import ir_module
from leech.asserts import checked_cast
from leech.errors import (
    CannotInferTypArgError,
    DerefInvalidTypError,
    FieldAccessIntoInvalidTypError,
    IncompatibleLetTypError,
    IndexIntoInvalidTypError,
    InvalidBinOpArgTypError,
    MissingTypArgsError,
    NotCallableError,
    TypArgsOnNonGenericItemError,
    WrongNumberOfTypArgsError,
)
from leech.ir_env import Env
from leech.ir_values import CallInstr
from leech.typs import BOOL, I32


def _get_fn(mod, name: str) -> ir_module.Fn:
    """Get the plain (non-generic) function ``name`` declares in ``mod``."""
    item = mod.get_item(Env.Namespace.VARS, name)
    assert item is not None
    return checked_cast(item.value, ir_module.Fn)


def _get_generic_fn(mod, name: str) -> ir_module.Fn:
    """Get the generic function ``name`` declares in ``mod``."""
    item = mod.get_item(Env.Namespace.VARS, name)
    assert item is not None
    return checked_cast(item.value, ir_module.GenericFn).fn


def _lower_main(tmp_path, src):
    """Build and lower ``main``'s body, without compiling it to LLVM IR.

    Codegen doesn't emit generic function instances yet, so this is how
    a test confirms a call to one type-checks and lowers correctly -
    including that its type arguments resolve, and that the call's
    callee is the right instance - short of actually running it.

    :return: ``main``'s lowered control-flow graph.
    """
    mod = build_ir_mod(tmp_path, src)
    return _get_fn(mod, "main").cfg


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
    check_prog_output(tmp_path, src, "", 0)


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
    check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_body_rejects_arithmetic_on_typ_param(tmp_path):
    src = """
    fn f[T](x: T) T {
        return x + x;
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(InvalidBinOpArgTypError) as exc_info:
        compile_str(tmp_path, src)
    assert '"T"' in str(exc_info.value)


def test_generic_fn_body_rejects_field_access_on_typ_param(tmp_path):
    src = """
    fn f[T](x: T) i32 {
        return x.field;
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(FieldAccessIntoInvalidTypError) as exc_info:
        compile_str(tmp_path, src)
    assert '"T"' in str(exc_info.value)


def test_generic_fn_body_rejects_indexing_typ_param(tmp_path):
    src = """
    fn f[T](x: T) T {
        return x.[0];
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(IndexIntoInvalidTypError) as exc_info:
        compile_str(tmp_path, src)
    assert '"T"' in str(exc_info.value)


def test_generic_fn_body_rejects_calling_typ_param(tmp_path):
    src = """
    fn f[T](x: T) i32 {
        x();
        return 0;
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(NotCallableError) as exc_info:
        compile_str(tmp_path, src)
    assert '"T"' in str(exc_info.value)


def test_generic_fn_body_rejects_deref_of_typ_param(tmp_path):
    src = """
    fn f[T](x: T) T {
        return x.*;
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(DerefInvalidTypError) as exc_info:
        compile_str(tmp_path, src)
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
    with pytest.raises(IncompatibleLetTypError) as exc_info:
        compile_str(tmp_path, src)
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
    with pytest.raises(MissingTypArgsError) as exc_info:
        compile_str(tmp_path, src)

    assert '"id"' in str(exc_info.value)
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "id;")


def test_address_of_generic_fn_requires_typ_args(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }
    pub fn main() i32 {
        let p = &id;
        return 0;
    }
    """
    with pytest.raises(MissingTypArgsError) as exc_info:
        compile_str(tmp_path, src)
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
    check_prog_output(tmp_path, src, "", 0)


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
    check_prog_output(tmp_path, src, "", 0)


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
    check_prog_output(tmp_path, src, "", 0)


def test_wrong_number_of_explicit_typ_args_on_bare_generic_fn_reference(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }
    pub fn main() i32 {
        let g = id[i32, bool];
        return 0;
    }
    """
    with pytest.raises(WrongNumberOfTypArgsError) as exc_info:
        compile_str(tmp_path, src)
    assert '"id"' in str(exc_info.value)


def test_explicit_typ_args_on_non_generic_fn_reference(tmp_path):
    src = """
    fn f(x: i32) i32 { return x; }
    pub fn main() i32 {
        let g = f[i32];
        return 0;
    }
    """
    with pytest.raises(TypArgsOnNonGenericItemError) as exc_info:
        compile_str(tmp_path, src)
    assert '"f"' in str(exc_info.value)


def test_calling_generic_fn_infers_typ_args_from_argument(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        let n: i32 = 5;
        return id(n) - 5;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_calling_generic_fn_with_explicit_typ_args(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        return id[i32](5) - 5;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


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
    check_prog_output(tmp_path, src, "", 0)


def test_calling_generic_fn_from_another_module(tmp_path):
    a_src = "pub fn id[T](x: T) T { return x; }"
    main_src = """
    import a;

    pub fn main() i32 {
        let n: i32 = 7;
        return a::id(n) - 7;
    }
    """
    check_prog_output(tmp_path, main_src, "", 0, a=a_src)


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
    check_prog_output(tmp_path, main_src, "", 0, a=a_src)


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
    check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_over_pointer_typ_param(tmp_path):
    src = """
    fn deref[T](p: *T) T { return p.*; }

    pub fn main() i32 {
        let n: i32 = 42;
        return deref(&n) - 42;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


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
    check_prog_output(tmp_path, src, "", 0)


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
    check_prog_output(tmp_path, src, "", 0)


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
    check_prog_output(tmp_path, src, "", 0)


def test_mod_var_initializer_calls_generic_fn_with_explicit_typ_args(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    let x = id[i32](5);

    pub fn main() i32 {
        return x - 5;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_mod_var_initializer_calls_generic_fn_with_inferred_typ_args(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    let n: i32 = 5;
    let x = id(n);

    pub fn main() i32 {
        return x - 5;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_mod_var_initializer_stores_generic_fn_instance_as_function_pointer(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    let f = id[i32];

    pub fn main() i32 {
        return f(5) - 5;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


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
    check_prog_output(tmp_path, src, "", 0)


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
    with pytest.raises(CannotInferTypArgError) as exc_info:
        compile_str(tmp_path, src)
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
    with pytest.raises(WrongNumberOfTypArgsError) as exc_info:
        compile_str(tmp_path, src)
    assert '"id"' in str(exc_info.value)


def test_explicit_typ_args_on_non_generic_fn(tmp_path):
    src = """
    fn f(x: i32) i32 { return x; }
    pub fn main() i32 {
        f[i32](5);
        return 0;
    }
    """
    with pytest.raises(TypArgsOnNonGenericItemError) as exc_info:
        compile_str(tmp_path, src)
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
    with pytest.raises(AssertionError):
        compile_str(tmp_path, src)


def test_fn_instance_caches_by_typ_args(tmp_path):
    mod = build_ir_mod(tmp_path, "fn id[T](x: T) T { return x; }")
    fn = _get_generic_fn(mod, "id")

    assert fn.instance((I32,)) is fn.instance((I32,))
    assert fn.instance((I32,)) is not fn.instance((BOOL,))


def test_fn_instance_signature_is_substituted(tmp_path):
    mod = build_ir_mod(tmp_path, "fn id[T](x: T) T { return x; }")
    fn = _get_generic_fn(mod, "id")

    inst = fn.instance((I32,))
    assert inst.fn_typ.param_typs == (I32,)
    assert inst.fn_typ.ret_typ is I32


def test_fn_instance_mangled_name(tmp_path):
    mod = build_ir_mod(tmp_path, "fn id[T](x: T) T { return x; }")
    fn = _get_generic_fn(mod, "id")

    inst = fn.instance((I32,))
    assert inst.name == "id[i32]"
    assert inst.qualified_name == "main.id[i32]"


def test_fn_instance_mangled_name_with_multiple_typ_args(tmp_path):
    mod = build_ir_mod(tmp_path, "fn pair_first[T, U](x: T, y: U) T { return x; }")
    fn = _get_generic_fn(mod, "pair_first")

    inst = fn.instance((I32, BOOL))
    assert inst.name == "pair_first[i32, bool]"


def test_fn_instance_body_lowers(tmp_path):
    # The body's sole statement is a bare `return x;`, with no tail
    # expression - so the block's own type is `never`, coerced to the
    # declared return type T. Lowering that coercion has to substitute
    # the instance's concrete type argument for T rather than leaving a
    # raw type parameter behind for codegen to choke on.
    mod = build_ir_mod(tmp_path, "fn id[T](x: T) T { return x; }")
    fn = _get_generic_fn(mod, "id")

    _ = fn.instance((I32,)).cfg


def test_fn_instance_body_lowers_for_multiple_typ_args(tmp_path):
    mod = build_ir_mod(tmp_path, "fn pair_first[T, U](x: T, y: U) T { return x; }")
    fn = _get_generic_fn(mod, "pair_first")

    _ = fn.instance((I32, BOOL)).cfg


def test_fn_instance_recursive_call_lowers(tmp_path):
    # depth's own recursive call binds its type parameter to itself (the
    # argument x has type T, depth's own type parameter), so the type
    # argument TypCheck records for that call site is itself a type
    # parameter, not a concrete type - lowering has to resolve that
    # against the instance actually being built before asking for it.
    mod = build_ir_mod(
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

    _ = fn.instance((I32,)).cfg


def test_calling_generic_fn_lowers_to_an_instance_call(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        let n: i32 = 5;
        return id(n) - 5;
    }
    """
    cfg = _lower_main(tmp_path, src)

    call_instrs = [instr for bb in cfg.nodes for instr in bb.instrs if isinstance(instr, CallInstr)]
    (call,) = call_instrs
    callee = checked_cast(call.callee, ir_module.FnInstance)
    assert callee.name == "id[i32]"
