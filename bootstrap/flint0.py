#!/usr/bin/env python3
"""flint0 -- the bootstrap seed for the Flint compiler.

Flint's compiler is written in Flint. Something has to run it the first time,
and this is that something: a small translator from Flint to Python. It does no
type checking and no optimisation; it only has to be faithful enough to execute
the real compiler once, producing the stage 1 native binary.

    python3 bootstrap/flint0.py FILE.fl... [-- ARGS...]

Value representation:
    int, bool      Python int / bool (arithmetic wraps to 64 bits like the CPU)
    string         bytes
    [T]            list
    struct         instance of a generated __slots__ class
    enum type      int tag, when no constructor carries data
    other types    tuple (tag, field...)
"""
import os
import re
import sys

KEYWORDS = {
    "fn", "let", "if", "else", "while", "for", "in", "return", "break",
    "continue", "match", "struct", "type", "const", "true", "false",
}

TOKEN_RE = re.compile(
    r"""
    (?P<ws>[ \t\r\n]+|//[^\n]*)
  | (?P<int>0x[0-9a-fA-F]+|[0-9]+)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<str>"(?:[^"\\\n]|\\.)*")
  | (?P<chr>'(?:[^'\\\n]|\\x[0-9a-fA-F]{2}|\\.)')
  | (?P<op>\.\.|->|=>|==|!=|<=|>=|<<|>>|&&|\|\||\+=|-=|[-+*/%&|^!<>=(){}\[\],;:.])
    """,
    re.X,
)

ESCAPES = {"n": 10, "t": 9, "r": 13, "0": 0, "\\": 92, '"': 34, "'": 39}


class FlintError(Exception):
    pass


def unescape(body, where):
    out = bytearray()
    i = 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out += c.encode("utf-8")
            i += 1
            continue
        e = body[i + 1]
        if e == "x":
            out.append(int(body[i + 2:i + 4], 16))
            i += 4
        elif e in ESCAPES:
            out.append(ESCAPES[e])
            i += 2
        else:
            raise FlintError("%s: unknown escape \\%s" % (where, e))
    return bytes(out)


def lex(src, fname):
    toks = []
    pos = 0
    line = 1
    n = len(src)
    while pos < n:
        m = TOKEN_RE.match(src, pos)
        if not m:
            raise FlintError("%s:%d: unexpected character %r" % (fname, line, src[pos]))
        kind = m.lastgroup
        text = m.group()
        where = "%s:%d" % (fname, line)
        if kind == "int":
            toks.append(("int", int(text, 0), where))
        elif kind == "id":
            if text in KEYWORDS:
                toks.append(("kw", text, where))
            elif text[0].isupper():
                toks.append(("uid", text, where))
            else:
                toks.append(("id", text, where))
        elif kind == "str":
            toks.append(("str", unescape(text[1:-1], where), where))
        elif kind == "chr":
            toks.append(("int", unescape(text[1:-1], where)[0], where))
        elif kind == "op":
            toks.append(("op", text, where))
        line += text.count("\n")
        pos = m.end()
    toks.append(("eof", "", "%s:%d" % (fname, line)))
    return toks


BINARY_LEVELS = [
    ["||"], ["&&"], ["==", "!=", "<", "<=", ">", ">="], ["|"], ["^"], ["&"],
    ["<<", ">>"], ["+", "-"], ["*", "/", "%"],
]


