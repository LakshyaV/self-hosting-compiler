# Bootstrapping

A compiler written in its own language cannot compile itself until it has
been compiled. This is how Flint gets started, and how the result is checked.

## The seed

`bootstrap/flint0.py` is a second, much smaller implementation of Flint: a
translator from Flint to Python, about 850 lines. It lexes and parses a
program, emits a Python function for each Flint function, and executes the
result.

It is deliberately not a compiler in the interesting sense. It does no type
checking (the real compiler will do that, to itself, as soon as it runs). It
does not optimise and it generates no machine code. What it must get right is
the language's dynamic semantics, exactly:

- 64-bit wrap-around for `+ - * <<` and unary minus, since Python integers
  are unbounded;
- division and remainder that truncate toward zero, where Python's floor;
- left-to-right evaluation, including the cases where Python would evaluate
  the right-hand side of an assignment first;
- block scoping and shadowing, which Python's function-level scopes lack.

The native runtime (`lib/rt.fl`), which is written in Flint using raw memory
intrinsics, is replaced under the seed by a few Python functions with the
same names.

## The stages

```
            seed (Python)
                 |  runs
                 v
   compiler source ----compiles----> build/stage1
                                          |  runs
                                          v
   compiler source ----compiles----> build/stage2
                                          |  runs
                                          v
   compiler source ----compiles----> build/stage3
```

Stage 2 and stage 3 are the outputs of the same compiler source applied to
the same input; they differ only in which binary did the work. If stage 1 is
a correct compilation of the compiler, stage 2 is too, and then stage 2 and
stage 3 were produced by equivalent programs and must be the same bytes.
`make bootstrap` runs `cmp build/stage2 build/stage3`.

Stage 1 would be allowed to differ from stage 2: it is produced by the
compiler running on a different implementation of the language. It does not
differ. The seed and the generated machine code agree on every decision the
compiler makes while compiling 6,000 lines, which is a decent test of both.
CI checks this too.

## Why the output is reproducible

Nothing in the compiler depends on anything but its input:

- there are no pointers-as-integers, addresses never reach the output;
- the hash tables are the compiler's own, with deterministic hashing, and
  nothing iterates over one in hash order;
- sorting is a stable merge sort;
- the output contains no timestamps, paths or build identifiers.

## The variant cross-check

The fixed point shows the compiler is consistent with itself, not that every
part of it is right: a pass that is wrong in a way the compiler's own source
never triggers would go unnoticed, and so would a bug that reproduces itself
faithfully. `make variants` narrows that gap by building the compiler in
three more ways:

```
stage2 -O1 --regalloc=linear   -> variant A
stage2 -O0 --regalloc=graph    -> variant B
stage2 -O0 --regalloc=linear   -> variant C
```

A, B and C contain very different machine code. A went through linear scan
instead of graph colouring; B and C skipped SCCP, GVN, LICM and inlining
entirely. But each is the same compiler, so each is then asked to compile
the compiler normally, and each result must be byte-identical to stage 2.

If the optimiser miscompiled something in the compiler, B and C (which never
ran it) would disagree with stage 2. If the two register allocators disagreed
about the meaning of some function, A would.

## Trusting the seed

The seed is the one piece that is not checked by construction, so it is kept
small enough to read in one sitting, and it earns some trust the other way
round: every program in `tests/run` and every fuzz program has its expected
output produced by the seed and is then compiled natively under four
configurations. The two implementations share no code beyond the Flint
sources of the library, and they have to agree.
