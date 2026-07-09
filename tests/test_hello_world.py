from util import check_prog_output


def test_hello_world(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts("hello world");
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "hello world\n", 0)


def test_recursive_factorial_fn(tmp_path):
    src = """
    fn fact(n: i32) i32 {
        if (n == 1) {
            return 1;
        };
        return n * fact(n - 1);
    }
    pub fn main() i32 {
        return fact(5);
    }
    """
    check_prog_output(tmp_path, src, "", 120)


def test_comptime_recursive_factorial_fn(tmp_path):
    src = """
    fn fact(n: i32) i32 {
        if (n == 1) {
            return 1;
        };
        return n * fact(n - 1);
    }
    let x = fact(5);
    pub fn main() i32 {
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 120)