class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.p = 0
        # Struct names are needed up front: `Name {` is a struct literal only
        # when Name is a struct, otherwise the brace opens a block.
        self.structs = set()
        for i in range(len(toks) - 1):
            if toks[i][:2] == ("kw", "struct"):
                self.structs.add(toks[i + 1][1])

    def fail(self, msg):
        raise FlintError("%s: %s" % (self.toks[self.p][2], msg))

    def at(self, kind, val=None):
        t = self.toks[self.p]
        return t[0] == kind and (val is None or t[1] == val)

    def accept(self, kind, val=None):
        if self.at(kind, val):
            self.p += 1
            return True
        return False

    def expect(self, kind, val=None):
        t = self.toks[self.p]
        if not self.at(kind, val):
            self.fail("expected %s, found %r" % (val or kind, t[1]))
        self.p += 1
        return t[1]

    def op(self, val):
        return self.expect("op", val)

    # ---- items ----------------------------------------------------------
    def program(self):
        items = []
        while not self.at("eof"):
            items.append(self.item())
        return items

    def tparams(self):
        if self.accept("op", "<"):
            self.expect("uid")
            while self.accept("op", ","):
                self.expect("uid")
            self.op(">")

    def item(self):
        if self.accept("kw", "fn"):
            name = self.expect("id")
            self.tparams()
            self.op("(")
            params = []
            while not self.at("op", ")"):
                params.append(self.expect("id"))
                if self.accept("op", ":"):
                    self.type()
                if not self.accept("op", ","):
                    break
            self.op(")")
            if self.accept("op", "->"):
                self.type()
            return ("fn", name, params, self.block())
        if self.accept("kw", "struct"):
            name = self.expect("uid")
            self.tparams()
            self.op("{")
            fields = []
            while not self.at("op", "}"):
                fields.append(self.expect("id"))
                self.op(":")
                self.type()
                if not self.accept("op", ","):
                    break
            self.op("}")
            return ("struct", name, fields)
        if self.accept("kw", "type"):
            name = self.expect("uid")
            self.tparams()
            self.op("=")
            self.accept("op", "|")
            ctors = []
            while True:
                cname = self.expect("uid")
                arity = 0
                if self.accept("op", "("):
                    while not self.at("op", ")"):
                        self.type()
                        arity += 1
                        if not self.accept("op", ","):
                            break
                    self.op(")")
                ctors.append((cname, arity))
                if not self.accept("op", "|"):
                    break
            return ("type", name, ctors)
        if self.accept("kw", "const"):
            name = self.expect("uid")
            self.op("=")
            value = self.expr()
            self.op(";")
            return ("const", name, value)
        self.fail("expected an item")

    def type(self):
        if self.accept("op", "["):
            self.type()
            self.op("]")
        elif self.accept("kw", "fn"):
            self.op("(")
            while not self.at("op", ")"):
                self.type()
                if not self.accept("op", ","):
                    break
            self.op(")")
            self.op("->")
            self.type()
        elif self.accept("id"):
            pass
        else:
            self.expect("uid")
            if self.accept("op", "<"):
                self.type()
                while self.accept("op", ","):
                    self.type()
                if self.at("op", ">>"):
                    # split `>>` so Vec<Vec<int>> closes two lists
                    t = self.toks[self.p]
                    self.toks[self.p] = ("op", ">", t[2])
                else:
                    self.op(">")

    # ---- statements -----------------------------------------------------
    def block(self):
        self.op("{")
        stmts = []
        tail = None
        while not self.at("op", "}"):
            if self.accept("kw", "let"):
                name = self.expect("id")
                if self.accept("op", ":"):
                    self.type()
                self.op("=")
                value = self.expr()
                self.op(";")
                stmts.append(("let", name, value))
            elif self.accept("kw", "while"):
                cond = self.expr()
                stmts.append(("while", cond, self.block()))
            elif self.accept("kw", "for"):
                name = self.expect("id")
                self.expect("kw", "in")
                lo = self.expr()
                self.op("..")
                hi = self.expr()
                stmts.append(("for", name, lo, hi, self.block()))
            elif self.accept("kw", "return"):
                value = None if self.at("op", ";") else self.expr()
                self.op(";")
                stmts.append(("return", value))
            elif self.accept("kw", "break"):
                self.op(";")
                stmts.append(("break",))
            elif self.accept("kw", "continue"):
                self.op(";")
                stmts.append(("continue",))
            elif self.at("kw", "if") or self.at("kw", "match") or self.at("op", "{"):
                # A block-like expression at statement start stands alone.
                e = self.primary()
                if self.at("op", "}"):
                    tail = e
                else:
                    self.accept("op", ";")
                    stmts.append(("expr", e))
            else:
                e = self.expr()
                if self.accept("op", "="):
                    rhs = self.expr()
                    self.op(";")
                    stmts.append(("assign", e, rhs))
                elif self.at("op", "+=") or self.at("op", "-="):
                    op = self.toks[self.p][1][0]
                    self.p += 1
                    if e[0] != "var":
                        self.fail("compound assignment needs a plain variable")
                    rhs = self.expr()
                    self.op(";")
                    stmts.append(("assign", e, ("bin", op, e, rhs)))
                elif self.accept("op", ";"):
                    stmts.append(("expr", e))
                elif self.at("op", "}"):
                    tail = e
                else:
                    self.fail("expected ';' or '}'")
        self.op("}")
        return ("block", stmts, tail)

    # ---- expressions ----------------------------------------------------
    def expr(self, level=0):
        if level == len(BINARY_LEVELS):
            return self.unary()
        lhs = self.expr(level + 1)
        while self.at("op") and self.toks[self.p][1] in BINARY_LEVELS[level]:
            op = self.toks[self.p][1]
            self.p += 1
            lhs = ("bin", op, lhs, self.expr(level + 1))
        return lhs

    def unary(self):
        if self.accept("op", "-"):
            return ("un", "-", self.unary())
        if self.accept("op", "!"):
            return ("un", "!", self.unary())
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            if self.accept("op", "("):
                args = []
                while not self.at("op", ")"):
                    args.append(self.expr())
                    if not self.accept("op", ","):
                        break
                self.op(")")
                e = ("call", e, args)
            elif self.accept("op", "["):
                i = self.expr()
                self.op("]")
                e = ("index", e, i)
            elif self.accept("op", "."):
                e = ("field", e, self.expect("id"))
            else:
                return e

    def primary(self):
        t = self.toks[self.p]
        if self.accept("int"):
            return ("int", t[1])
        if self.accept("str"):
            return ("str", t[1])
        if self.accept("kw", "true"):
            return ("bool", True)
        if self.accept("kw", "false"):
            return ("bool", False)
        if self.accept("id"):
            return ("var", t[1])
        if self.accept("uid"):
            name = t[1]
            if name in self.structs and self.at("op", "{"):
                self.op("{")
                inits = []
                while not self.at("op", "}"):
                    fname = self.expect("id")
                    self.op(":")
                    inits.append((fname, self.expr()))
                    if not self.accept("op", ","):
                        break
                self.op("}")
                return ("struct", name, inits)
            args = []
            if self.accept("op", "("):
                while not self.at("op", ")"):
                    args.append(self.expr())
                    if not self.accept("op", ","):
                        break
                self.op(")")
            return ("ctor", name, args)
        if self.accept("op", "("):
            if self.accept("op", ")"):
                return ("unit",)
            e = self.expr()
            self.op(")")
            return e
        if self.accept("kw", "if"):
            cond = self.expr()
            then = self.block()
            other = None
            if self.accept("kw", "else"):
                other = self.primary() if self.at("kw", "if") else self.block()
            return ("if", cond, then, other)
        if self.accept("kw", "match"):
            scrut = self.expr()
            self.op("{")
            arms = []
            while not self.at("op", "}"):
                pat = self.pattern()
                self.op("=>")
                arms.append((pat, self.expr()))
                if not self.accept("op", ","):
                    if not self.at("op", "}") and arms[-1][1][0] not in ("block", "if", "match"):
                        self.fail("expected ',' after match arm")
            self.op("}")
            return ("match", scrut, arms)
        if self.at("op", "{"):
            return self.block()
        self.fail("unexpected %r in expression" % (t[1],))

    def pattern(self):
        t = self.toks[self.p]
        if self.accept("int"):
            return ("pint", t[1])
        if self.accept("op", "-"):
            return ("pint", -self.expect("int"))
        if self.accept("id"):
            return ("pwild",) if t[1] == "_" else ("pvar", t[1])
        name = self.expect("uid")
        subs = []
        if self.accept("op", "("):
            while not self.at("op", ")"):
                subs.append(self.expect("id"))
                if not self.accept("op", ","):
                    break
            self.op(")")
        return ("pctor", name, subs)


