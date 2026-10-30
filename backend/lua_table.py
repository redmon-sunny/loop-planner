"""Minimal parser for WoW SavedVariables files (Lua table literals) -> Python objects.

Handles: nested tables, [key]= / name= / positional entries, strings with escapes,
numbers (int/float/hex), true/false/nil, -- comments and --[[ ]] block comments.
Does NOT execute Lua; it only reads literals, which is all SavedVariables contain.

Usage:
    from lua_table import load_saved_variables
    vars = load_saved_variables("Auctionator.lua")   # {"AUCTIONATOR_PRICE_DATABASE": {...}, ...}

Tables become dicts. A table whose keys are 1..n becomes a list.
"""
import re

_NUM = re.compile(r'-?(0[xX][0-9a-fA-F]+|\d+\.?\d*([eE][-+]?\d+)?|\.\d+([eE][-+]?\d+)?)')
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


class LuaParseError(ValueError):
    pass


class _P:
    def __init__(self, s):
        self.s = s
        self.i = 0
        self.n = len(s)

    def _ws(self):
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c in ' \t\r\n':
                self.i += 1
            elif s.startswith('--', self.i):
                if s.startswith('--[[', self.i):
                    j = s.find(']]', self.i + 4)
                    self.i = n if j < 0 else j + 2
                else:
                    j = s.find('\n', self.i)
                    self.i = n if j < 0 else j + 1
            else:
                break

    def _peek(self):
        self._ws()
        return self.s[self.i] if self.i < self.n else ''

    def _expect(self, ch):
        self._ws()
        if not self.s.startswith(ch, self.i):
            raise LuaParseError(f"expected {ch!r} at {self.i}: {self.s[self.i:self.i+40]!r}")
        self.i += len(ch)

    def value(self):
        c = self._peek()
        if c == '{':
            return self.table()
        if c == '"' or c == "'":
            return self.string(c)
        if self.s.startswith('[[', self.i):
            j = self.s.find(']]', self.i + 2)
            if j < 0:
                raise LuaParseError('unterminated long string')
            v = self.s[self.i + 2:j]
            self.i = j + 2
            return v
        m = _NUM.match(self.s, self.i)
        if m and m.group(0) not in ('-', ''):
            tok = m.group(0)
            self.i = m.end()
            if tok.lower().startswith(('0x', '-0x')):
                return int(tok, 16)
            try:
                return int(tok)
            except ValueError:
                return float(tok)
        m = _NAME.match(self.s, self.i)
        if m:
            tok = m.group(0)
            self.i = m.end()
            if tok == 'true':
                return True
            if tok == 'false':
                return False
            if tok == 'nil':
                return None
            raise LuaParseError(f'unexpected identifier {tok!r} at {self.i}')
        raise LuaParseError(f'unexpected char {c!r} at {self.i}')

    def string(self, q):
        self._expect(q)
        out = []
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c == '\\':
                nxt = s[self.i + 1]
                esc = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '"': '"', "'": "'", 'a': '\a', 'b': '\b', 'f': '\f', 'v': '\v', '\n': '\n'}
                if nxt in esc:
                    out.append(esc[nxt]); self.i += 2
                elif nxt.isdigit():
                    m = re.match(r'\d{1,3}', s[self.i + 1:self.i + 4])
                    out.append(chr(int(m.group(0)))); self.i += 1 + len(m.group(0))
                else:
                    out.append(nxt); self.i += 2
            elif c == q:
                self.i += 1
                return ''.join(out)
            else:
                out.append(c); self.i += 1
        raise LuaParseError('unterminated string')

    def table(self):
        self._expect('{')
        d = {}
        pos = 1
        while True:
            c = self._peek()
            if c == '}':
                self.i += 1
                break
            if c == '[':
                self.i += 1
                k = self.value()
                self._expect(']')
                self._expect('=')
                v = self.value()
            else:
                m = _NAME.match(self.s, self.i)
                save = self.i
                if m:
                    self.i = m.end()
                    if self._peek() == '=' and not self.s.startswith('==', self.i):
                        self.i += 1
                        k = m.group(0)
                        v = self.value()
                    else:
                        self.i = save
                        k = pos; pos += 1
                        v = self.value()
                else:
                    k = pos; pos += 1
                    v = self.value()
            if v is not None:
                d[k] = v
            c = self._peek()
            if c in (',', ';'):
                self.i += 1
        # 1..n integer keys -> list
        if d and all(isinstance(k, int) for k in d) and sorted(d) == list(range(1, len(d) + 1)):
            return [d[i] for i in range(1, len(d) + 1)]
        return d


def parse_lua_value(text):
    p = _P(text)
    v = p.value()
    return v


def load_saved_variables(path):
    """Parse a SavedVariables .lua file: a sequence of `NAME = <value>` statements."""
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    p = _P(text)
    out = {}
    while True:
        p._ws()
        if p.i >= p.n:
            break
        m = _NAME.match(text, p.i)
        if not m:
            raise LuaParseError(f'expected a variable name at {p.i}: {text[p.i:p.i+40]!r}')
        name = m.group(0)
        p.i = m.end()
        p._expect('=')
        out[name] = p.value()
    return out


if __name__ == '__main__':
    import json, sys
    if len(sys.argv) < 2:
        print('usage: lua_table.py <SavedVariables.lua>  # dumps JSON to stdout', file=sys.stderr)
        sys.exit(2)
    json.dump(load_saved_variables(sys.argv[1]), sys.stdout, indent=1, default=str)
