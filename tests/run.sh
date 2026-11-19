#!/bin/sh
# Run the test suite with the given compiler binary.
#
#   tests/run.sh build/stage2
#
# Every program in tests/run is compiled under each optimisation level and
# register allocator, run, and its output and exit status compared with the
# checked-in expectation. Every program in tests/fail must be rejected with
# exactly the expected diagnostic.
set -u
FLINTC=${1:?usage: tests/run.sh FLINTC}
OUT=build/tests
mkdir -p "$OUT"
pass=0
fail=0

for cfg in "-O1 --regalloc=graph" "-O1 --regalloc=linear" "-O0 --regalloc=graph" "-O0 --regalloc=linear"; do
    for src in tests/run/*.fl; do
        name=$(basename "$src" .fl)
        want_status=0
        [ -f "tests/run/$name.status" ] && want_status=$(cat "tests/run/$name.status")
        if ! $FLINTC $cfg -o "$OUT/$name" lib/rt.fl lib/std.fl "$src" 2> "$OUT/$name.err"; then
            echo "FAIL $name [$cfg]: did not compile"
            cat "$OUT/$name.err"
            fail=$((fail + 1))
            continue
        fi
        "$OUT/$name" one "two words" 3 > "$OUT/$name.got" 2> /dev/null
        status=$?
        if [ "$status" != "$want_status" ]; then
            echo "FAIL $name [$cfg]: exit status $status, expected $want_status"
            fail=$((fail + 1))
        elif ! cmp -s "$OUT/$name.got" "tests/run/$name.out"; then
            echo "FAIL $name [$cfg]: wrong output"
            diff "tests/run/$name.out" "$OUT/$name.got" | head -10
            fail=$((fail + 1))
        else
            pass=$((pass + 1))
        fi
    done
done

for src in tests/fail/*.fl; do
    name=$(basename "$src" .fl)
    if $FLINTC -o "$OUT/$name" lib/rt.fl lib/std.fl "$src" 2> "$OUT/$name.err"; then
        echo "FAIL $name: compiled, but should have been rejected"
        fail=$((fail + 1))
    elif ! cmp -s "$OUT/$name.err" "tests/fail/$name.err"; then
        echo "FAIL $name: wrong diagnostic"
        diff "tests/fail/$name.err" "$OUT/$name.err"
        fail=$((fail + 1))
    else
        pass=$((pass + 1))
    fi
done

echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
