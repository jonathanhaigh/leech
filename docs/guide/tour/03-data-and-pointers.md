<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Data and pointers

After this chapter you can group values into structs and arrays, point at them, and give
types methods. You will be able to tell apart the three kinds of mutability Leech has: of a
binding, of a struct field, and of a pointer.

## Structs

A `struct` groups named fields. A struct expression gives a value for every field:

```leech test=structs file=points.leech mode=run exit=7
struct Point {
    x: i32,
    y: i32,
}

fn manhattan(a: Point, b: Point) i32 {
    let dx = if (a.x > b.x) { a.x - b.x } else { b.x - a.x };
    let dy = if (a.y > b.y) { a.y - b.y } else { b.y - a.y };
    dx + dy
}

fn main() i32 {
    let origin = Point { x: 0, y: 0 };
    let target = Point { x: 3, y: -4 };
    manhattan(origin, target)
}
```

Structs, like all Leech values, are copied when they are assigned or passed to a function.

## Mutable bindings and mutable fields

Assigning to a field needs two things: the binding must be `let mut`, and the field must be
declared `mut`. Replacing the whole value needs only the `let mut`:

```leech test=fields file=fields.leech mode=run exit=12
struct Account {
    id: u8,
    mut balance: i32,
}

fn main() i32 {
    let mut account = Account { id: 1, balance: 10 };
    account.balance = account.balance + 5;
    account = Account { id: 2, balance: account.balance - 3 };
    account.balance
}
```

A field without `mut` cannot be assigned, even in a mutable binding:

```leech test=immutable-field file=readonly.leech mode=error error=assignment-to-immutable-place
struct Account {
    id: u8,
    mut balance: i32,
}

fn main() i32 {
    let mut account = Account { id: 1, balance: 10 };
    account.id = 2;
    return 0;
}
```

`leech check readonly.leech` reports:

```text diagnostic=immutable-field
ERROR: cannot assign to an immutable place
 8|     account.id = 2;
--------^
```

## Arrays

`array[T, N]` is an array of `N` values of type `T`. An array expression names its type and
lists its elements, and `.[index]` reads or assigns an element. An index is a `usize`, or
an unsigned integer that widens to one. Arrays are values too, so `copy` below keeps the
squares however `squares` changes later:

```leech test=arrays file=squares.leech mode=run exit=30
fn sum(values: array[i32, 4]) i32 {
    let mut total = 0;
    let mut i = 0usize;
    while (i < 4) {
        total = total + values.[i];
        i = i + 1;
    }
    total
}

fn main() i32 {
    let mut squares = array[i32, 4]{0, 0, 0, 0};
    let mut i = 0u8;
    while (i < 4) {
        let n = i + 1;
        squares.[i] = n * n;
        i = i + 1;
    }
    let copy = squares;
    squares.[0] = 100;
    sum(copy)
}
```

Both conversions in `squares.[i] = n * n;` are widenings: the `u8` index to `usize`, and
the `u8` square to the `i32` element. A signed index such as an `i32` would be an error,
and Leech has no integer casts yet.

Every index is checked when the program runs. An index past the end aborts the program:

```leech test=bounds file=bounds.leech mode=run exit=SIGABRT
fn main() i32 {
    let values = array[i32, 3]{1, 2, 3};
    let mut total = 0;
    let mut i = 0usize;
    while (i <= 3) {
        total = total + values.[i];
        i = i + 1;
    }
    total
}
```

```text output=bounds
index out of bounds
```

