# Compiler internals

```
source text
   | lexer.fl
tokens
   | parser.fl
AST (ast.fl)
   | infer.fl, types.fl        Hindley-Milner inference; annotates the AST
typed AST
   | lower.fl                  SSA construction (Braun et al.)
SSA IR (ir.fl)
   | sccp.fl gvn.fl inline.fl licm.fl, helpers in cfg.fl
optimised SSA
   | isel.fl                   instruction selection, phi elimination
machine IR over virtual registers (mir.fl)
   | lsra.fl or color.fl, helpers in regalloc.fl
machine IR over physical registers
   | x86.fl                    encoding, ELF
executable
```

`main.fl` drives the pipeline. Everything is one whole-program compilation:
the source files on the command line are lexed into a single token stream.

## Type inference

`types.fl` defines types as a two-constructor tree: `TVar` (an inference
variable, which is a mutable cell with an optional link) and `TCon` (a name
applied to arguments). `unify_types` is textbook unification with an occurs
check; binding a variable writes its link, and `prune` follows links with
path compression.

`infer.fl` is algorithm W arranged around the call graph:

1. Build the graph of which function mentions which, and find its strongly
   connected components with Tarjan's algorithm. Tarjan emits components
   callees-first, which is the order they need to be checked in.
2. For each component, give every function a type made of its annotations
   and fresh variables, then infer all the bodies. Functions in the same
   component see each other's monomorphic types.
3. When the component is solved, its functions are generalised: every
   variable still unbound in a function's type is universally quantified.
   Each later reference instantiates the type with fresh variables.

Local variables are assignable, so they are never generalised (the value
restriction, taken to its conclusion). A function's explicit type parameters
are represented as rigid constructors named `'T/function` while its body is
checked, so the body cannot quietly specialise them; instantiation replaces
them like any other quantified variable.

The checker leaves two notes on the AST for the lowering phase: the index of
each accessed field, and whether an `==` compares strings. The second one can
only be decided once the component is solved, so equality checks are queued
and resolved at the end.

## SSA construction

`lower.fl` goes from the AST straight to SSA, following Braun, Buchwald,
Hack, Leißa, Mallon and Zwinkau, "Simple and Efficient Construction of Static
Single Assignment Form" (CC 2013). There is no separate pass that computes
dominance frontiers and no intermediate non-SSA form.

Each source variable gets a slot. A write records the value for (slot, block).
A read looks up the current block and, failing that, recurses into the
predecessors: one predecessor just forwards the question, several create a
phi, and a block whose predecessors are not all known yet (a loop header
before its back edge exists) gets a placeholder phi that is completed when
the block is sealed. Phis that turn out to merge a single value are removed
on the spot and again, to a fixed point, in `remove_trivial_phis`.

Short-circuit operators and value-producing `if`/`match` need no special
handling: their result is written to a temporary slot in each arm and read
at the join, and the same machinery supplies the phi.

### Loop rotation

`while c { body }` is emitted as `if c { do { body } while c }`: the
condition is lowered twice, once as a guard and once at the bottom. The loop
then has a single block at its top that dominates the back edge and the exit
test, and the guard's true edge is where a preheader goes. Both matter for
LICM. It also leaves one conditional branch per iteration instead of a
conditional plus an unconditional one.

## The IR

A function is an arena of instructions and a list of blocks (`ir.fl`). An
instruction's index in the arena is the name of the value it computes. Blocks
hold instruction indices, with the terminator last; a phi's operands are
parallel to its block's predecessor list.

Replacing a value never touches its users. The replaced instruction becomes
`OP_COPY` of its replacement, `val` follows such chains, and `cleanup`
rewrites operands and drops the husks. `tidy` (in `cfg.fl`) restores the
canonical shape passes rely on: no unreachable blocks, no copies, no trivial
phis, straight-line chains merged, empty forwarding blocks bypassed.

Memory access has four load opcodes. `OP_LOAD`/`OP_LOAD8` read mutable
memory (struct fields, array elements). `OP_LOADC`/`OP_LOAD8C` read words that
are written once when the object is created and never again: array and string
lengths, constructor tags and fields, string bytes. The second kind is pure,
which is what lets GVN merge two `len(a)` and LICM hoist one out of a loop
without any alias analysis.

## Optimisation

Order (`optimize` in `main.fl`): SCCP and GVN on every function, so the
inliner judges callees by their cleaned-up size; inlining; then SCCP, GVN,
LICM and GVN again on the result.

**SCCP** (`sccp.fl`). Wegman and Zadeck's algorithm: each value is TOP, a
constant, or BOTTOM; blocks start unreachable and CFG edges become executable
only when a branch can take them. Phis merge only executable inputs, so a
constant survives a join with a path that is never taken, and that in turn
can decide further branches. Folding uses the same wrap-around arithmetic the
generated code would.

**GVN** (`gvn.fl`). A preorder walk of the dominator tree with a scoped hash
table of pure expressions. An instruction equal to one already in scope is
dominated by it and is replaced. Leaving a block pops what it pushed. The
walk also applies algebraic identities (`x+0`, `x*1`, `x-x`, `!(a<b)` becomes
`a>=b`, constant offsets fold into load/store displacements) and turns
branches on `!x` around.

**Inlining** (`inline.fl`). Functions are visited bottom-up over the call
graph. A callee is inlined if it is already finished (which excludes
recursion cycles) and no bigger than 30 instructions. The call's block is
split, the callee's blocks are cloned between the halves with parameters
mapped to arguments, and returns become jumps to the second half, where the
call instruction is reused as the phi of the returned values.

