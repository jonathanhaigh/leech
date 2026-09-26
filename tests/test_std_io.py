# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0


def test_println_adds_a_newline(compiler):
    main_src = """
    import std::io;
    pub fn main() i32 {
        io::println("hello");
        return 0;
    }
    """
    compiler.check(main_src, stdout="hello\n")


def test_print_does_not_add_a_newline(compiler):
    main_src = """
    import std::io;
    pub fn main() i32 {
        io::print("hello");
        return 0;
    }
    """
    compiler.check(main_src, stdout="hello")


def test_print_and_println_compose_across_multiple_calls(compiler):
    main_src = """
    import std::io;
    pub fn main() i32 {
        io::print("a");
        io::print("b");
        io::println("c");
        io::print("d");
        io::println("e");
        return 0;
    }
    """
    compiler.check(main_src, stdout="abc\nde\n")
