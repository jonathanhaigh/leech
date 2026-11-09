<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Generics, traits and compile-time evaluation

After this chapter you can write functions and types that work for many types and array
lengths, describe shared behavior with traits, compute values while the program compiles,
and use the compiler's builtin functions.

## Generic functions and types

A function, struct or union may take *compile-time parameters* in square brackets after its
name. A type parameter, such as `T`, stands for any type; a value parameter, written
`value N: usize`, stands for a constant of the given type. Value parameters may be integers
or `bool`, and most often give an array's length:

```leech test=generics file=ring.leech mode=run exit=24
struct Pair[T] {
    first: T,
    second: T,
}

fn swap[T](pair: Pair[T]) Pair[T] {
    Pair[T] { first: pair.second, second: pair.first }
}

struct Ring[T, value N: usize] {
    mut items: array[T, N],
    mut next: usize,
}

impl[T, value N: usize] Ring[T, N] {
    fn push(*mut self, item: T) {
        self.*.items.[self.*.next] = item;
        self.*.next = self.*.next + 1;
        if (self.*.next == N) {
            self.*.next = 0;
        }
    }
}

fn main() i32 {
    let pair = swap(Pair[i32] { first: 1, second: 2 });
    let wide = swap[i64](Pair[i64] { first: 1, second: 2 });

    let mut ring = Ring[u8, 3] { items: array[u8, 3]{0, 0, 0}, next: 0 };
    ring.push(1);
    ring.push(2);
    ring.push(3);
    ring.push(4);
    let oldest: i32 = ring.items.[0];
    pair.first * 10 + oldest
}
```

A call usually infers a generic function's arguments from the values passed to it, as in
`swap(...)`; `swap[i64](...)` gives them explicitly. A struct expression always names its
type's arguments, as in `Pair[i32] { ... }`. An `impl` block for a generic type declares the
parameters it uses, `impl[T, value N: usize] Ring[T, N]`, and its methods can use them,
such as `N` in `push`.

The compiler checks a generic function's body once, for every possible argument, before
using it. In `swap`, the body may copy `T` values and pass them around, but not add them:
nothing says that `T` supports `+`. A trait bound gives a type parameter abilities. Each
set of arguments a program uses then gets its own compiled copy of the function, as if it
had been written for those types.