WRAP = "(((%s) + 9223372036854775808 & 18446744073709551615) - 9223372036854775808)"
LITERAL_RE = re.compile(r"^(-?[0-9]+|True|False|None|_t[0-9]+)$")
PURE_NODES = {"int", "bool", "str", "var", "unit"}


def pure_local(e):
    """True when evaluating e cannot observe or cause a heap side effect."""
    if e[0] in PURE_NODES:
        return True
    if e[0] == "bin" and e[1] not in ("/", "%"):
        return pure_local(e[2]) and pure_local(e[3])
    if e[0] == "un":
        return pure_local(e[2])
    return False


class Gen:
    """Flint AST -> Python source."""

    def __init__(self, items):
        self.items = items
        self.structs = {}
        self.ctors = {}
        self.consts = {}
        for it in items:
            if it[0] == "struct":
                self.structs[it[1]] = it[2]
            elif it[0] == "type":
                boxed = any(arity for _, arity in it[2])
                for tag, (cname, arity) in enumerate(it[2]):
                    self.ctors[cname] = (tag, arity, boxed)
        self.out = []
        self.ind = 0
        self.ntmp = 0
        self.scopes = []
        self.taken = {}

    # ---- emission helpers -----------------------------------------------
    def emit(self, text):
        self.out.append([self.ind, text])

    def tmp(self):
        self.ntmp += 1
        return "_t%d" % self.ntmp

    def capture(self, fn):
        """Run fn with a fresh statement buffer; return (lines, result)."""
        saved = self.out
        self.out = []
        try:
            res = fn()
            return self.out, res
        finally:
            self.out = saved

    def declare(self, name):
        n = self.taken.get(name, 0)
        self.taken[name] = n + 1
        py = "v_%s" % name if n == 0 else "v_%s_%d" % (name, n)
        self.scopes[-1][name] = py
        return py

    def lookup(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return "f_" + name

    def seq(self, exprs):
        """Compile exprs left to right, keeping that order observable even when a
        later one needs statements of its own."""
        res = []
        marks = []
        for e in exprs:
            start = len(self.out)
            s = self.expr(e)
            if len(self.out) > start:
                shift = 0
                for j in range(len(res)):
                    marks[j] += shift
                    if not LITERAL_RE.match(res[j]):
                        t = self.tmp()
                        self.out.insert(marks[j], [self.ind, "%s = %s" % (t, res[j])])
                        res[j] = t
                        shift += 1
            res.append(s)
            marks.append(len(self.out))
        return res

    def body(self, lines):
        """Splice captured lines in as an indented suite."""
        if not lines:
            lines = [[self.ind, "pass"]]
        for ind, text in lines:
            self.out.append([ind + 1, text])

    # ---- top level ------------------------------------------------------
    def program(self):
        for it in self.items:
            if it[0] == "const":
                self.scopes = [{}]
                self.consts[it[1]] = self.expr(it[2])
        for name, fields in self.structs.items():
            self.emit("class S_%s:" % name)
            self.ind = 1
            self.emit("__slots__ = (%s)" % "".join("'m_%s', " % f for f in fields))
            self.emit("def __init__(self%s):" % "".join(", " + f for f in fields))
            self.ind = 2
            for f in fields:
                self.emit("self.m_%s = %s" % (f, f))
            if not fields:
                self.emit("pass")
            self.ind = 0
        for it in self.items:
            if it[0] == "fn":
                self.function(it)
        return "\n".join("    " * ind + text for ind, text in self.out) + "\n"

    def function(self, it):
        _, name, params, block = it
        self.scopes = [{}]
        self.taken = {}
        self.ntmp = 0
        pys = [self.declare(p) for p in params]
        self.ind = 0
        self.emit("def f_%s(%s):" % (name, ", ".join(pys)))
        self.ind = 1
        v = self.expr(block)
        self.emit("return %s" % v)
        self.ind = 0

    # ---- statements -----------------------------------------------------
    def stmt(self, s):
        k = s[0]
        if k == "let":
            v = self.expr(s[2])
            self.emit("%s = %s" % (self.declare(s[1]), v))
        elif k == "assign":
            self.assign(s[1], s[2])
        elif k == "while":
            lines, c = self.capture(lambda: self.expr(s[1]))
            if lines:
                self.emit("while True:")
                self.body(lines + [[self.ind, "if not (%s): break" % c]])
            else:
                self.emit("while %s:" % c)
            self.body(self.capture(lambda: self.expr(s[2], False))[0])
        elif k == "for":
            lo, hi = self.seq([s[2], s[3]])
            self.scopes.append({})
            self.emit("for %s in range(%s, %s):" % (self.declare(s[1]), lo, hi))
            self.body(self.capture(lambda: self.expr(s[4], False))[0])
            self.scopes.pop()
        elif k == "return":
            self.emit("return %s" % (self.expr(s[1]) if s[1] else "None"))
        elif k == "break":
            self.emit("break")
        elif k == "continue":
            self.emit("continue")
        elif k == "expr":
            v = self.expr(s[1], False)
            if not LITERAL_RE.match(v) and not v.startswith("v_"):
                self.emit(v)

    def assign(self, lhs, rhs):
        if lhs[0] == "var":
            v = self.expr(rhs)
            self.emit("%s = %s" % (self.lookup(lhs[1]), v))
            return
        # Flint evaluates the target's operands before the right-hand side;
        # Python does the opposite, so pin anything that could tell.
        parts = [lhs[1]] if lhs[0] == "field" else [lhs[1], lhs[2]]
        vals = self.seq(parts + [rhs])
        for j, part in enumerate(parts):
            if not pure_local(part) and not LITERAL_RE.match(vals[j]) and not pure_local(rhs):
                t = self.tmp()
                self.out.insert(len(self.out), [self.ind, "%s = %s" % (t, vals[j])])
                vals[j] = t
        if lhs[0] == "field":
            self.emit("%s.m_%s = %s" % (vals[0], lhs[2], vals[1]))
        else:
            self.emit("%s[%s] = %s" % (vals[0], vals[1], vals[2]))

    # ---- expressions ----------------------------------------------------
    def expr(self, e, want=True):
        k = e[0]
        if k == "int":
            return str(e[1])
        if k == "str":
            return repr(e[1])
        if k == "bool":
            return "True" if e[1] else "False"
        if k == "unit":
            return "None"
        if k == "var":
            return self.lookup(e[1])
        if k == "un":
            v = self.expr(e[2])
            return "(not %s)" % v if e[1] == "!" else WRAP % ("-" + v)
        if k == "bin":
            return self.binary(e)
        if k == "call":
            vals = self.seq([e[1]] + e[2])
            return "%s(%s)" % (vals[0], ", ".join(vals[1:]))
        if k == "index":
            a, i = self.seq([e[1], e[2]])
            return "%s[%s]" % (a, i)
        if k == "field":
            return "%s.m_%s" % (self.expr(e[1]), e[2])
        if k == "ctor":
            if e[1] in self.consts:
                return self.consts[e[1]]
            if e[1] not in self.ctors:
                raise FlintError("flint0: unknown constructor '%s'" % e[1])
            tag, arity, boxed = self.ctors[e[1]]
            if not boxed:
                return str(tag)
            vals = self.seq(e[2])
            return "(%s,)" % ", ".join([str(tag)] + vals)
        if k == "struct":
            fields = self.structs[e[1]]
            vals = self.seq([v for _, v in e[2]])
            given = [f for f, _ in e[2]]
            if given != fields:
                # initialisers run in source order, the constructor takes field order
                pinned = []
                for v in vals:
                    if LITERAL_RE.match(v):
                        pinned.append(v)
                    else:
                        t = self.tmp()
                        self.emit("%s = %s" % (t, v))
                        pinned.append(t)
                vals = [pinned[given.index(f)] for f in fields]
            return "S_%s(%s)" % (e[1], ", ".join(vals))
        if k == "block":
            self.scopes.append({})
            for s in e[1]:
                self.stmt(s)
            v = "None"
            if e[2] is not None:
                v = self.expr(e[2], want)
            self.scopes.pop()
            return v
        if k == "if":
            return self.if_(e, want)
        if k == "match":
            return self.match(e, want)
        raise FlintError("flint0: cannot translate %s" % k)

    def binary(self, e):
        op = e[1]
        if op in ("&&", "||"):
            a = self.expr(e[2])
            lines, b = self.capture(lambda: self.expr(e[3]))
            if not lines:
                return "(%s %s %s)" % (a, "and" if op == "&&" else "or", b)
            t = self.tmp()
            self.emit("%s = %s" % (t, a))
            self.emit("if %s:" % (t if op == "&&" else "not " + t))
            self.body(lines + [[self.ind, "%s = %s" % (t, b)]])
            return t
        a, b = self.seq([e[2], e[3]])
        if op in ("+", "-", "*", "<<"):
            return WRAP % ("%s %s %s" % (a, op, b))
        if op == "/":
            return "_div(%s, %s)" % (a, b)
        if op == "%":
            return "_mod(%s, %s)" % (a, b)
        return "(%s %s %s)" % (a, op, b)

    def branch(self, e, want, t):
        def run():
            v = self.expr(e, want)
            if want:
                self.emit("%s = %s" % (t, v))
            elif not LITERAL_RE.match(v) and not v.startswith("v_"):
                self.emit(v)
        return self.capture(run)[0]

    def if_(self, e, want):
        c = self.expr(e[1])
        t = self.tmp() if want else None
        self.emit("if %s:" % c)
        self.body(self.branch(e[2], want, t))
        if e[3] is not None:
            self.emit("else:")
            self.body(self.branch(e[3], want, t))
        elif want:
            self.emit("else:")
            self.body([[self.ind, "%s = None" % t]])
        return t if want else "None"

    def match(self, e, want):
        m = self.tmp()
        self.emit("%s = %s" % (m, self.expr(e[1])))
        t = self.tmp() if want else None
        arms = e[2]
        for i, (pat, body) in enumerate(arms):
            self.scopes.append({})
            binds = []
            cond = None
            if pat[0] == "pint":
                cond = "%s == %d" % (m, pat[1])
            elif pat[0] == "pvar":
                binds.append("%s = %s" % (self.declare(pat[1]), m))
            elif pat[0] == "pctor":
                if pat[1] in self.consts:
                    cond = "%s == %s" % (m, self.consts[pat[1]])
                else:
                    if pat[1] not in self.ctors:
                        raise FlintError("flint0: unknown constructor '%s'" % pat[1])
                    tag, arity, boxed = self.ctors[pat[1]]
                    cond = "%s[0] == %d" % (m, tag) if boxed else "%s == %d" % (m, tag)
                    for j, sub in enumerate(pat[2]):
                        if sub != "_":
                            binds.append("%s = %s[%d]" % (self.declare(sub), m, j + 1))
            if i == len(arms) - 1 and i > 0:
                self.emit("else:")
            elif cond is None:
                self.emit("if True:" if i == 0 else "else:")
            else:
                self.emit("%s %s:" % ("if" if i == 0 else "elif", cond))
            lines = [[self.ind, b] for b in binds] + self.branch(body, want, t)
            self.body(lines)
            self.scopes.pop()
            if cond is None:
                break
        return t if want else "None"


# ---- runtime ------------------------------------------------------------
def make_runtime(argv):
    out = sys.stdout.buffer
    err = sys.stderr.buffer

    def _div(a, b):
        q = abs(a) // abs(b)
        return q if (a < 0) == (b < 0) else -q

    def _mod(a, b):
        return a - b * _div(a, b)

    def f_exit(code):
        out.flush()
        err.flush()
        sys.exit(code)

    def f_read_file(path):
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            err.write(b"cannot read " + path + b"\n")
            f_exit(1)

    def f_write_file(path, data, mode):
        with open(path, "wb") as f:
            f.write(data)
        os.chmod(path, mode)

    def f_print(s):
        out.write(s)

    def f_eprint(s):
        err.write(s)

    return {
        "_div": _div,
        "_mod": _mod,
        "f_exit": f_exit,
        "f_read_file": f_read_file,
        "f_write_file": f_write_file,
        "f_print": f_print,
        "f_eprint": f_eprint,
        "f_len": len,
        "f_str_len": len,
        "f_char_at": lambda s, i: s[i],
        "f_str_concat": lambda a, b: a + b,
        "f_str_eq": lambda a, b: a == b,
        "f_substr": lambda s, start, n: s[start:start + n],
        "f_str_from_array": lambda a, n: bytes(a[:n]),
        "f_array_new": lambda n, x: [x] * n,
        "f_array_alloc": lambda n: [0] * n,
        "f_heap_used": lambda: 0,
        "f_arg_count": lambda: len(argv),
        "f_arg": lambda i: argv[i],
    }


def translate(paths):
    toks = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            toks += lex(f.read(), path)[:-1]
    toks.append(("eof", "", "<end>"))
    return Gen(Parser(toks).program()).program()


def main():
    args = sys.argv[1:]
    prog_args = []
    if "--" in args:
        cut = args.index("--")
        args, prog_args = args[:cut], args[cut + 1:]
    emit_only = "--emit-python" in args
    paths = [a for a in args if a != "--emit-python"]
    if not paths:
        sys.stderr.write(__doc__)
        return 2
    try:
        source = translate(paths)
    except FlintError as e:
        sys.stderr.write("%s\n" % e)
        return 1
    if emit_only:
        sys.stdout.write(source)
        return 0
    env = make_runtime([os.fsencode(paths[-1])] + [os.fsencode(a) for a in prog_args])
    exec(compile(source, "<flint>", "exec"), env)
    env["f_main"]()
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    import threading

    sys.setrecursionlimit(1000000)
    threading.stack_size(512 * 1024 * 1024)
    result = []

    def run():
        try:
            result.append(main())
        except SystemExit as e:
            result.append(e.code)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join()
    sys.exit(result[0] if result else 1)
