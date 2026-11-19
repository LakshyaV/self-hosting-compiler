#!/usr/bin/env python3
"""Differential fuzzer for the Flint back end.

Generates random integer programs, then checks that the native compiler
agrees with the bootstrap seed about what they print. The seed executes
programs by translating them to Python, sharing no code with the optimiser,
instruction selector, register allocators or encoder, which makes it a useful
oracle for exactly those parts.

    tests/fuzz.py generate DIR COUNT [SEED]   write DIR/fuzzNNN.fl + .out
    tests/fuzz.sh FLINTC DIR                  compile, run and compare

Generating needs only Python. Checking is a separate shell script so that it
can run where the binaries do, on x86-64 Linux (see scripts/linux).
"""
import os
import random
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Gen:
    def __init__(self, rng):
        self.rng = rng
        self.lines = []
        self.depth = 0
        self.nfuncs = 0
        self.funcs = []  # (name, arity)

    def emit(self, text):
        self.lines.append("    " * self.depth + text)

    def const(self):
        r = self.rng
        return str(r.choice([0, 1, 2, 3, 5, 7, 8, 10, 16, 31, 64, 100, 255, 1000, 65537,
                             r.randint(-50, 50), r.randint(-100000, 100000),
                             2147483647, -2147483648, 4294967296, 123456789012345]))

    def expr(self, vars_, depth=0):
        r = self.rng
        if depth > 3 or r.random() < 0.25:
            return r.choice(vars_) if vars_ and r.random() < 0.7 else self.const()
        kind = r.random()
        a = self.expr(vars_, depth + 1)
        b = self.expr(vars_, depth + 1)
        if kind < 0.45:
            return "(%s %s %s)" % (a, r.choice(["+", "-", "*", "&", "|", "^"]), b)
        if kind < 0.55:
            # keep the divisor away from zero and from -1
            return "(%s %s ((%s & 1023) + 2))" % (a, r.choice(["/", "%"]), b)
        if kind < 0.65:
            return "(%s %s (%s & 31))" % (a, r.choice(["<<", ">>"]), b)
        if kind < 0.75:
            return "(0 - %s)" % a
        if kind < 0.88:
            return "(if %s { %s } else { %s })" % (self.cond(vars_, depth + 1), a, b)
        if self.funcs:
            name, arity = r.choice(self.funcs)
            return "%s(%s)" % (name, ", ".join(self.expr(vars_, depth + 2) for _ in range(arity)))
        return "(%s + %s)" % (a, b)

    def cond(self, vars_, depth=0):
        r = self.rng
        c = "(%s %s %s)" % (self.expr(vars_, depth + 1), r.choice(["<", "<=", ">", ">=", "==", "!="]),
                            self.expr(vars_, depth + 1))
        k = r.random()
        if k < 0.15:
            return "(%s && %s)" % (c, self.cond(vars_, depth + 2)) if depth < 3 else c
        if k < 0.3:
            return "(%s || %s)" % (c, self.cond(vars_, depth + 2)) if depth < 3 else c
        if k < 0.4:
            return "!%s" % c
        return c

    def block(self, vars_, loops, budget):
        r = self.rng
        local = list(vars_)
        for _ in range(r.randint(2, budget)):
            k = r.random()
            if k < 0.3 or not local:
                name = "v%d" % len(local)
                self.emit("let %s = %s;" % (name, self.expr(local)))
                local.append(name)
            elif k < 0.55:
                self.emit("%s = %s;" % (r.choice(local), self.expr(local)))
            elif k < 0.7 and self.depth < 4:
                self.emit("if %s {" % self.cond(local))
                self.depth += 1
                self.block(local, loops, 3)
                self.depth -= 1
                if r.random() < 0.5:
                    self.emit("} else {")
                    self.depth += 1
                    self.block(local, loops, 3)
                    self.depth -= 1
                self.emit("}")
            elif k < 0.85 and self.depth < 3:
                i = "i%d" % self.depth
                self.emit("for %s in 0..%d {" % (i, r.randint(0, 6)))
                self.depth += 1
                self.block(local + [i], loops + 1, 4)
                if r.random() < 0.3:
                    self.emit("if %s { %s; }" % (self.cond(local + [i]), r.choice(["break", "continue"])))
                self.depth -= 1
                self.emit("}")
            elif k < 0.92 and len(local) >= 2:
                a, b = r.sample(local, 2)
                self.emit("let t%d = %s;" % (len(local), a))
                self.emit("%s = %s;" % (a, b))
                self.emit("%s = t%d;" % (b, len(local)))
                local.append("t%d" % (len(local),))
            else:
                self.emit("acc = acc * 31 + %s;" % self.expr(local))
        return local

    def function(self):
        r = self.rng
        arity = r.randint(1, 6)
        name = "f%d" % self.nfuncs
        self.nfuncs += 1
        params = ["p%d" % i for i in range(arity)]
        self.emit("fn %s(%s) -> int {" % (name, ", ".join(p + ": int" for p in params)))
        self.depth = 1
        self.emit("let acc = %s;" % self.const())
        local = self.block(params + ["acc"], 0, 7)
        self.emit("acc + %s" % self.expr(local))
        self.depth = 0
        self.emit("}")
        self.emit("")
        self.funcs.append((name, arity))

    def program(self):
        for _ in range(self.rng.randint(2, 6)):
            self.function()
        self.emit("fn main() {")
        self.depth = 1
        for _ in range(8):
            name, arity = self.rng.choice(self.funcs)
            args = ", ".join(self.const() for _ in range(arity))
            self.emit("println(int_to_str(%s(%s)));" % (name, args))
        self.depth = 0
        self.emit("}")
        return "\n".join(self.lines) + "\n"


def generate(out_dir, count, seed):
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(seed)
    made = 0
    attempt = 0
    while made < count:
        attempt += 1
        src = Gen(random.Random(rng.random())).program()
        path = os.path.join(out_dir, "fuzz%03d.fl" % made)
        with open(path, "w") as f:
            f.write(src)
        try:
            res = subprocess.run(
                [sys.executable, os.path.join(ROOT, "bootstrap", "flint0.py"),
                 os.path.join(ROOT, "lib", "std.fl"), path],
                capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            continue
        if res.returncode != 0:
            continue  # e.g. a shift the generator could not keep in range
        with open(path[:-3] + ".out", "wb") as f:
            f.write(res.stdout)
        made += 1
    print("generated %d programs in %s (%d attempts)" % (count, out_dir, attempt))


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "generate":
        generate(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 1)
    else:
        sys.stderr.write(__doc__)
        sys.exit(2)
