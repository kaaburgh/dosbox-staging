# SPDX-License-Identifier: GPL-2.0-or-later
"""Obtain and verify the Quake 1.06 shareware files used by the benchmark.

Sources, in order of preference:
  1. --quake-dir: a directory with QUAKE.EXE, CWSDPMI.EXE and ID1/PAK0.PAK;
  2. --quake-zip: the official shareware archive quake106.zip;
  3. download of quake106.zip from a public id Software mirror.

The archive holds resource.1, a self-extracting LHA archive (-lh5-). It is
unpacked with a small pure-Python LH5 decoder below (no external tools
needed). Every extracted file is checked against its SHA-256; nothing is used
unless all three match. The game files stay in the private work directory and
are never copied into results.
"""
from __future__ import annotations

import hashlib
import io
import shutil
import struct
import urllib.request
import zipfile
from pathlib import Path

QUAKE_ZIP_URLS = [
    'https://ftp.gwdg.de/pub/misc/ftp.idsoftware.com/idstuff/quake/quake106.zip',
    'https://ftp.fau.de/idsoftware/idstuff/quake/quake106.zip',
]
QUAKE_ZIP_SHA256 = 'ec6c9d34b1ae0252ac0066045b6611a7919c2a0d78a3a66d9387a8f597553239'
FILES = {  # name in game dir -> (name inside resource.1, sha256, size)
    'QUAKE.EXE': ('quake.exe', '7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb', None),
    'CWSDPMI.EXE': ('cwsdpmi.exe', '0a890815df6cbdc052915a856c16944cedec825267e7b6e453c351a0cdc695f2', 25920),
    'ID1/PAK0.PAK': ('id1/pak0.pak', '35a9c55e5e5a284a159ad2a62e0e8def23d829561fe2f54eb402dbc0a9a946af', 18689235),
}


class QuakeFilesError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# LHA archive reading and -lh5- decoding (after LHA/ar002 by H. Okumura)
# --------------------------------------------------------------------------
class _Bits:
    def __init__(self, data: bytes):
        self.data = data + b'\0\0\0\0'
        self.pos = 0  # bit position

    def peek16(self) -> int:
        b = self.pos >> 3
        d = self.data
        return (((d[b] << 16) | (d[b + 1] << 8) | d[b + 2]) >> (8 - (self.pos & 7))) & 0xFFFF

    def skip(self, n: int) -> None:
        self.pos += n

    def get(self, n: int) -> int:
        if n == 0:
            return 0
        v = self.peek16() >> (16 - n)
        self.pos += n
        return v


def _make_table(nchar, bitlen, tablebits, table, left, right):
    count = [0] * 17
    for i in range(nchar):
        count[bitlen[i]] += 1
    start = [0] * 18
    for i in range(1, 17):
        start[i + 1] = start[i] + (count[i] << (16 - i))
    if start[17] != 1 << 16:
        raise QuakeFilesError('corrupt LHA Huffman table')
    jutbits = 16 - tablebits
    weight = [0] * 17
    for i in range(1, tablebits + 1):
        start[i] >>= jutbits
        weight[i] = 1 << (tablebits - i)
    for i in range(tablebits + 1, 17):
        weight[i] = 1 << (16 - i)
    i = start[tablebits + 1] >> jutbits
    k = 1 << tablebits
    while i < k:
        table[i] = 0
        i += 1
    avail = nchar
    mask = 1 << (15 - tablebits)
    for ch in range(nchar):
        ln = bitlen[ch]
        if ln == 0:
            continue
        nextcode = start[ln] + weight[ln]
        if ln <= tablebits:
            for i in range(start[ln], nextcode):
                table[i] = ch
        else:
            k = start[ln]
            # walk/extend the tree; (arr, idx) emulates the C pointer
            arr, idx = table, k >> jutbits
            i = ln - tablebits
            while i:
                if arr[idx] == 0:
                    right[avail] = left[avail] = 0
                    arr[idx] = avail
                    avail += 1
                node = arr[idx]
                arr, idx = (right, node) if k & mask else (left, node)
                k <<= 1
                i -= 1
            arr[idx] = ch
        start[ln] = nextcode


