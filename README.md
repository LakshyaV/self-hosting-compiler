# Flint

A self-hosting compiler for a small statically typed language, written from
scratch. It compiles to native x86-64 Linux executables and uses no LLVM, no
assembler and no linker: the compiler writes the ELF file byte by byte.

The compiler is written in Flint. Compiling it with itself reaches a fixed
point: the stage 2 and stage 3 binaries are byte-identical.

```
$ make bootstrap
python3 bootstrap/flint0.py ... -o build/stage1 ...
build/stage1 -o build/stage2 ...
build/stage2 -o build/stage3 ...
cmp build/stage2 build/stage3
fixed point reached: stage 2 and stage 3 are byte-identical
```

## What is in it

| Stage | What it does | Source |
| --- | --- | --- |
| Lexer, parser | Hand-written lexer and recursive-descent parser | `src/lexer.fl`, `src/parser.fl` |
| Type inference | Hindley-Milner: unification, let-polymorphism over call-graph SCCs, optional annotations, rigid type parameters | `src/types.fl`, `src/infer.fl` |
| SSA construction | Braun et al., built directly from the AST with no dominance frontiers; loops are rotated on the way | `src/lower.fl` |
| SCCP | Sparse conditional constant propagation (Wegman-Zadeck) | `src/sccp.fl` |
| GVN | Dominator-tree value numbering with a scoped hash table, plus peephole identities | `src/gvn.fl` |
| LICM | Preheader insertion and hoisting, with a safety rule for anything that can fault | `src/licm.fl` |
| Inlining | Bottom-up over the call graph, cloning the callee into a split block | `src/inline.fl` |
| Instruction selection | Bottom-up selection that folds addressing modes, immediates and compare-and-branch; phi elimination by parallel moves | `src/isel.fl` |
| Register allocation | Linear scan (`--regalloc=linear`) and iterated register coalescing, i.e. graph colouring with Briggs/George coalescing (`--regalloc=graph`, the default) | `src/lsra.fl`, `src/color.fl` |
| Code emission | x86-64 instruction encoding and a static ELF64 executable, written directly | `src/x86.fl` |

About 5,600 lines of Flint for the compiler and 430 for its library and
runtime. The compiler compiles itself into a ~160 KB executable.

## The language

Flint is expression-oriented, with type inference, generics, structs,
algebraic data types and pattern matching.

```
type Shape = Circle(int) | Rect(int, int)

fn area(s) {
    match s {
        Circle(r) => 3 * r * r,
        Rect(w, h) => w * h
    }
}

// Works for any element type and any measure; nothing is annotated.
fn largest(items, measure) {
    let best = at(items, 0);
    for i in 1..items.len {
        if measure(at(items, i)) > measure(best) { best = at(items, i); }
    }
    best
}

fn main() {
    let shapes = vec_new();
    push(shapes, Circle(3));
    push(shapes, Rect(4, 9));
    println(int_to_str(area(largest(shapes, area))));
}
```

The full description is in [docs/language.md](docs/language.md); more
programs are in [examples/](examples) and [tests/run/](tests/run).

## Building and running

Requirements: `python3` (only to run the bootstrap seed once) and, unless you
are already on x86-64 Linux, Docker with amd64 emulation. `scripts/linux`
runs a command natively on x86-64 Linux and in a container everywhere else.

```
make                # build/flintc
make bootstrap      # stages 1-3, and compare stage 2 with stage 3
make test           # the test suite
make check          # all of the above plus the variant cross-check
```

Compiling a program:

```
$ scripts/linux build/flintc -o hello lib/rt.fl lib/std.fl examples/hello.fl
$ scripts/linux ./hello
hello, world
```

A program is the list of source files given on the command line; `lib/rt.fl`
(the runtime) and `lib/std.fl` (vectors, maps, strings) are ordinary Flint
files that are simply compiled along with it.

```
flintc [options] -o OUTPUT FILE.fl...
  -O0, -O1             optimisation off / on (default on)
  --regalloc=graph     graph colouring with coalescing (default)
  --regalloc=linear    linear scan
  --dump-ir            print the optimised SSA to stdout
  --stats              print code size and spill counts to stderr
  --verbose            name each function as it is compiled
```

## How the bootstrap works

There is exactly one compiler, and it is written in Flint. The only other
implementation of the language is `bootstrap/flint0.py`, a seed of about 850
lines of Python that translates Flint to Python. It has no type checker, no
optimiser and no code generator; its one job is to run the real compiler once.

1. **Stage 1**: the seed runs the compiler's source, and that compiler
   compiles its own source into a native binary.
2. **Stage 2**: the stage 1 binary compiles the same source.
3. **Stage 3**: the stage 2 binary compiles it again.

Stage 2 and stage 3 are the same compiler source built by binaries of that
same source, so they must be identical, and `make bootstrap` checks that they
are. Here stage 1 is identical to them as well, which says something stronger:
the compiler behaves the same whether Python or its own machine code is
executing it.

`make variants` pushes on that. It rebuilds the compiler with the optimiser
off and with the other register allocator. Those binaries contain completely
different machine code, but each must still compile the compiler to exactly
the stage 2 bytes. See [docs/bootstrap.md](docs/bootstrap.md).

## Testing

- `tests/run/`: programs with expected output, each compiled and run under
  all four combinations of `-O0`/`-O1` and linear scan/graph colouring.
- `tests/fail/`: programs that must be rejected, with the exact diagnostic.
- `tests/variants.sh`: the cross-check described above.
- `tests/fuzz.py`: a differential fuzzer that generates random programs and
  uses the seed as an oracle for the native back end.

## Layout

```
bootstrap/flint0.py   the Python seed
lib/rt.fl             native runtime: allocation, strings, files, syscalls
lib/std.fl            Vec, Map, IntSet, string helpers
src/                  the compiler
tests/                test programs, runners and the fuzzer
examples/             small programs to read and run
docs/                 language reference and internals
```

How each pass works, the calling convention, the memory model and the ELF
layout are covered in [docs/internals.md](docs/internals.md).

## Limits

Deliberately out of scope: garbage collection (memory comes from a bump
allocator and is never freed), closures (functions are first-class values but
cannot capture), floating point, separate compilation, and any target other
than x86-64 Linux. Functions take at most six parameters.

## License

MIT. See [LICENSE](LICENSE).
