# Flint

A self-hosting compiler to x86-64 native code, written from scratch: no LLVM,
no assembler, no linker.

The goal: a small language with type inference, an SSA-based optimiser, a
real register allocator and direct ELF output, with the compiler rewritten in
its own language until building it with itself reaches a fixed point (stage 2
and stage 3 byte-identical).

Work in progress. The plan is tracked in the issues.