def lh5_decode(data: bytes, origsize: int) -> bytes:
    NC, NP, NT, CBIT, PBIT, TBIT = 510, 14, 19, 9, 4, 5
    bits = _Bits(data)
    left = [0] * (2 * NC - 1)
    right = [0] * (2 * NC - 1)
    c_len = [0] * NC
    c_table = [0] * 4096
    pt_len = [0] * NT
    pt_table = [0] * 256

    def read_pt_len(nn, nbit, i_special):
        n = bits.get(nbit)
        if n == 0:
            c = bits.get(nbit)
            for i in range(nn):
                pt_len[i] = 0
            for i in range(256):
                pt_table[i] = c
            return
        i = 0
        while i < n:
            c = bits.peek16() >> 13
            if c == 7:
                mask = 1 << 12
                buf = bits.peek16()
                while mask & buf:
                    mask >>= 1
                    c += 1
            bits.skip(3 if c < 7 else c - 3)
            pt_len[i] = c
            i += 1
            if i == i_special:
                c = bits.get(2)
                while c > 0:
                    pt_len[i] = 0
                    i += 1
                    c -= 1
        while i < nn:
            pt_len[i] = 0
            i += 1
        _make_table(nn, pt_len, 8, pt_table, left, right)

    def read_c_len():
        n = bits.get(CBIT)
        if n == 0:
            c = bits.get(CBIT)
            for i in range(NC):
                c_len[i] = 0
            for i in range(4096):
                c_table[i] = c
            return
        i = 0
        while i < n:
            buf = bits.peek16()
            c = pt_table[buf >> 8]
            if c >= NT:
                mask = 1 << 7
                while c >= NT:
                    c = right[c] if buf & mask else left[c]
                    mask >>= 1
            bits.skip(pt_len[c])
            if c <= 2:
                if c == 0:
                    c = 1
                elif c == 1:
                    c = bits.get(4) + 3
                else:
                    c = bits.get(CBIT) + 20
                while c > 0:
                    c_len[i] = 0
                    i += 1
                    c -= 1
            else:
                c_len[i] = c - 2
                i += 1
        while i < NC:
            c_len[i] = 0
            i += 1
        _make_table(NC, c_len, 12, c_table, left, right)

    out = bytearray()
    blocksize = 0
    while len(out) < origsize:
        if blocksize == 0:
            blocksize = bits.get(16)
            read_pt_len(NT, TBIT, 3)
            read_c_len()
            read_pt_len(NP, PBIT, -1)
        blocksize -= 1
        buf = bits.peek16()
        j = c_table[buf >> 4]
        if j >= NC:
            mask = 1 << 3
            while j >= NC:
                j = right[j] if buf & mask else left[j]
                mask >>= 1
        bits.skip(c_len[j])
        if j < 256:
            out.append(j)
            continue
        length = j - 253
        buf = bits.peek16()
        p = pt_table[buf >> 8]
        if p >= NP:
            mask = 1 << 7
            while p >= NP:
                p = right[p] if buf & mask else left[p]
                mask >>= 1
        bits.skip(pt_len[p])
        if p != 0:
            p = (1 << (p - 1)) + bits.get(p - 1)
        src = len(out) - p - 1
        if src < 0:
            raise QuakeFilesError('corrupt LH5 stream')
        if p + 1 >= length:
            out += out[src:src + length]
        else:
            for k in range(length):
                out.append(out[src + k])
    return bytes(out[:origsize])


def lha_members(buf: bytes):
    """Yield (name, method, packed, original, data_offset) for level 0/1/2
    headers; the archive may follow a DOS SFX stub."""
    pos = buf.find(b'-lh')
    while pos >= 2 and not (buf[pos + 4:pos + 5] == b'-' and buf[pos - 2] != 0
                            and buf[pos + 18] in (0, 1, 2)):
        pos = buf.find(b'-lh', pos + 1)
    if pos < 2:
        raise QuakeFilesError('no LHA header found')
    pos -= 2
    while pos + 22 <= len(buf) and buf[pos] != 0:
        method = buf[pos + 2:pos + 7].decode('latin1')
        packed, orig = struct.unpack_from('<II', buf, pos + 7)
        level = buf[pos + 20]
        dirname = ''
        if level in (0, 1):
            hsize = buf[pos]
            nlen = buf[pos + 21]
            name = buf[pos + 22:pos + 22 + nlen].decode('latin1')
            data = pos + 2 + hsize
            if level == 1:
                nxt = struct.unpack_from('<H', buf, data - 2)[0]
                while nxt:
                    ext = buf[data:data + nxt]
                    if ext[0] == 1:
                        name = ext[1:nxt - 2].decode('latin1')
                    elif ext[0] == 2:
                        dirname = ext[1:nxt - 2].replace(b'\xff', b'/').decode('latin1')
                    packed -= nxt
                    data += nxt
                    nxt = struct.unpack_from('<H', buf, data - 2)[0]
        elif level == 2:
            hsize = struct.unpack_from('<H', buf, pos)[0]
            name = ''
            p = pos + 24
            nxt = struct.unpack_from('<H', buf, p)[0]
            p += 2
            while nxt:
                ext = buf[p:p + nxt]
                if ext[0] == 1:
                    name = ext[1:nxt - 2].decode('latin1')
                elif ext[0] == 2:
                    dirname = ext[1:nxt - 2].replace(b'\xff', b'/').decode('latin1')
                p += nxt
                nxt = struct.unpack_from('<H', buf, p - 2)[0]
            data = pos + hsize
        else:
            raise QuakeFilesError(f'unsupported LHA header level {level}')
        full = (dirname.rstrip('/') + '/' + name if dirname else name).replace('\\', '/').lower()
        yield full, method, packed, orig, data
        pos = data + packed


