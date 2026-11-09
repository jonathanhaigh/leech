<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Control flow

After this chapter you can branch and loop, use blocks and `if` as expressions, declare
enumerations and tagged unions, and take them apart with `match`. You will also know how
Leech treats code that never finishes, such as a call to `panic`.

## Blocks and `if`

A block is a sequence of statements in braces, optionally ending with an expression that
has no semicolon. That final expression is the block's value. `if` is an expression too:
when both branches produce values of the same type, the `if` produces one of them.

```leech test=if-expressions file=classify.leech mode=run
import std::io;

fn classify(n: i32) *u8 {
    if (n < 0) {
        "negative"
    } else {
        if (n == 0) { "zero" } else { "positive" }
    }
}

fn main() i32 {
    io::println(classify(-5));
    io::println(classify(0));
    let doubled = {
        let half = 21;
        half * 2
    };
    io::println(classify(doubled));
    return 0;
}
```

```text output=if-expressions
negative
zero
positive
```

The condition is always in parentheses and the branches are always blocks. There is no
`else if` yet ([#31](https://github.com/jonathanhaigh/leech/issues/31)), so a second test
goes in a block, as in `classify`. An `if` without `else` produces no value, and is used
as a statement.

A statement that ends with a block, such as an `if`, `while` or `match` statement, needs
no semicolon after it.

## Loops

`while` repeats its body while its condition is true. `break` leaves the loop and
`continue` starts its next iteration. A loop may have a label, `name:`, so that `break`
or `continue` in a nested loop can name it:

```leech test=labeled-loops file=factors.leech mode=run exit=49
// Finds factors a <= b of 36 whose sum is 13, and returns them as 10 * a + b.
fn main() i32 {
    let mut found = 0;
    let mut a = 1;
    search: while (a <= 36) {
        let mut b = a;
        while (b <= 36) {
            if (a * b > 36) {
                break;
            }
            if (a * b == 36 and a + b == 13) {
                found = 10 * a + b;
                break search;
            }
            b = b + 1;
        }
        a = a + 1;
    }
    return found;
}
```

The plain `break` leaves the inner loop; `break search;` leaves both. In the same way,
`continue numbers;` below abandons the search for divisors and starts the outer loop's next
iteration, skipping `primes = primes + 1;`:

```leech test=labeled-continue file=primes.leech mode=run exit=10
// Counts the primes below 30.
fn main() i32 {
    let mut primes = 0;
    let mut n = 1;
    numbers: while (n < 29) {
        n = n + 1;
        let mut d = 1;
        while (true) {
            d = d + 1;
            if (d * d > n) {
                break;
            }
            if (n / d * d == n) {
                // n has a divisor, so it is not prime.
                continue numbers;
            }
        }
        primes = primes + 1;
    }
    return primes;
}
```

There is no `for` loop yet ([#35](https://github.com/jonathanhaigh/leech/issues/35)).

## Code that never finishes

The statements `return`, `break` and `continue` move control elsewhere, so a block that
always reaches one of them never produces a value. Neither does a call to a function whose
return type is `never`, such as the prelude's `panic`, which every module can call without
an import: it prints its message to standard error and aborts the program. A call or block
of type `never` fits wherever any type is expected, so one branch of an `if` may `panic`
while the other produces a value:

```leech test=panic file=divide.leech mode=run exit=SIGABRT
fn divide(a: i32, b: i32) i32 {
    if (b == 0) {
        panic("division by zero")
    } else {
        a / b
    }
}

fn main() i32 {
    assert(divide(10, 2) == 5);
    return divide(1, 0);
}
```

```text output=panic
division by zero
```

The prelude's `assert` panics with the message `assertion failed` when its condition is
false. A function can declare the return type `never` itself, but then it must not return.

## Enumerations

An `enum` is a type with a fixed set of named values, like a C enumeration. Each value is
backed by an integer, numbered from zero unless given explicitly, and the backing type is
chosen to fit unless it is written after the name:

```leech test=enums file=cards.leech mode=run exit=2
enum Suit { Clubs, Diamonds, Hearts, Spades }

enum Status(u16) {
    Ok = 200,
    NotFound = 404,
    Teapot = 418,
}

fn is_red(suit: Suit) bool {
    match (suit) {
        Suit::Diamonds | Suit::Hearts => true,
        Suit::Clubs | Suit::Spades => false,
    }
}

fn main() i32 {
    if (not is_red(Suit::Hearts) or is_red(Suit::Spades)) {
        return 1;
    }
    if (__enum_to_int(Status::NotFound) != 404) {
        return 3;
    }
    return 2;
}
```

A variant is named through its type, as in `Suit::Hearts`. The builtin `__enum_to_int`
reads a value's backing integer. Enums cannot yet be compared with `==`
([#15](https://github.com/jonathanhaigh/leech/issues/15)); use `match`.

## `match`

`match` compares a value with patterns, in order, and evaluates the arm of the first
pattern that matches. Its arms are separated by commas, and like `if`, a `match` produces
a value when its arms do. A pattern is one of:

- a literal, such as `0`, `-1` or `true`;
- an enum or union variant, such as `Suit::Hearts`;
- `let name` or `let mut name`, which matches anything and binds it;
- `_`, which matches anything and binds nothing;
- alternatives separated by `|`, which must not bind names.

```leech test=match-integers file=buckets.leech mode=run exit=40
fn bucket(n: i32) i32 {
    match (n) {
        0 => 1,
        1 | 2 | 3 => 2,
        let other => other * 10,
    }
}

fn main() i32 {
    bucket(4)
}
```

A `match` must be *exhaustive*: some arm must match every possible value. For integers that
usually means a final `_` or `let` arm. A `match` that misses a value is an error, and a
note names a pattern it misses:

```leech test=non-exhaustive file=lights.leech mode=error error=non-exhaustive-match
enum Light { Red, Amber, Green }

fn wait_seconds(light: Light) i32 {
    match (light) {
        Light::Red => 30,
        Light::Amber => 3,
    }
}

fn main() i32 {
    wait_seconds(Light::Green)
}
```

`leech check lights.leech` reports:

```text diagnostic=non-exhaustive
ERROR: "match" is not exhaustive
 4|     match (light) {
--------^
NOTE: uncovered pattern "Light::Green"
```

An arm that can never be reached, because earlier arms match everything it would, is a
warning:

```leech test=unreachable-arm file=unreachable.leech mode=run exit=1 warning=unreachable-match-arm
fn bucket(n: i32) i32 {
    match (n) {
        0 => 0,
        _ => 1,
        1 => 2,
    }
}

fn main() i32 {
    bucket(1)
}
```

`leech run unreachable.leech` prints this warning, then runs the program, which
exits with status 1:

```text diagnostic=unreachable-arm
WARNING: unreachable match arm
 5|         1 => 2,
------------^
```

Patterns cannot yet have guards, ranges or struct fields
([#62](https://github.com/jonathanhaigh/leech/issues/62),
[#63](https://github.com/jonathanhaigh/leech/issues/63),
[#65](https://github.com/jonathanhaigh/leech/issues/65)).

## Tagged unions

A `union` is a tagged union: a value is one of its variants, and a variant may carry values
of its own, its *payload*. A union may be generic, written with type parameters in square
brackets:

```leech test=unions file=commands.leech mode=run exit=7
import std::io;

union Command {
    Quit,
    Move(i32, i32),
    Say(*u8),
}

union Maybe[T] {
    Nothing,
    Just(T),
}

fn first_move(commands: array[Command, 3]) Maybe[i32] {
    let mut i = 0usize;
    while (i < 3) {
        match (commands.[i]) {
            Command::Move(let x, let y) => { return Maybe::Just(x + y); },
            Command::Say(let text) => io::println(text),
            Command::Quit => { return Maybe::Nothing; },
        }
        i = i + 1;
    }
    Maybe::Nothing
}

fn main() i32 {
    let commands = array[Command, 3]{
        Command::Say("hello"),
        Command::Move(3, 4),
        Command::Quit,
    };
    match (first_move(commands)) {
        Maybe::Just(let distance) => distance,
        Maybe::Nothing => 0,
    }
}
```

```text output=unions
hello
```

`Command::Move(3, 4)` builds a variant with its payload, and the pattern
`Command::Move(let x, let y)` binds the payload's values. The type arguments of a generic
union's variant are usually inferred: `Maybe::Just(x + y)` is a `Maybe[i32]`, and in
`first_move`, `Maybe::Nothing` takes its type from the return type. An arm whose body is a
block, such as `{ return Maybe::Nothing; }`, still needs a comma after it
([#61](https://github.com/jonathanhaigh/leech/issues/61)). The arrays in this example are
the subject of the [next chapter](03-data-and-pointers.md).

The prelude does not yet provide `Option` or `Result`
([#88](https://github.com/jonathanhaigh/leech/issues/88)), so programs declare unions like
`Maybe` themselves.

Next: [Data and pointers](03-data-and-pointers.md). Back to the [tour](index.md) or the
[user guide](../index.md).
