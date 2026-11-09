<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Modules and the standard library

After this chapter you can split a program into modules in directories, choose what each
module makes public, call C functions, and use the small standard library to print and to
allocate memory.

## Packages and imports

Every file is a module. The directory holding the program's root module, the file you pass
to `leech build` or `leech run`, is the program's *package*, and a module's name is its
path within the package: `shapes.leech` beside the root is `shapes`, and
`text/banner.leech` is `text::banner`.

`import text::banner;` loads that module and binds its last segment, `banner`, so the
importing module calls `banner::show(...)`. An import path always starts from the package
directory, wherever the importing file is. This program has four modules:

```leech test=modules file=report.leech mode=run
import std::io;
import shapes;
import text::banner;

fn main() i32 {
    banner::show("Report");
    let box = shapes::Box { width: 3, height: 4 };
    io::print("box area: ");
    if (box.area() == shapes::expected_area) {
        io::println("as expected");
    }
    return 0;
}
```

`shapes.leech` is beside the root:

```leech test=modules file=shapes.leech
pub struct Box {
    pub width: i32,
    pub height: i32,
}

impl Box {
    pub fn area(*self) i32 {
        self.*.width * self.*.height
    }
}

pub let expected_area = 12;
```

`text/banner.leech` imports its neighbour `text/style.leech` by its full name,
`text::style`:

```leech test=modules file=text/banner.leech
import std::io;
import text::style;

pub fn show(title: *u8) {
    style::rule();
    io::println(title);
    style::rule();
}
```

```leech test=modules file=text/style.leech
import std::io;

pub fn rule() {
    io::println(line());
}

fn line() *u8 {
    "=========="
}
```

```text output=modules
==========
Report
==========
box area: as expected
```

`leech run report.leech` finds the three other modules through the imports and builds
them all into one program. Modules may import each other in a cycle. A file outside the
package directory cannot be imported, and a name must be imported before it can be used:
there is no way to name `text::style::rule` without `import text::style;`.

Writing `import style;` in `text/banner.leech`, as if imports were relative to the
importing file, names a module `style.leech` beside the root instead:

```leech test=relative-import file=notice.leech mode=error error=unknown-module
import text::banner;

fn main() i32 {
    banner::show("Notice");
    return 0;
}
```

```leech test=relative-import file=text/banner.leech
import std::io;
import style;

pub fn show(title: *u8) {
    style::rule();
    io::println(title);
}
```

```leech test=relative-import file=text/style.leech
import std::io;

pub fn rule() {
    io::println("==========");
}
```

`leech check notice.leech` reports an error in `text/banner.leech`, though the output
does not yet name the file:

```text diagnostic=relative-import
ERROR: cannot find module "style"
2| import style;
----------^
```

## Visibility

Everything a module declares is private to it unless it is marked `pub`. That applies to
functions, module-level `let` bindings, structs, enums, unions and traits, and separately
to each struct field and each function in an `impl` block. In `shapes.leech` above, `Box`,
both its fields, its `area` method and `expected_area` are all public; `line` in
`text/style.leech` is private, and only `rule` can call it.

Using another module's private item is an error:

```leech test=private file=vault.leech mode=error error=private-item-access
import secrets;

fn main() i32 {
    return secrets::combination();
}
```

```leech test=private file=secrets.leech
fn combination() i32 {
    return 1234;
}
```

`leech check vault.leech` reports the error in `vault.leech`, and a note pointing at
the declaration in `secrets.leech`:

```text diagnostic=private
ERROR: cannot access private function "combination"
4|     return secrets::combination();
-----------------------^
NOTE: "combination" defined here
1| fn combination() i32 {
---^
```

`main` itself need not be `pub`.

## Calling C

`extern fn` declares a function that the C library, or another object linked into the
program, defines. It has a signature and no body, and calls to it follow the C calling
convention:

```leech test=extern file=parse.leech mode=run exit=42
extern fn atoi(text: *u8) i32;
extern fn abs(n: i32) i32;

fn main() i32 {
    abs(atoi("-40")) + 2
}
```

Leech does not check that the declaration matches the C function, so a wrong signature is
undefined behavior. An `extern fn` is always public for now, so a module cannot keep its C
declarations to itself ([#130](https://github.com/jonathanhaigh/leech/issues/130)). The C
library is linked into every program. Leech has no C integer types such as `int` yet
([#33](https://github.com/jonathanhaigh/leech/issues/33)); use the Leech type of the same
width, `i32` for `int` on x86-64 Linux.

## The prelude

The bundled module `std::prelude` is the *prelude*: its public items are available in every
module without an import. It provides two functions:

- `panic(message: *u8) never` writes `message` and a newline to standard error, then
  aborts the program.
- `assert(condition: bool)` panics with `assertion failed` if `condition` is false.

Its C declarations of `write`, `strlen` and `abort` are also visible everywhere, until
`extern` functions can be private
([#130](https://github.com/jonathanhaigh/leech/issues/130)).

The prelude does not yet provide `Option` or `Result`
([#88](https://github.com/jonathanhaigh/leech/issues/88)). A module path starting with
`std` always names the bundled library, so a program cannot have its own `std` module.

## `std::io`

`std::io` writes strings to standard output:

- `print(s: *u8)` writes `s`.
- `println(s: *u8)` writes `s` and a newline.

Both write straight to the file descriptor, without buffering, so their output appears in
the order of the calls:

```leech test=print file=countdown.leech mode=run
import std::io;

fn main() i32 {
    io::print("3, ");
    io::print("2, ");
    io::print("1, ");
    io::print("liftoff");
    return 0;
}
```

```text output=print newline=no
3, 2, 1, liftoff
```

There is no input or file access yet, and no way to format a number
([#81](https://github.com/jonathanhaigh/leech/issues/81)).

## `std::mem`

`std::mem` allocates memory from the C heap:

- `alloc[T]() *mut T` allocates space for one `T`, panicking with `out of memory` if
  allocation fails. The memory is not initialized.
- `dealloc[T](p: *mut T)` frees memory from `alloc`. A null pointer is ignored.

`T` can be any type, including an array:

```leech test=alloc file=stack.leech mode=run exit=43
import std::mem;

struct Stack {
    items: *mut array[i32, 8],
    mut size: usize,
}

impl Stack {
    fn new() Stack {
        Stack { items: mem::alloc[array[i32, 8]](), size: 0 }
    }

    fn push(*mut self, item: i32) {
        self.*.items.*.[self.*.size] = item;
        self.*.size = self.*.size + 1;
    }

    fn pop(*mut self) i32 {
        self.*.size = self.*.size - 1;
        self.*.items.*.[self.*.size]
    }

    fn free(*self) {
        mem::dealloc(self.*.items);
    }
}

fn main() i32 {
    let mut stack = Stack::new();
    stack.push(3);
    stack.push(4);
    let top = stack.pop();
    let result = top * 10 + stack.pop();
    stack.free();
    result
}
```

`items` is not a `mut` field, yet `push` writes through it: the field's pointer never
changes, and it is a `*mut` pointer, so the array it points to can. Nothing frees the
memory automatically: Leech has no destructors
([#34](https://github.com/jonathanhaigh/leech/issues/34)). Nothing stops memory being
used after `dealloc` either, and there is no allocator interface yet
([#40](https://github.com/jonathanhaigh/leech/issues/40)).

Next: [Generics, traits and compile-time evaluation](05-generics-traits-comptime.md). Back
to the [tour](index.md) or the [user guide](../index.md).