def extract_from_resource(resource: bytes, dest: Path, log=print) -> None:
    wanted = {inner: outer for outer, (inner, _, _) in FILES.items()}
    found = set()
    for name, method, packed, orig, off in lha_members(resource):
        if name not in wanted:
            continue
        log(f'  extracting {name} ({orig} bytes, {method}) ...')
        raw = resource[off:off + packed]
        if method == '-lh5-':
            data = lh5_decode(raw, orig)
        elif method == '-lh0-':
            data = raw
        else:
            raise QuakeFilesError(f'unsupported LHA method {method} for {name}')
        target = dest / wanted[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        found.add(name)
    missing = set(wanted) - found
    if missing:
        raise QuakeFilesError(f'not found in resource.1: {sorted(missing)}')


# --------------------------------------------------------------------------
def _find(directory: Path, rel: str):
    cur = directory
    for part in rel.split('/'):
        if not cur.is_dir():
            return None
        cur = next((p for p in cur.iterdir() if p.name.lower() == part.lower()), None)
        if cur is None:
            return None
    return cur


def verify_game_dir(game: Path) -> dict:
    report = {}
    for outer, (_, sha, _) in FILES.items():
        f = _find(game, outer)
        if f is None:
            raise QuakeFilesError(f'{outer} missing in game directory')
        got = sha256_file(f)
        report[outer] = got
        if got != sha:
            raise QuakeFilesError(f'{outer}: SHA-256 {got} does not match the expected {sha}')
    return report


def prepare(work: Path, quake_dir: Path | None = None, quake_zip: Path | None = None,
            download: bool = True, log=print) -> tuple[Path, dict]:
    """Return (game_dir, provenance). game_dir is inside work and contains
    exactly QUAKE.EXE, CWSDPMI.EXE and ID1/PAK0.PAK."""
    game = work / 'quake' / 'game'
    prov = {'expected_sha256': {k: v[1] for k, v in FILES.items()}}
    if game.is_dir():
        try:
            prov['files_sha256'] = verify_game_dir(game)
            prov['source'] = 'cached'
            return game, prov
        except QuakeFilesError:
            shutil.rmtree(game)
    staging = work / 'quake' / 'staging'
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    if quake_dir:
        for outer in FILES:
            src = _find(quake_dir, outer)
            if src is None:
                raise QuakeFilesError(f'{outer} missing in {quake_dir}')
            (staging / outer).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, staging / outer)
        prov['source'] = 'directory'
    else:
        if quake_zip is None:
            quake_zip = work / 'quake' / 'quake106.zip'
            if not quake_zip.is_file() or sha256_file(quake_zip) != QUAKE_ZIP_SHA256:
                if not download:
                    raise QuakeFilesError('no Quake files: pass --quake-zip or --quake-dir, or allow download')
                err = None
                for url in QUAKE_ZIP_URLS:
                    try:
                        log(f'  downloading {url}')
                        with urllib.request.urlopen(url, timeout=120) as r, quake_zip.open('wb') as f:
                            shutil.copyfileobj(r, f)
                        if sha256_file(quake_zip) == QUAKE_ZIP_SHA256:
                            prov['download_url'] = url
                            err = None
                            break
                        err = f'{url}: unexpected SHA-256'
                    except OSError as e:
                        err = f'{url}: {e}'
                if err:
                    raise QuakeFilesError(f'download failed ({err}); download quake106.zip manually '
                                          'and pass --quake-zip')
        zsha = sha256_file(quake_zip)
        prov['zip_sha256'] = zsha
        prov['zip_sha256_matches_reference'] = zsha == QUAKE_ZIP_SHA256
        prov['source'] = prov.get('source', 'zip')
        with zipfile.ZipFile(quake_zip) as z:
            name = next((n for n in z.namelist() if n.lower() == 'resource.1'), None)
            if name is None:
                raise QuakeFilesError('resource.1 not found in the zip')
            resource = z.read(name)
        extract_from_resource(resource, staging, log)
    prov['files_sha256'] = verify_game_dir(staging)
    staging.rename(game)
    return game, prov


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--quake-zip', type=Path)
    ap.add_argument('--quake-dir', type=Path)
    args = ap.parse_args()
    g, pv = prepare(args.work, args.quake_dir, args.quake_zip)
    print(g, pv)
    _ = io  # keep import for embedding users
