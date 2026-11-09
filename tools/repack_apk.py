"""Put a new assets/index.html into the Jarvis APK and sign it again (v1 JAR signature + APK Signature Scheme v2).

Usage:
  python3 repack_apk.py BASE.apk NEW_index.html OUT.apk --keystore KS --alias ALIAS --storepass PASS \
      [--version-code N --version-name X.Y]

Steps: copy every entry except the old signature (same compression), swap in the page, optionally raise the
version in the binary manifest, sign v1 with the JDK's jarsigner, 4-byte-align uncompressed entries (like
zipalign), then add the v2 signing block (RSA PKCS#1 v1.5 + SHA-256, algorithm 0x0103).
"""
import argparse, hashlib, os, struct, subprocess, sys, tempfile, zipfile
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12

# ---------- binary manifest: version code (int attribute) and version name (same-length string) ----------
def set_version(axml, code, name):
    d = bytearray(axml)
    count, _, flags, str_off, _ = struct.unpack_from('<IIIII', d, 16)
    assert not flags & 0x100, 'expected a UTF-16 string pool'
    offs = [struct.unpack_from('<I', d, 36 + 4 * i)[0] for i in range(count)]
    base = 8 + str_off
    def get(i):
        o = base + offs[i]; n = struct.unpack_from('<H', d, o)[0]
        return d[o + 2:o + 2 + 2 * n].decode('utf-16le')
    names = [get(i) for i in range(count)]
    o = 8 + struct.unpack_from('<I', d, 12)[0]              # the chunks after the string pool, one after another
    done = set()
    while o < len(d):
        typ, hsz, sz = struct.unpack_from('<HHI', d, o)
        if typ == 0x102 and names[struct.unpack_from('<I', d, o + 20)[0]] == 'manifest':
            ast, asz, ac = struct.unpack_from('<HHH', d, o + 24)
            for k in range(ac):
                a = o + 16 + ast + k * asz
                an = names[struct.unpack_from('<I', d, a + 4)[0]]
                raw = struct.unpack_from('<I', d, a + 8)[0]
                if an == 'versionCode' and code is not None:
                    struct.pack_into('<I', d, a + 16, code); done.add('code')
                if an == 'versionName' and name is not None:
                    old = names[raw]
                    assert len(name) == len(old), 'version name must keep its length (%r)' % old
                    so = base + offs[raw] + 2
                    d[so:so + 2 * len(name)] = name.encode('utf-16le'); done.add('name')
        o += sz
    assert done == {x for x, v in (('code', code), ('name', name)) if v is not None}, done
    return bytes(d)

# ---------- 4-byte alignment of uncompressed entries (what zipalign -p 4 does) ----------
def align(src, dst):
    d = open(src, 'rb').read()
    eocd = d.rfind(b'PK\x05\x06')
    n = struct.unpack_from('<H', d, eocd + 10)[0]; cd_off = struct.unpack_from('<I', d, eocd + 16)[0]
    out, cd, p = bytearray(), bytearray(), cd_off
    for _ in range(n):
        assert d[p:p + 4] == b'PK\x01\x02'
        method = struct.unpack_from('<H', d, p + 10)[0]; csize = struct.unpack_from('<I', d, p + 20)[0]
        fl, xl, cl = struct.unpack_from('<HHH', d, p + 28); lho = struct.unpack_from('<I', d, p + 42)[0]
        entry = bytearray(d[p:p + 46 + fl + xl + cl]); p += 46 + fl + xl + cl
        lfl, lxl = struct.unpack_from('<HH', d, lho + 26)
        flags = struct.unpack_from('<H', d, lho + 6)[0]
        name = d[lho + 30:lho + 30 + lfl]
        data = d[lho + 30 + lfl + lxl:lho + 30 + lfl + lxl + csize]
        extra = b''                                           # drop old extra fields, pad stored entries
        if method == 0:
            pad = (4 - (len(out) + 30 + lfl) % 4) % 4
            extra = b'\0' * pad
        hdr = bytearray(d[lho:lho + 30]); struct.pack_into('<H', hdr, 28, len(extra))
        struct.pack_into('<I', entry, 42, len(out))
        out += hdr + name + extra + data
        if flags & 0x08:                                      # data descriptor follows the data
            q = lho + 30 + lfl + lxl + csize
            dl = 16 if d[q:q + 4] == b'PK\x07\x08' else 12
            out += d[q:q + dl]
        cd += entry
    new_cd = len(out)
    tail = bytearray(d[eocd:]); struct.pack_into('<I', tail, 16, new_cd); struct.pack_into('<I', tail, 12, len(cd))
    open(dst, 'wb').write(bytes(out + cd + tail))

# ---------- APK Signature Scheme v2 ----------
lp = lambda b: struct.pack('<I', len(b)) + b                  # length-prefixed
def sections(d):
    eocd = d.rfind(b'PK\x05\x06')
    cd_off = struct.unpack_from('<I', d, eocd + 16)[0]
    return d[:cd_off], d[cd_off:eocd], d[eocd:], cd_off
def v2_digest(entries, cd, eocd_with_offset):
    chunks = []
    for part in (entries, cd, eocd_with_offset):
        for i in range(0, len(part), 1 << 20):
            c = part[i:i + (1 << 20)]
            chunks.append(hashlib.sha256(b'\xa5' + struct.pack('<I', len(c)) + c).digest())
    return hashlib.sha256(b'\x5a' + struct.pack('<I', len(chunks)) + b''.join(chunks)).digest()
