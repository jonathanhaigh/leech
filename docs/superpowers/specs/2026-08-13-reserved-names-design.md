<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Reserve keywords and compiler-bound type names

## Problem

Leech reserves almost nothing. `IDENT` is `/[_a-zA-Z][_0-9a-zA-Z]*/`, and
Lark's contextual lexer only offers terminals the parser can accept in the
current state — so anywhere a bare identifier is expected, a keyword lexes
as one. Every one of these compiles today:

```leech
struct if { mut x: i32 }        enum if(u8) { A }        trait if { fn f(*self) i32; }
fn struct() i32 { return 0; }   let return = 5;          fn f(if: i32) i32 { return 0; }
struct Foo { mut if: i32 }      enum E(u8) { if }        trait T { fn if(*self) i32; }
fn f[if](x: if) i32 { ... }     let Self = 5;            struct bool { mut x: i32 }
```

Seventeen such declarations were probed; all seventeen were accepted.

Builtin type names are worse than merely shadowable. `i32` is not declared
anywhere: `ir_env.Env.get` synthesizes it on lookup *failure* and caches it
into the root scope, so any user declaration wins. The result is not a
clean shadow but an incoherent diagnostic — `struct i32 { ... }` compiles
its declaration and then fails with:

```
Return expression has invalid type "i32" in function "main" that returns "i32"
```

Two distinct types, one spelling. The problem is not that shadowing is
permitted; it is that it is permitted *and* broken.

## Goal

- No declaration may take a reserved name, in either namespace.
- The reserved set is explicit and readable, and cannot silently drift from
  the grammar.
- Compiler-introduced bindings of reserved names keep working: `Self` in
  trait methods and impls, `never` in return position, the builtin types,
  and a method's `*self` receiver.

## Design

### What is reserved

`Self` in all namespaces, rather than only in the type namespace, follows
the languages that have the feature: Rust makes `Self` a strict keyword
usable as no kind of identifier at all, and Swift makes it a keyword
escapable only with backticks. Neither scopes it to types.

Reserving *everything* in *all* namespaces is deliberately stricter than
strictly necessary — `let i32 = 5;` is harmless in principle. A uniform
rule is easier to explain, and far easier to relax later than to tighten.

### `src/leech/reserved.py`

```python
KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "and",
        "break",
        "continue",
        "else",
        "enum",
        "extern",
        "false",
        "fn",
        "for",
        "if",
        "impl",
        "import",
        "let",
        "mut",
        "not",
        "or",
        "pub",
        "return",
        "self",
        "struct",
        "trait",
        "true",
        "while",
    }
)

#: Type names the compiler binds itself, so source never declares them.
TYP_NAMES: Final[frozenset[str]] = frozenset({"Self", "bool", "isize", "never", "usize"})


def is_reserved(name: str) -> bool:
    from leech import typs  # local: typs reaches this module back through ir_env

    return name in KEYWORDS or name in TYP_NAMES or typs.IntTyp.from_name(name) is not None
```

The sized integer types are matched by spelling rather than listed, because
the set they form is unbounded — `IntTyp._NAME_RE` accepts any width. This
mirrors Zig, which recognizes `iN`/`uN` (to 65535) in the compiler rather
than declaring them in a prelude, and likewise refuses to let a declaration
collide with one.

Reservation asks only whether a name is *spelled* like an int type, never
whether a type of that width could be built — hence `IntTyp.is_name` rather
than `from_name`. The two answers differ: a width with more digits than
CPython will convert spells an int type but builds none. Keeping them apart
also means that bounding the widths Leech supports, as Zig bounds its at
65535, would not release the names above the bound for declarations.

`from_name` guards that conversion too. It is reached from `ir_env.Env.get`
for any unresolved type reference, so without the guard `let x: i<5000
digits>` crashed the compiler rather than reporting an unknown type. That
crash predates this change; the reservation check merely made it reachable
from a second direction.

`never` is in `TYP_NAMES` even though it is only bound in return position
today. Leaving it out would let `struct never` compile and then confuse
that one position. Reserving it is cheaper than reasoning about the
interaction. (Other languages mostly do *not* reserve their bottom type:
Rust spells it `!`, so there is no name; Kotlin's `Nothing` and Scala's
`Nothing` are ordinary shadowable library types; TypeScript's `never` is
reserved only in type position.)

