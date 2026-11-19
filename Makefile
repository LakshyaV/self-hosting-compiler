# Flint: a self-hosting compiler to x86-64 Linux.
#
#   make            build the compiler (build/flintc)
#   make bootstrap  build stages 1-3 and check that stage 2 == stage 3
#   make test       run the test suite
#   make check      everything: bootstrap, variants, tests
#
# Stage 1 needs python3 (for the bootstrap seed). The stages after it are
# x86-64 Linux programs; on other hosts they run in a container through
# scripts/linux, which needs Docker.

SRC := lib/rt.fl lib/std.fl \
       src/lexer.fl src/ast.fl src/parser.fl src/types.fl src/infer.fl \
       src/ir.fl src/lower.fl src/cfg.fl src/sccp.fl src/gvn.fl src/licm.fl \
       src/inline.fl src/mir.fl src/isel.fl src/regalloc.fl src/lsra.fl \
       src/color.fl src/x86.fl src/main.fl

# The seed supplies the runtime itself, so it takes everything but lib/rt.fl.
SEED_SRC := $(filter-out lib/rt.fl,$(SRC))

LINUX := scripts/linux

.PHONY: all bootstrap variants test check clean

all: build/flintc

# Stage 1: the compiler, run by the Python seed, compiling itself.
build/stage1: $(SRC) bootstrap/flint0.py
	@mkdir -p build
	python3 bootstrap/flint0.py $(SEED_SRC) -- -o $@ $(SRC)

# Stage 2: the stage 1 binary compiling the compiler.
build/stage2: build/stage1
	$(LINUX) build/stage1 -o $@ $(SRC)

# Stage 3: the stage 2 binary compiling the compiler.
build/stage3: build/stage2
	$(LINUX) build/stage2 -o $@ $(SRC)

build/flintc: build/stage2
	cp build/stage2 $@

bootstrap: build/stage3
	cmp build/stage2 build/stage3
	@echo "fixed point reached: stage 2 and stage 3 are byte-identical"

variants: build/stage2
	$(LINUX) tests/variants.sh build/stage2 $(SRC)

test: build/stage2
	$(LINUX) tests/run.sh build/stage2

check: bootstrap variants test

clean:
	rm -rf build
