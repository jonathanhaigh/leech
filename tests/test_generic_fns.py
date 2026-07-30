import pytest
from util import check_prog_output, compile_str, find_pos

from leech.errors import (
    DerefInvalidTypError,
    FieldAccessIntoInvalidTypError,
    IncompatibleLetTypError,
    IndexIntoInvalidTypError,
    InvalidBinOpArgTypError,
    MissingTypArgsError,
    NotCallableError,
)


def test_generic_fn_body_typechecks_with_identity_only_ops(tmp_path):
    # A generic function's body is checked eagerly, whether or not it's
    # ever called (calling one isn't supported until type-arg inference
    # lands) - so this only has to compile and run, never invoking `id`.
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


def test_calling_generic_fn_requires_typ_args_until_inference_lands(tmp_path):
    # Call-site type-arg inference isn't implemented yet, so a call is
    # rejected the same as any other bare reference to a generic function.
    src = """
    fn id[T](x: T) T { return x; }
    pub fn main() i32 {
        id(5);
        return 0;
    }
    """
    with pytest.raises(MissingTypArgsError) as exc_info:
        compile_str(tmp_path, src)
    assert '"id"' in str(exc_info.value)


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