An array's length is part of its type. To pass arrays of different lengths to one
function, make the length a parameter, as [Generics, traits and compile-time
evaluation](05-generics-traits-comptime.md) shows. Leech does not have slices yet
([#26](https://github.com/jonathanhaigh/leech/issues/26)).

## Pointers

`*T` is a pointer through which a `T` can be read, and `*mut T` is one through which it can
also be written. `&place` takes the address of a variable, field or element: a `*mut T`
when the place can be assigned, and a `*T` otherwise. `pointer.*` is the value a pointer
points to, so `p.*.x` is a field of a pointed-to struct:

```leech test=pointers file=cursor.leech mode=run exit=8
struct Point {
    mut x: i32,
    mut y: i32,
}

fn sum(p: *Point) i32 {
    p.*.x + p.*.y
}

fn move_right(p: *mut Point, distance: i32) {
    p.*.x = p.*.x + distance;
}

fn main() i32 {
    let mut point = Point { x: 1, y: 2 };
    move_right(&point, 4);

    let cursor = &point;
    cursor.*.y = 10;

    let total = sum(&point);
    let fixed = Point { x: 3, y: 4 };
    total - sum(&fixed)
}
```

`cursor` is not `let mut`, so it cannot be made to point elsewhere, but it holds a
`*mut Point`, so it can write to `point`. A pointer's mutability belongs to its type, not
to the binding that holds it. A `*mut T` converts to a `*T`, which is why `sum(&point)`
compiles; `sum(&fixed)` passes a `*Point` because `fixed` is not mutable. The reverse
conversion is an error, since it would allow writing to a value declared immutable:

```leech test=pointer-mutability file=setter.leech mode=error error=argument-type-mismatch
fn reset(p: *mut i32) {
    p.* = 0;
}

fn main() i32 {
    let count = 5;
    reset(&count);
    return count;
}
```

`leech check setter.leech` reports:

```text diagnostic=pointer-mutability
ERROR: argument 1 in call to variable "reset" has type "*i32", expected "*mut i32"
7|     reset(&count);
-------------^
```

Reaching through a pointer always needs `.*`. A shorter `->` spelling for fields and
methods is planned ([#12](https://github.com/jonathanhaigh/leech/issues/12)).

### What pointers do not check

Leech pointers are not checked the way Rust references are. Nothing stops a function
returning the address of one of its own variables, which is no longer valid once the
function returns, and nothing frees memory automatically. The language's ownership model is
undecided ([#34](https://github.com/jonathanhaigh/leech/issues/34)). There is no null
pointer literal, but a pointer returned by a C function may be null. The builtin
`__is_null` tests a `*mut T` for that; it does not yet accept a `*T`.

## Methods

An `impl` block gives a type functions of its own. A function whose first parameter is
`*self` or `*mut self` is a *method*: `self` is a pointer to the value it was called on.
Other functions in the block are called through the type's name, as in `Counter::new(3)`:

```leech test=methods file=counter.leech mode=run exit=3
import std::io;

struct Counter {
    mut count: u8,
    limit: u8,
}

impl Counter {
    fn new(limit: u8) Counter {
        Counter { count: 0, limit: limit }
    }

    fn is_full(*self) bool {
        self.*.count >= self.*.limit
    }

    fn tick(*mut self) bool {
        if (self.*.is_full()) {
            return false;
        }
        self.*.count = self.*.count + 1;
        true
    }
}

fn tick_twice(counter: *mut Counter) {
    counter.*.tick();
    counter.*.tick();
}

fn main() i32 {
    let mut counter = Counter::new(3);
    tick_twice(&counter);
    counter.tick();
    if (not counter.tick() and counter.is_full()) {
        io::println("full");
    }
    let count: i32 = counter.count;
    count
}
```

```text output=methods
full
```

`counter.tick()` passes `&counter` as `self`. A `*mut self` method needs a place it can
write to, so `counter` must be `let mut`. Through a pointer, call a method on the
pointed-to value: `counter.*.tick()` in `tick_twice`, and `self.*.is_full()` in `tick`.
Methods can also come from traits, which [chapter 5](05-generics-traits-comptime.md)
introduces.

Next: [Modules and the standard library](04-modules-and-stdlib.md). Back to the
[tour](index.md) or the [user guide](../index.md).
