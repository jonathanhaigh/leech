<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Leech at a glance

Leech is a small, statically typed language that compiles to native x86-64 Linux
executables through LLVM. This page compares it with languages you may already know, so
you can tell which of your habits carry over. The [language tour](tour/index.md) then
teaches each feature with complete programs.

```leech test=glance file=shapes.leech mode=run
import std::io;

union Shape {
    Square(i32),
    Rect(i32, i32),
}

trait Area {
    fn area(*self) i32;
}

impl Area for Shape {
    fn area(*self) i32 {
        match (self.*) {
            Shape::Square(let side) => side * side,
            Shape::Rect(let width, let height) => width * height,
        }
    }
}

fn total_area[T: Area, value N: usize](shapes: *array[T, N]) i32 {
    let mut total = 0;
    let mut i = 0usize;
    while (i < N) {
        total = total + shapes.*.[i].area();
        i = i + 1;
    }
    total
}

fn main() i32 {
    let shapes = array[Shape, 3]{Shape::Square(3), Shape::Rect(2, 5), Shape::Square(1)};
    if (total_area(&shapes) == 20) {
        io::println("total area 20");
    }
    return 0;
}
```

```text output=glance
total area 20
```

## Compared with Rust

The surface will look familiar to Rust programmers: `fn`, `let` and `let mut`, `struct`,
`trait`, `impl`, `match`, `pub`, `::` paths, and blocks whose last expression is their
value. Generic parameters are written in square brackets, `fn total_area[T: Area]`, and
generic code is type-checked against its bounds before it is instantiated, as in Rust.

The resemblance stops at memory. Leech has no ownership, borrowing or lifetimes: `*T` and
`*mut T` are raw pointers, nothing stops a pointer outliving what it points to, and values
have no destructors. How Leech should handle ownership is still an open question
([#34](https://github.com/jonathanhaigh/leech/issues/34)).

## Compared with Zig

Like Zig, Leech evaluates code at compile time: module-level `let` initializers run in the
compiler, calling ordinary Leech functions. Generics take compile-time values as well as
types, such as the array length `value N: usize` above. Integer types can have unusual
widths, such as `u17` or `i128`, and compiler builtins have a reserved prefix:
`__size_of[T]()` where Zig writes `@sizeOf(T)`.

## Compared with C and C++

Leech links with the C toolchain. `extern fn` declares a C function, and the C compiler
links the program against the C library; the standard library's printing and allocation
are thin wrappers over `write`, `malloc` and `free`. A string literal is a pointer to
NUL-terminated bytes, `*u8`. There is no C++ interoperability, and generics are not
templates: they are checked once, not at each instantiation.

## Compared with Swift

Leech's `union` is a tagged union, like a Swift `enum` whose cases carry associated
values: `Shape::Rect(2, 5)` builds one, and `match` takes it apart. Leech keeps `enum` for
C-style enumerations backed by integers.

## What Leech does not have yet

Leech is young, and many things you would expect are missing. There are no floating-point
types ([#76](https://github.com/jonathanhaigh/leech/issues/76)), slices
([#26](https://github.com/jonathanhaigh/leech/issues/26)), closures
([#36](https://github.com/jonathanhaigh/leech/issues/36)) or `for` loops
([#35](https://github.com/jonathanhaigh/leech/issues/35)). The standard library is small:
it prints strings, allocates memory and aborts, but it cannot yet format a number
([#81](https://github.com/jonathanhaigh/leech/issues/81)), so the tour often reports results
through a program's exit status. Leech supports only x86-64 Linux.

Next: [Basics](tour/01-basics.md), or return to the [user guide](index.md).
