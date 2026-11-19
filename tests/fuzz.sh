#!/bin/sh
# Check the programs made by `tests/fuzz.py generate` against their expected
# output, under every optimisation level and register allocator.
#
#   tests/fuzz.sh build/stage2 build/fuzz
set -u
FLINTC=${1:?usage: tests/fuzz.sh FLINTC DIR}
DIR=${2:?usage: tests/fuzz.sh FLINTC DIR}
total=0
bad=0
for src in "$DIR"/*.fl; do
    base=${src%.fl}
    for cfg in "-O1 --regalloc=graph" "-O1 --regalloc=linear" "-O0 --regalloc=graph" "-O0 --regalloc=linear"; do
        total=$((total + 1))
        if ! $FLINTC $cfg -o "$base.bin" lib/rt.fl lib/std.fl "$src" 2> "$base.err"; then
            echo "FAIL $src [$cfg]: $(cat "$base.err")"
            bad=$((bad + 1))
            continue
        fi
        timeout 120 "$base.bin" > "$base.got" 2> /dev/null
        status=$?
        if [ "$status" != 0 ] || ! cmp -s "$base.got" "$base.out"; then
            echo "FAIL $src [$cfg]: exit status $status"
            bad=$((bad + 1))
        fi
    done
done
echo "$total checks, $bad failed"
[ "$bad" = 0 ]
