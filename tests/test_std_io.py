# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import util


def test_println_adds_a_newline(tmp_path):
    main_src = """
    import std::io;
    pub fn main() i32 {
        io::println("hello");
        return 0;
    }
    """
    util.check_prog_output(tmp_path, main_src, "hello\n", 0, std_modules=("io",))


def test_print_does_not_add_a_newline(tmp_path):
    main_src = """
    import std::io;
    pub fn main() i32 {
        io::print("hello");
        return 0;
    }
    """
    util.check_prog_output(tmp_path, main_src, "hello", 0, std_modules=("io",))


def test_print_and_println_compose_across_multiple_calls(tmp_path):
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
    util.check_prog_output(tmp_path, main_src, "abc\nde\n", 0, std_modules=("io",))