def v2_sign(src, dst, key, cert_der):
    d = open(src, 'rb').read()
    entries, cd, eocd, cd_off = sections(d)
    digest = v2_digest(entries, cd, eocd)                     # before the block, the EOCD already points at cd_off
    ALG = 0x0103
    signed = lp(lp(struct.pack('<I', ALG) + lp(digest))) + lp(lp(cert_der)) + lp(b'')
    sig = key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    pub = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    signer = lp(signed) + lp(lp(struct.pack('<I', ALG) + lp(sig))) + lp(pub)
    value = lp(lp(signer))
    pair = struct.pack('<Q', 4 + len(value)) + struct.pack('<I', 0x7109871a) + value
    size = len(pair) + 8 + 16
    block = struct.pack('<Q', size) + pair + struct.pack('<Q', size) + b'APK Sig Block 42'
    new_eocd = bytearray(eocd); struct.pack_into('<I', new_eocd, 16, cd_off + len(block))
    open(dst, 'wb').write(entries + block + cd + bytes(new_eocd))

def v2_verify(path):
    """Independent check of the result: parse the block back, recompute the digest, verify the signature."""
    from cryptography import x509
    d = open(path, 'rb').read()
    eocd = d.rfind(b'PK\x05\x06'); cd_off = struct.unpack_from('<I', d, eocd + 16)[0]
    assert d[cd_off - 16:cd_off] == b'APK Sig Block 42', 'no signing block'
    size = struct.unpack_from('<Q', d, cd_off - 24)[0]; start = cd_off - size - 8
    assert struct.unpack_from('<Q', d, start)[0] == size
    p, end, v2 = start + 8, cd_off - 24, None
    while p < end:
        ln, pid = struct.unpack_from('<QI', d, p)
        if pid == 0x7109871a: v2 = d[p + 12:p + 8 + ln]
        p += 8 + ln
    assert v2 is not None, 'no v2 signature'
    rd = lambda b, o: (b[o + 4:o + 4 + struct.unpack_from('<I', b, o)[0]], o + 4 + struct.unpack_from('<I', b, o)[0])
    signers, _ = rd(v2, 0); signer, _ = rd(signers, 0)
    signed, o = rd(signer, 0); sigs, o = rd(signer, o); pub, o = rd(signer, o)
    digests, so = rd(signed, 0); certs, so = rd(signed, so)
    dg, _ = rd(digests, 0); alg = struct.unpack_from('<I', dg, 0)[0]; want, _ = rd(dg, 4)
    s1, _ = rd(sigs, 0); sig, _ = rd(s1, 4)
    cert = x509.load_der_x509_certificate(rd(certs, 0)[0])
    cert.public_key().verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
    assert cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) == pub
    e = bytearray(d[eocd:]); struct.pack_into('<I', e, 16, start)
    assert v2_digest(d[:start], d[cd_off:eocd], bytes(e)) == want, 'v2 digest mismatch'
    for i in zipfile.ZipFile(path).infolist():                # stored entries aligned
        if i.compress_type == 0:
            lfl, lxl = struct.unpack_from('<HH', d, i.header_offset + 26)
            assert (i.header_offset + 30 + lfl + lxl) % 4 == 0, 'unaligned ' + i.filename
    return 'v2 ok, alg 0x%04x, signer %s' % (alg, cert.subject.rfc4514_string())

def main():
    a = argparse.ArgumentParser()
    a.add_argument('base'); a.add_argument('page'); a.add_argument('out')
    a.add_argument('--keystore', required=True); a.add_argument('--alias', required=True); a.add_argument('--storepass', required=True)
    a.add_argument('--version-code', type=int); a.add_argument('--version-name')
    o = a.parse_args()
    tmp = tempfile.mkdtemp()
    unsigned, v1, aligned = (os.path.join(tmp, n) for n in ('unsigned.apk', 'v1.apk', 'aligned.apk'))
    page = open(o.page, 'rb').read()
    with zipfile.ZipFile(o.base) as zin, zipfile.ZipFile(unsigned, 'w') as zout:
        for i in zin.infolist():
            if i.filename.startswith('META-INF/'): continue
            data = zin.read(i)
            if i.filename == 'assets/index.html': data = page
            if i.filename == 'AndroidManifest.xml' and (o.version_code or o.version_name):
                data = set_version(data, o.version_code, o.version_name)
            zi = zipfile.ZipInfo(i.filename, date_time=(1980, 1, 1, 0, 0, 0)); zi.compress_type = i.compress_type
            zout.writestr(zi, data)
    subprocess.run(['jarsigner', '-keystore', o.keystore, '-storepass', o.storepass, '-sigalg', 'SHA256withRSA',
                    '-digestalg', 'SHA-256', '-signedjar', v1, unsigned, o.alias], check=True, stdout=subprocess.DEVNULL)
    align(v1, aligned)
    ks = pkcs12.load_key_and_certificates(open(o.keystore, 'rb').read(), o.storepass.encode())
    v2_sign(aligned, o.out, ks[0], ks[1].public_bytes(serialization.Encoding.DER))
    print(v2_verify(o.out))

if __name__ == '__main__':
    main()
