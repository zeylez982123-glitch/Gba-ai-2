"""Minimal DEX reader: per method, list string constants, field refs and invoked methods, in order."""
import struct, sys

data = open(sys.argv[1], 'rb').read()
u4 = lambda o: struct.unpack_from('<I', data, o)[0]
u2 = lambda o: struct.unpack_from('<H', data, o)[0]

def uleb(o):
    r = s = 0
    while True:
        b = data[o]; o += 1; r |= (b & 0x7f) << s; s += 7
        if b < 0x80: return r, o

def sleb_skip(o):
    while data[o] & 0x80: o += 1
    return o + 1

(sid_n, sid_o, tid_n, tid_o, pid_n, pid_o, fid_n, fid_o, mid_n, mid_o, cd_n, cd_o) = struct.unpack_from('<12I', data, 0x38)

def string(i):
    off = u4(sid_o + 4 * i); _, o = uleb(off); e = data.index(b'\0', o)
    return data[o:e].decode('utf-8', 'replace')
typ = lambda i: string(u4(tid_o + 4 * i))
def field(i):
    c, t, n = struct.unpack_from('<HHI', data, fid_o + 8 * i); return typ(c) + '->' + string(n)
def meth(i):
    c, p, n = struct.unpack_from('<HHI', data, mid_o + 8 * i); return typ(c) + '->' + string(n)

# instruction size in 16-bit units by opcode
W = [1] * 256
def setw(rng, w):
    for o in rng: W[o] = w
setw([0x02, 0x05, 0x08, 0x13, 0x15, 0x16, 0x19, 0x1a, 0x1c, 0x1f, 0x20, 0x22, 0x23, 0x29, 0x2d, 0x2e, 0x2f, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x3b, 0x3c, 0x3d], 2)
setw(range(0x44, 0x6e), 2); setw(range(0x90, 0xb0), 2); setw(range(0xd0, 0xe3), 2)
setw([0x03, 0x06, 0x09, 0x14, 0x17, 0x1b, 0x24, 0x25, 0x26, 0x2a, 0x2b, 0x2c], 3)
setw(range(0x6e, 0x73), 3); setw(range(0x74, 0x79), 3); setw([0xfc, 0xfd, 0xfe, 0xff], 3)
setw([0xfa, 0xfb], 4); W[0x18] = 5

def code(off):
    ins_n = u4(off + 12); o = off + 16; end = o + 2 * ins_n; outl = []
    while o < end:
        unit = u2(o); op = unit & 0xff
        if op == 0 and unit != 0:              # payload pseudo-instructions
            if unit == 0x100: size = u2(o + 2); o += (4 + size * 2) * 2; continue
            if unit == 0x200: size = u2(o + 2); o += (2 + size * 4) * 2; continue
            if unit == 0x300: ew = u2(o + 2); n = u4(o + 4); o += ((ew * n + 1) // 2 + 4) * 2; continue
        if op == 0x1a: outl.append('"' + string(u2(o + 2)) + '"')
        elif op == 0x1b: outl.append('"' + string(u4(o + 2)) + '"')
        elif 0x6e <= op <= 0x78 and op != 0x73: outl.append('call ' + meth(u2(o + 2)))
        elif 0x52 <= op <= 0x6d: outl.append('field ' + field(u2(o + 2)))
        elif op == 0x22: outl.append('new ' + typ(u2(o + 2)))
        elif op in (0x2b, 0x2c): pass
        o += W[op] * 2
    return outl

for c in range(cd_n):
    base = cd_o + 32 * c
    cls = typ(u4(base)); cdata = u4(base + 24)
    if not cdata or not cls.startswith('Lapp/'): continue
    print('\n=== class', cls)
    o = cdata
    sf, o = uleb(o); inf, o = uleb(o); dm, o = uleb(o); vm, o = uleb(o)
    idx = 0
    for _ in range(sf + inf):
        d, o = uleb(o); idx += d; _, o = uleb(o)
        print('  field', field(idx))
    for count in (dm, vm):
        idx = 0
        for _ in range(count):
            d, o = uleb(o); idx += d; acc, o = uleb(o); co, o = uleb(o)
            print('  method', meth(idx).split('->')[1], hex(acc))
            if co:
                for line in code(co): print('     ', line)
