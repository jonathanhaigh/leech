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


def _typecheck_main(tmp_path, src) -> None:
    """Type-check ``main``'s body, without lowering or compiling it.

    Calling a generic function can't be lowered yet (that needs
    monomorphization), so this is how a test confirms a call
    type-checks - including that its type arguments resolve correctly -
    short of actually running it.
    """
    mod = build_ir_mod(tmp_path, src)
    main_item = mod.get_item(Env.Namespace.VARS, "main")
    assert main_item is not None
    main_fn = checked_cast(main_item.value, ir_module.Fn)
    _ = main_fn.typ_check_results


def test_generic_fn_body_typechecks_with_identity_only_ops(tmp_path):
    # A generic function's body is checked eagerly, whether or not it's
    # ever called - so this only has to compile and run, never invoking
    # `id` (calling a generic function can't be lowered yet).
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


def test_calling_generic_fn_infers_typ_args_from_argument(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        let n: i32 = 5;
        return id(n) - 5;
    }
    """
    _typecheck_main(tmp_path, src)


def test_calling_generic_fn_with_explicit_typ_args(tmp_path):
    src = """
    fn id[T](x: T) T { return x; }

    pub fn main() i32 {
        return id[i32](5) - 5;
    }
    """
    _typecheck_main(tmp_path, src)


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
    _typecheck_main(tmp_path, src)


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
