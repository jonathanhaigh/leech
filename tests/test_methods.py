import pytest
import util

from leech import errors


def test_const_ptr_method_call(tmp_path):
    src = """
    struct Counter { n: i32 }
    impl Counter {
        fn get(*self) i32 { self.*.n }
    }
    pub fn main() i32 {
        let c = Counter { n: 42 };
        return c.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_mut_ptr_method_call(tmp_path):
    src = """
    struct Counter { mut n: i32 }
    impl Counter {
        fn inc(*mut self) { self.*.n = self.*.n + 1; }
    }
    pub fn main() i32 {
        let mut c = Counter { n: 0 };
        c.inc();
        c.inc();
        return c.n;
    }
    """
    util.check_prog_output(tmp_path, src, "", 2)


def test_mut_ptr_method_call_on_const_place_rejected(tmp_path):
    # Calling a *mut self method requires a place that can be written
    # through; a non-`mut` local can't provide one.
    src = """
    struct Counter { mut n: i32 }
    impl Counter {
        fn inc(*mut self) { self.*.n = self.*.n + 1; }
    }
    pub fn main() i32 {
        let c = Counter { n: 0 };
        c.inc();
        return 0;
    }
    """
    with pytest.raises(errors.InvalidArgTypError):
        util.compile_str(tmp_path, src)


def test_const_ptr_method_call_on_mut_place(tmp_path):
    # A `let mut` place gives a *mut receiver, which coerces to the *self
    # method's const one - giving up write access is always safe.
    src = """
    struct Counter { n: i32 }
    impl Counter {
        fn get(*self) i32 { self.*.n }
    }
    pub fn main() i32 {
        let mut c = Counter { n: 42 };
        return c.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_dot_call_and_explicit_path_call_equivalent_const_receiver(tmp_path):
    src = """
    struct Counter { n: i32 }
    impl Counter {
        fn get(*self) i32 { self.*.n }
    }
    pub fn main() i32 {
        let c = Counter { n: 42 };
        return c.get() - Counter::get(&c);
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_dot_call_and_explicit_path_call_equivalent_mut_receiver(tmp_path):
    src = """
    struct Counter { mut n: i32 }
    impl Counter {
        fn inc(*mut self) { self.*.n = self.*.n + 1; }
    }
    pub fn main() i32 {
        let mut c = Counter { n: 0 };
        c.inc();
        Counter::inc(&c);
        return c.n;
    }
    """
    util.check_prog_output(tmp_path, src, "", 2)


def test_dot_call_private_method_accessible_within_defining_module(tmp_path):
    src = """
    struct Counter { n: i32 }
    impl Counter {
        fn get(*self) i32 { self.*.n }
    }
    pub fn main() i32 {
        let c = Counter { n: 42 };
        return c.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_dot_call_private_method_cross_module_rejected(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        let c = a::Counter::mk(42);
        return c.get();
    }
    """
    a_src = """
    pub struct Counter { n: i32 }
    impl Counter {
        pub fn mk(n: i32) Counter { Counter { n: n } }
        fn get(*self) i32 { self.*.n }
    }
    """
    with pytest.raises(errors.PrivateItemAccessError):
        util.compile_modules(tmp_path, main=main_src, a=a_src)


def test_self_param_outside_impl_rejected(tmp_path):
    src = """
    pub fn f(*self) i32 { 0 }
    pub fn main() i32 { 0 }
    """
    with pytest.raises(errors.SelfParamOutsideImplError):
        util.compile_str(tmp_path, src)


def test_self_param_on_extern_rejected(tmp_path):
    src = """
    extern fn f(*self) i32;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(errors.SelfParamOutsideImplError):
        util.compile_str(tmp_path, src)


def test_dot_call_on_receiverless_assoc_fn_rejected(tmp_path):
    # `new` is a real associated function of Counter, but has no `self`
    # receiver, so it can't be dot-called - only `Counter::new()`.
    src = """
    struct Counter { n: i32 }
    impl Counter {
        fn new() Counter { Counter { n: 0 } }
    }
    pub fn main() i32 {
        let c = Counter::new();
        return c.new();
    }
    """
    with pytest.raises(errors.NotAMethodError):
        util.compile_str(tmp_path, src)


def test_field_access_of_non_method_name_still_works(tmp_path):
    # Non-call field access (no trailing `(...)`) is untouched by method
    # resolution - it never goes through _build_call_expr at all.
    src = """
    struct Counter { n: i32 }
    impl Counter {
        fn get(*self) i32 { self.*.n }
    }
    pub fn main() i32 {
        let c = Counter { n: 7 };
        return c.n;
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_dot_call_falls_back_to_field_access_when_no_method_matches(tmp_path):
    # No method named "n" exists, so `h.n()` falls back to ordinary field
    # access (per test_field_access_of_non_method_name_still_works) and
    # then fails because the field's value (an i32) isn't callable -
    # exercising the "no method found" branch of the fallback, distinct
    # from the "found, but receiverless" case NotAMethodError covers.
    src = """
    struct Holder { n: i32 }
    pub fn main() i32 {
        let h = Holder { n: 5 };
        return h.n();
    }
    """
    with pytest.raises(errors.NotCallableError):
        util.compile_str(tmp_path, src)
