#!/bin/sh
# Cross-check the compiler against itself.
#
#   tests/variants.sh build/stage2 FILE.fl...
#
# The compiler is rebuilt with each combination of optimisation level and
# register allocator. Those builds contain very different machine code, but
# they are all the same program, so each must compile the compiler to exactly
# the bytes stage 2 consists of. A bug in an optimisation pass or in either
# allocator that the compiler's own source exercises shows up here as a diff.
set -u
STAGE2=${1:?usage: tests/variants.sh STAGE2 SOURCES...}
shift
fail=0
for cfg in "-O1 --regalloc=linear" "-O0 --regalloc=graph" "-O0 --regalloc=linear"; do
    name=$(echo "$cfg" | tr -d ' =-')
    if ! "$STAGE2" $cfg -o "build/variant_$name" "$@"; then
        echo "FAIL [$cfg]: could not build the compiler"
        fail=1
        continue
    fi
    if ! "build/variant_$name" -o "build/variant_$name.out" "$@"; then
        echo "FAIL [$cfg]: the resulting compiler crashed"
        fail=1
    elif cmp -s "build/variant_$name.out" "$STAGE2"; then
        echo "ok   [$cfg] compiler reproduces stage 2"
    else
        echo "FAIL [$cfg]: compiler built this way produces a different stage 2"
        fail=1
    fi
done
[ "$fail" = 0 ]