**LICM** (`licm.fl`). Dominators come from the Cooper-Harvey-Kennedy
iterative algorithm; a natural loop is found from each back edge. Every loop
gets a preheader, then loops are processed innermost first and instructions
whose operands are all defined outside the loop move to the preheader.

Speculation is the delicate part. Arithmetic can always be hoisted. Anything
that can fault (a division, any load) is hoisted only from a block that
dominates every latch and every exiting block, i.e. one that runs on every
trip. Because loops are rotated, the preheader is only reached when the body
will run at least once, so `for i in 0..n { s = s + a / b; }` does not divide
when `n` is zero. Loads of mutable memory additionally require that the loop
contains no store and no call.

## Instruction selection

`isel.fl` first splits critical edges, then selects each block bottom-up, and
the blocks in reverse layout order. Since definitions dominate uses, every
use is visited before its definition, so a use can absorb its operand:

- `load(add(p, shl(i, 3)), 8)` becomes `mov r, [p + i*8 + 8]`;
- an `add` becomes `lea`, taking a scaled index and a displacement with it;
- a comparison feeding a branch becomes `cmp` + `jcc` with no `setcc`;
- constants become immediates wherever the encoding has room;
- multiplying by a power of two becomes a shift.

`need[v]` counts the uses of `v` that still want it in a register. Folding a
use decrements it, and a pure instruction reached with a count of zero is
never emitted. Constants and addresses of strings and functions are not
instructions at all at this level; they are materialised at each use.

Phis are eliminated here. The copies for one CFG edge form a parallel move,
which is sequenced so that no copy reads a register an earlier one has
written; a cycle (a swap carried around a loop) is broken with a temporary.

### Calling convention

Internal, since nothing else is linked in: arguments in `rdi rsi rdx rcx r8
r9`, result in `rax`; `rbx rbp r12 r13 r14` are callee-saved, the rest
caller-saved. `rsp` is the stack pointer and `r15` holds the heap pointer for
the whole run. There is no frame pointer; spill slots are addressed from
`rsp`, which does not move inside a function body.

The constraints of the instruction set are expressed entirely through
physical registers appearing in the machine IR: `idiv` reads and writes `rax`
and `rdx`, variable shifts read `rcx`, a call defines every caller-saved
register. The allocators need no special cases; liveness makes a value that
is live across a call interfere with all of them.

## Register allocation

Both allocators share liveness (bit-vector dataflow), the spill rewriter and
the final renaming (`regalloc.fl`). Spilling a register gives it a stack slot
and rewrites the code: each use loads into a fresh temporary just before,
each definition stores just after. Then allocation runs again; the
temporaries have tiny live ranges and are marked unspillable.

**Linear scan** (`lsra.fl`), after Poletto and Sarkar. Instructions are
numbered in layout order, each virtual register gets one interval from first
definition to last use, and intervals are assigned in order of start. Uses of
physical registers become fixed busy ranges that an interval must not
overlap. When nothing is free, the candidate that ends furthest away is
spilled.

**Graph colouring** (`color.fl`): iterated register coalescing, after George
and Appel. An interference graph is built from liveness, with the two ends of
a move deliberately not interfering. Then:

- *simplify* removes nodes of degree < 14 that no move involves;
- *coalesce* merges the ends of a move when Briggs's test (the merged node
  has fewer than K neighbours of significant degree) or, against a physical
  register, George's test says the graph stays colourable;
- *freeze* gives up on the moves of a low-degree node so it can be simplified;
- *spill* picks a high-degree node by cost (uses weighted by loop depth,
  divided by degree) and continues optimistically;
- *select* pops nodes and assigns colours.

Coalescing is what removes the copies introduced by two-address instructions,
argument passing and phi elimination.

Compiling the compiler, graph colouring spills 471 registers and produces
158 KB of code; linear scan spills 551 and produces 179 KB.

## Emission

`x86.fl` encodes each machine instruction directly: REX prefix, opcode,
ModRM, SIB, displacement, immediate. The awkward corners of the encoding are
handled where they arise (`rsp`/`r12` as a base need a SIB byte, `rbp`/`r13`
cannot use the no-displacement form, byte registers need a REX prefix).

Jumps and calls use 32-bit relative offsets recorded as fixups. Blocks are
laid out in reverse postorder; a jump to the next block is dropped, and a
conditional branch whose true target is next is inverted.

A function's prologue pushes the callee-saved registers it writes and
reserves its spill slots; each `ret` undoes that.

### The executable

A static ELF64 file with two program headers and no sections:

| Segment | Address | Contents |
| --- | --- | --- |
| R+X | `0x200000000` | ELF header, program headers, start stub, code, string constants |
| R+W | `0x280000000` | one zero page of runtime state |

All references between functions and to string constants are rip-relative.
The start stub maps 16 GiB of address space for the heap (uncommitted until
touched), stores the initial stack pointer and the heap base in the runtime
page, calls `main`, and exits.

### Memory

Allocation is `mov result, r15; add r15, size`, inline. Nothing is freed.
Fresh pages from the kernel are zero, so new objects start zeroed. Strings
and arrays are a length word followed by their contents; structs are their
fields; a data-type value is a tag word followed by its fields, except that a
type whose constructors all carry nothing is represented by the bare tag.

`lib/rt.fl` implements strings, arrays, file I/O and argument access in Flint
itself, on top of a handful of intrinsics (`__alloc`, `__load64`, `__store8`,
`__syscall`, ...) that are available to any source file but used only there.