Methods and other functions inside an `impl` block cannot yet have compile-time
parameters of their own ([#1](https://github.com/jonathanhaigh/leech/issues/1)).

## Traits

A `trait` lists method signatures, and an `impl Trait for Type` block implements them for a
type. A module can implement its own traits for any type, including builtin types such as
`i32`, and any trait for its own types. A bound such as `[T: Describe + Score]` limits a type parameter to types
implementing those traits, and lets the function call their methods:

```leech test=traits file=scores.leech mode=run exit=27
import std::io;

trait Describe {
    fn describe(*self) *u8;
}

trait Score {
    fn score(*self) i32;
}

enum Coin { Penny, Nickel, Dime }

struct Card {
    rank: i32,
}

impl Describe for Coin {
    fn describe(*self) *u8 {
        match (self.*) {
            Coin::Penny => "penny",
            Coin::Nickel => "nickel",
            Coin::Dime => "dime",
        }
    }
}

impl Score for Coin {
    fn score(*self) i32 {
        match (self.*) {
            Coin::Penny => 1,
            Coin::Nickel => 5,
            Coin::Dime => 10,
        }
    }
}

impl Describe for Card {
    fn describe(*self) *u8 {
        "card"
    }
}

impl Score for Card {
    fn score(*self) i32 {
        self.*.rank
    }
}

impl Score for i32 {
    fn score(*self) i32 {
        self.*
    }
}

fn announce[T: Describe + Score](item: T) i32 {
    io::print("scoring a ");
    io::println(item.describe());
    item.score()
}

struct Pair[T] {
    first: T,
    second: T,
}

impl[T: Score] Score for Pair[T] {
    fn score(*self) i32 {
        self.*.first.score() + self.*.second.score()
    }
}

fn main() i32 {
    let total = announce(Coin::Dime) + announce(Card { rank: 7 });
    let pairs = Pair[Pair[i32]] {
        first: Pair[i32] { first: 1, second: 2 },
        second: Pair[i32] { first: 3, second: 4 },
    };
    total + pairs.score()
}
```

```text output=traits
scoring a dime
scoring a card
```

The last `impl` is generic: every `Pair[T]` whose `T` has a score has one too, so
`Pair[Pair[i32]]` does. Calling `announce` with a type that lacks one of its traits is an
error at the call:

```leech test=unsatisfied-bound file=bounds.leech mode=error error=unsatisfied-trait-bound
trait Score {
    fn score(*self) i32;
}

impl Score for i32 {
    fn score(*self) i32 {
        self.*
    }
}

fn double_score[T: Score](item: T) i32 {
    item.score() * 2
}

fn main() i32 {
    double_score(true)
}
```

`leech check bounds.leech` reports:

```text diagnostic=unsatisfied-bound
ERROR: type "bool" does not implement trait "Score", required by bound on type parameter "T"
16|     double_score(true)
--------^
```

Trait methods are resolved when the program compiles; there are no trait objects or
dynamic dispatch ([#17](https://github.com/jonathanhaigh/leech/issues/17)). If a type has
two traits' methods of the same name, a call to that name is an error, and there is not yet
a syntax to choose one ([#11](https://github.com/jonathanhaigh/leech/issues/11)). Traits
cannot yet provide default method bodies
([#37](https://github.com/jonathanhaigh/leech/issues/37)) or associated types
([#19](https://github.com/jonathanhaigh/leech/issues/19)).

## Compile-time evaluation

A module-level `let` is evaluated while the program compiles. Its initializer may call
ordinary Leech functions, with loops, arrays and structs, and the program contains only
the result:

```leech test=comptime file=tables.leech mode=run exit=128
fn fibonacci(n: usize) u64 {
    let mut a = 0u64;
    let mut b = 1u64;
    let mut i = 0usize;
    while (i < n) {
        let next = a + b;
        a = b;
        b = next;
        i = i + 1;
    }
    a
}

fn powers_of_two() array[u16, 8] {
    let mut table = array[u16, 8]{0, 0, 0, 0, 0, 0, 0, 0};
    let mut i = 0usize;
    let mut power = 1u16;
    while (i < 8) {
        table.[i] = power;
        power = power * 2;
        i = i + 1;
    }
    table
}

let fib_50 = fibonacci(50);
let powers = powers_of_two();

fn main() i32 {
    if (fib_50 != 12586269025) {
        return 1;
    }
    let largest: i32 = powers.[7];
    largest
}
```

`leech build tables.leech --emit llvm-ir` shows `fib_50` and `powers` as constants in the
output. The same functions can also run when the program does: `main` could call
`fibonacci` directly.

A module-level `let mut` is a global variable that functions can assign to while the
program runs, but its initial value is still computed at compile time. Module-level
initializers may refer to each other in any order, but not in a cycle. Arithmetic at
compile time is checked: an overflow, or a division by zero, stops the compilation.
`fibonacci(100)` does not fit a `u64`:

```leech test=comptime-overflow file=overflow.leech mode=error error=comptime-panic
fn fibonacci(n: usize) u64 {
    let mut a = 0u64;
    let mut b = 1u64;
    let mut i = 0usize;
    while (i < n) {
        let next = a + b;
        a = b;
        b = next;
        i = i + 1;
    }
    a
}

let fib_100 = fibonacci(100);

fn main() i32 {
    return 0;
}
```

`leech check overflow.leech` reports the addition that overflowed, inside `fibonacci`,
and a note gives the reason:

```text diagnostic=comptime-overflow
ERROR: compile-time evaluation panicked
 6|         let next = a + b;
-----------------------^
NOTE: panic message: "integer overflow"
```

Compile-time code cannot
call `extern` functions, allocate from the heap with `std::mem`
([#5](https://github.com/jonathanhaigh/leech/issues/5)) or cast pointers
([#6](https://github.com/jonathanhaigh/leech/issues/6)).

## Compiler builtins

Four functions are built into the compiler. Their names start with `__`, and they are
available in every module without an import:

- `__size_of[T]() usize` is the size of `T` in bytes, as the C compiler would lay it out.
- `__ptr_cast_mut[From, To](p: *mut From) *mut To` reinterprets a mutable pointer as a
  pointer to a different type. It works only when the program runs.
- `__is_null[T](p: *mut T) bool` tests whether a mutable pointer is null.
- `__enum_to_int[E](value: E)` returns an enum value's backing integer, of the enum's
  backing type.

Together they are enough to manage C memory by hand, which is how `std::mem` is written:

```leech test=builtins file=header.leech mode=run exit=9
extern fn malloc(size: usize) *mut u8;
extern fn free(p: *mut u8);

enum Level(u8) {
    Low = 1,
    High = 9,
}

struct Header {
    level: u8,
    length: u32,
}

let header_size = __size_of[Header]();

fn main() i32 {
    if (header_size != 8) {
        return 1;
    }
    let raw = malloc(header_size);
    if (__is_null(raw)) {
        panic("out of memory");
    }
    let header = __ptr_cast_mut[u8, Header](raw);
    header.* = Header { level: __enum_to_int(Level::High), length: 0 };
    let level: i32 = header.*.level;
    free(__ptr_cast_mut[Header, u8](header));
    level
}
```

`Header` is 8 bytes: one for `level`, three of padding, and four for `length`.
`header_size` is computed while compiling, since `__size_of` works at compile time too.
The builtins' type arguments are usually inferred, as in `__is_null(raw)`, but
`__size_of` and `__ptr_cast_mut` need theirs written out.

This is the end of the tour. Return to the [tour](index.md) or the
[user guide](../index.md).
