# The Flint language

Flint is a small, statically typed, expression-oriented language. Types are
inferred; annotations are allowed anywhere and required nowhere (with one
exception, [noted below](#field-access)).

## Programs

A program is a set of top-level items: functions, structs, data types and
constants. Their order does not matter. Execution starts at `fn main()`.

```
const LIMIT = 10;

fn main() {
    for i in 0..LIMIT { println(int_to_str(i * i)); }
}
```

Comments run from `//` to the end of the line.

## Types

| Type | Values |
| --- | --- |
| `int` | 64-bit signed integers; arithmetic wraps |
| `bool` | `true`, `false` |
| `string` | immutable byte strings |
| `unit` | the single value `()` |
| `[T]` | fixed-length mutable arrays |
| `fn(A, B) -> R` | functions |
| `Name<T, ...>` | structs and data types |

Every value occupies one machine word: integers and booleans directly,
everything else as a pointer.

## Functions

```
fn gcd(a: int, b: int) -> int {
    if b == 0 { a } else { gcd(b, a % b) }
}

fn twice(f, x) { f(f(x)) }          // inferred: fn(fn(a) -> a, a) -> a
```

A function's value is its body's final expression, or the operand of
`return`. Parameter and result types may be omitted and are then inferred.
Functions are polymorphic wherever their inferred type leaves something
open, so `twice` works at every type.

Type parameters can be named explicitly, which lets them be used in
annotations. Inside the function's own body such a parameter is rigid: the
body must work for every choice of `T`.

```
fn first<T>(v: Vec<T>) -> T { at(v, 0) }
```

Functions are values, but there are no closures: only top-level functions
exist, and they cannot capture local variables. Functions take at most six
parameters.

## Variables and statements

```
let x = 1;            // all variables are mutable
let y: int = 2;       // optional annotation
x = x + y;
x += 1;               // also -=, on plain variables
```

`let` introduces a binding scoped to the enclosing block. A later `let` of
the same name shadows the earlier one.

```
while cond { ... }
for i in lo..hi { ... }     // lo, lo+1, ..., hi-1
break;  continue;  return value;
```

The bounds of a `for` loop are evaluated once. Its variable is a fresh
binding on each iteration: assigning to it does not affect the loop.

## Expressions

Blocks, `if` and `match` are expressions:

```
let sign = if n < 0 { -1 } else if n == 0 { 0 } else { 1 };
let area = { let w = 3; let h = 4; w * h };
```

An `if` without `else` has type `unit`.

Operators, loosest first:

```
||
&&
== != < <= > >=
|
^
&
<< >>
+ -
* / %
unary - !
calls f(x), indexing a[i], field access p.x
```

Note that the bitwise operators bind tighter than comparison, so
`x & 1 == 1` means `(x & 1) == 1`. `/` and `%` truncate toward zero. `>>` is
an arithmetic shift; shift counts must be in `0..63`. `&&` and `||`
short-circuit. Operands are evaluated left to right.

`==` and `!=` work on integers, booleans, strings (by content) and data
types whose constructors carry no data. Other types cannot be compared.

Literals: `42`, `0x2A`, `'a'` (a character literal is an `int`), `"text"`
with the escapes `\n \t \r \0 \\ \" \' \xNN`.

## Structs

```
struct Point { x: int, y: int }
struct Pair<A, B> { first: A, second: B }

let p = Point { x: 1, y: 2 };
p.x = p.x + 10;
```

Structs are mutable and have reference semantics: assigning a struct or
passing it to a function shares it.

### Field access

`e.name` needs to know which struct `e` is. Normally inference has already
worked that out. When it has not, for instance for an unannotated parameter
whose first use is a field access, the field name settles it if exactly one
struct declares a field of that name. If several do, the compiler asks for an
annotation.

## Data types and pattern matching

```
type Option<T> = None | Some(T)
type Expr = Num(int) | Add(Expr, Expr) | Neg(Expr)

fn eval(e: Expr) -> int {
    match e {
        Num(n) => n,
        Add(a, b) => eval(a) + eval(b),
        Neg(a) => 0 - eval(a)
    }
}
```

A pattern is a constructor with a name or `_` for each field, an integer
literal, a constant, a name (matches anything and binds it) or `_`. Patterns
do not nest. A `match` must be exhaustive: either every constructor is
covered or there is a catch-all arm.

Names beginning with a capital letter are types, constructors and constants;
all other names are variables and functions.

## Constants

```
const MAX_DEPTH = 64;
const GREETING = "hello";
```

A constant is a literal (integer, string or boolean) and can be used in
expressions and as a pattern.

## Built-in functions

Compiler intrinsics:

| | |
| --- | --- |
| `len(a: [T]) -> int` | array length |
| `str_len(s: string) -> int` | string length in bytes |
| `char_at(s: string, i: int) -> int` | byte at index `i` |

Provided by the runtime (`lib/rt.fl`):

| | |
| --- | --- |
| `array_new(n, x) -> [T]` | array of `n` copies of `x` |
| `array_alloc(n) -> [T]` | array of `n` zeroed elements |
| `str_concat(a, b)`, `substr(s, start, n)`, `str_eq(a, b)` | string operations |
| `str_from_array(a: [int], n) -> string` | string from the first `n` byte values |
| `print(s)`, `eprint(s)` | write to stdout / stderr |
| `read_file(path) -> string` | whole file; exits if it cannot be read |
| `write_file(path, data, mode)` | create or replace a file |
| `arg_count()`, `arg(i)` | command-line arguments |
| `exit(code)` | end the process |

`lib/std.fl` adds `Option`, `Vec` (`vec_new`, `push`, `pop`, `at`, `set`,
`last`, ...), a string-keyed `Map`, `IntSet`, bit sets, `int_to_str`, string
builders (`sb_add`, `sb_int`, `sb_str`) and a stable `sort_by_key`.

Array indexing and `char_at` are not bounds-checked.