### Where the check runs

At each site where source introduces a name, rather than inside `Env.add`.
`Env.add` is a single funnel and would be impossible to forget, but the
compiler's own bindings of reserved names pass through it too, so it would
need a bypass flag threaded through `add`/`add_var`/`add_container` — and
the per-site checks have the identifier's own AST node, giving a precise
span instead of `Env.add`'s `span or ast.opt_span(item)` fallback.

Eight sites cover every user-authored name:

| Site | Covers |
| --- | --- |
| `typs.typ_params_from_ast` | generic parameters, of functions, structs, traits and impls |
| `ir_module.Mod._add_item` | struct, enum, trait, function, variable and import names |
| `reserved.check_fn_params`, from `NonBuiltinFnSpec.__init__` and `TraitMethod.__init__` | function parameters, of definitions, `extern` declarations and trait prototypes alike |
| `typcheck` let-binding | local variables |
| `typcheck` while-expression | loop labels |
| `typs.StructTyp._add_member` | struct fields *and* inherent-impl associated functions |
| `typs.EnumTyp.variants` | enum variants |
| `ir_traits.Trait.__init__` | trait method names |

Trait *impl* method names need no site of their own: a name that isn't one
of the trait's own methods is already rejected as an extra method, so the
trait-declaration check covers them.

The set is derived from the grammar rather than from inspection, because
inspection missed one. Every rule mentioning a bare `ident` is either a
declaration handled above, or a *reference* needing no check:
`break_stmt`/`continue_stmt` labels, `struct_field_expr`,
`field_access_expr` and `path`. Loop labels are the site least like the
others — a label is not bound in an `Env` at all, but recorded on the type
checker's own `_loop_labels` stack, so enumerating scopes does not find
it.

Compiler-introduced bindings need no exemption, because none of them pass
through these sites — builtins bind through `Env.add_container` directly,
as do `Self` and `never`.

One binding does need an exemption: a method's receiver. `params` includes it, so it
reaches the parameter check, and `ast.Receiver` sets its name to
`Ident._synthetic("self", ...)`. That name is generated, not written, so
the check skips `ast.Receiver` rather than treating `self` as available.

### Keeping the list honest

`KEYWORDS` is written out rather than derived from the parser, so it reads
as documentation. A test stops it drifting: Lark exposes the compiled
terminals, so the grammar's keywords can be recovered directly with no
text-scraping.

```python
terminals = parse.build_parser("mod").terminals
from_grammar = {
    t.pattern.value
    for t in terminals
    if isinstance(t.pattern, lark.lexer.PatternStr) and t.pattern.value.isidentifier()
}
assert from_grammar == reserved.KEYWORDS
```

Adding a keyword to the grammar without adding it to `KEYWORDS` fails this
test.

## Non-goals

- **Grammar-level rejection.** A semantic check catches declarations, which
  is where the damage is: a keyword cannot be *referenced* usefully anyway,
  since the parser takes the keyword's own production. Making `IDENT`
  exclude keywords outright would be more robust but needs lexer surgery
  for no additional benefit here.
- **Reserving names for future use.** Only what the compiler actually binds
  or the grammar actually defines is reserved.

## Testing

Parametrized rejection of a keyword used as each of the eleven declaration
kinds the sites cover, plus each category of compiler-bound type name
(a sized integer type as a type, a local and a field; `bool`, `usize` and
`never` as types; `Self` as a local). The grammar-agreement test above.
Unit coverage of `is_reserved` in both directions, including near-misses
that must *not* be reserved (`i32x`, `iffy`, `selfish`, `Self2`, `i`, `u`).

Loop labels get their own cases, covering a sized integer type, `bool`,
`Self`, `never` and a keyword. A keyword label is worth testing separately
because the lexer catches keywords there only inconsistently: `if:` fails
to parse, while `struct:` reaches type checking, so the semantic check is
what actually rejects it.

Parameter cases cover a definition, an `extern` prototype and a trait
prototype, because only the first is reached by body type checking. The
existing suite is the real regression guard for receivers: every method in
it declares a `*self` receiver, so wrongly treating one as a declared
parameter fails loudly and broadly rather than subtly.
