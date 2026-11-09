<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Basics

After this chapter you can write functions over integers and booleans, bind and update
variables, and predict when Leech converts a value from one type to another. Each example
is a complete program, to save under the name shown. Run the working examples with
`leech run`. An example of an error is followed by everything `leech check` prints for it,
which [Getting started](../getting-started.md#finding-errors)
explains how to read.

## `main` and the exit status

Every program has a `main` function that takes no parameters and returns an `i32`. Its
return value becomes the process's exit status:

```leech test=exit-status file=answer.leech mode=run exit=42
fn main() i32 {
    return 6 * 7;
}
```

`leech run answer.leech` prints nothing, but `echo $?` afterwards shows `42`. `main` does
not yet receive the program's arguments
([#107](https://github.com/jonathanhaigh/leech/issues/107)), and it must return `i32`
([#110](https://github.com/jonathanhaigh/leech/issues/110)). The standard library cannot
yet format numbers ([#81](https://github.com/jonathanhaigh/leech/issues/81)), so this tour
often reports a result through the exit status.

## Functions

```leech test=functions file=rectangle.leech mode=run
import std::io;

// Returns with an explicit `return` statement.
fn area(width: i32, height: i32) i32 {
    return width * height;
}

// Returns its final expression, which has no semicolon.
fn perimeter(width: i32, height: i32) i32 {
    2 * (width + height)
}

// Returns nothing, so it has no return type.
fn report(message: *u8) {
    io::print("rectangle: ");
    io::println(message);
}

fn main() i32 {
    let width = 3;
    let height = 4;
    if (area(width, height) == 12 and perimeter(width, height) == 14) {
        report("area 12, perimeter 14");
    }
    return 0;
}
```

```text output=functions
rectangle: area 12, perimeter 14
```

A function lists its parameters with their types, then its return type, if any. Its body is
a block. A block's value is its final expression, when that expression has no semicolon,
so `perimeter` needs no `return`. `report` returns nothing and has no return type. A string
literal such as `"area 12, perimeter 14"` has type `*u8`, a pointer to NUL-terminated bytes.

`import std::io;` makes the standard library's `io` module available, and `io::print`
writes a string without a newline. Functions may be declared in any order, and comments
start with `//`.

## Integers and booleans

Integer types are written `i` (signed) or `u` (unsigned) followed by a width in bits. The
usual widths are `i8` to `i64` and `u8` to `u64`, but other widths, such as `u17` or
`i128`, work too. `isize` and `usize` have the width of a pointer, and are used for sizes
and array indexes. `bool` has the values `true` and `false`.

An integer literal may end with its type, as in `200u8` or `0usize`. Without a suffix, a
literal takes the type its context expects: in `let limit: u8 = 200;`, `200` is a `u8`.
When nothing decides, a literal is an `i32`. A literal that does not fit its type is an
error, and there are no hexadecimal or binary literals yet
([#77](https://github.com/jonathanhaigh/leech/issues/77)).

The arithmetic operators are `+`, `-`, `*` and `/`, and unary `-` for signed integers.
Division truncates toward zero, and there is no remainder operator. The comparisons `<`,
`<=`, `==`, `!=`, `>=` and `>` compare two integers and cannot be chained. `and`, `or` and
`not` combine booleans, and `and` and `or` evaluate their right operand only when they
need it. `==` and `!=` work only on integers for now
([#15](https://github.com/jonathanhaigh/leech/issues/15)); test a boolean directly, or
with `not`.

The operands of an arithmetic operator or comparison must have the same type. Leech never
converts one to match the other, even when that would be safe:

```leech test=operand-types file=operands.leech mode=error error=conflicting-operand-types
fn main() i32 {
    let small = 1i8;
    let large = 2i16;
    let sum = small + large;
    return 0;
}
```

`leech check operands.leech` reports:

```text diagnostic=operand-types
ERROR: left and right operands of "+" have incompatible types
4|     let sum = small + large;
-----------------------^
NOTE: left operand has type "i8"
4|     let sum = small + large;
-----------------^
NOTE: right operand has type "i16"
4|     let sum = small + large;
-------------------------^
```

## Variables

`let` binds a name to a value. The binding cannot be assigned to unless it is declared with
`let mut`. A type annotation is optional when the value's type is clear:

```leech test=variables file=count.leech mode=run exit=10
fn main() i32 {
    let limit: u8 = 10;
    let mut count = 0;
    let mut i = 0u8;
    while (i < limit) {
        count = count + 1;
        i = i + 1;
    }
    return count;
}
```

Assigning to a binding without `mut` is an error:

```leech test=immutable file=immutable.leech mode=error error=assignment-to-immutable-place
fn main() i32 {
    let count = 0;
    count = count + 1;
    return count;
}
```

`leech check immutable.leech` reports:

```text diagnostic=immutable
ERROR: cannot assign to an immutable place
3|     count = count + 1;
-------^
```

A name declared in an inner block may hide one from an outer block, but redeclaring a name
in the same block is an error
([#22](https://github.com/jonathanhaigh/leech/issues/22)).

## Conversions

Leech converts a value implicitly only where it is going somewhere with a declared type:
an argument to a parameter, a returned value to the return type, a value to an annotated
`let` or assigned variable, and a field or element to its declared type. At those points
an integer may *widen* to a type that holds every value of its own type: a narrower
integer of the same signedness, or an unsigned integer to a signed one that is wider.

```leech test=widening file=widen.leech mode=run
fn total(a: i64, b: i64) i64 {
    a + b
}

fn main() i32 {
    let small = 100u8;
    let medium: i32 = -20i16;
    // Both arguments widen to i64.
    let sum = total(small, medium);
    if (sum == 80) {
        return 0;
    }
    return 1;
}
```

Narrowing is never implicit, so a value that might not fit is an error, even when the
particular value would fit. A signed integer never converts to an unsigned one, and
`bool` never converts to or from an integer:

```leech test=narrowing file=half.leech mode=error error=argument-type-mismatch
fn half(n: i32) i32 {
    n / 2
}

fn main() i32 {
    let big = 100i64;
    return half(big);
}
```

`leech check half.leech` reports:

```text diagnostic=narrowing
ERROR: argument 1 in call to variable "half" has type "i64", expected "i32"
7|     return half(big);
-------------------^
```

Leech has no cast operator for integers yet. A mutable pointer may also convert to a
read-only one, as [Data and pointers](03-data-and-pointers.md) shows.

Next: [Control flow](02-control-flow.md). Back to the [tour](index.md) or the
[user guide](../index.md).
