#!/usr/bin/env python3
"""Extract only a verified DOS Quake 1.06 executable and PAK from wiki benchmark.

No game files are written to public CI artifacts. Supports nested zip archives
with explicit decompression/entry limits; rejects ZIP path traversal and links.
"""
import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

EXPECTED_QUAKE_SHA256 = "7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb"
MAX_UNCOMPRESSED = 400 * 1024 * 1024
MAX_ARCHIVE = 220 * 1024 * 1024
MAX_ENTRIES = 5000


def read_zip(data: bytes, depth: int = 0):
    if len(data) > MAX_ARCHIVE:
        raise ValueError("archive exceeds input cap")
    with zipfile.ZipFile(BytesIO(data)) as z:
        records = z.infolist()
        if len(records) > MAX_ENTRIES:
            raise ValueError("too many entries")
        if sum(e.file_size for e in records) > MAX_UNCOMPRESSED:
            raise ValueError("decompressed content exceeds cap")
        out = {}
        for e in records:
            name = e.filename.replace("\\", "/")
            p = PurePosixPath(name)
            if (p.is_absolute() or any(t in ("", ".", "..") for t in p.parts)
                    or stat.S_IFMT(e.external_attr >> 16) == stat.S_IFLNK):
                raise ValueError(f"unsafe ZIP entry {name!r}")
            if e.is_dir():
                continue
            raw = z.read(e)
            key = name.casefold()
            if depth == 0 and key.endswith(".zip") and len(raw) < MAX_ARCHIVE:
                out.update(read_zip(raw, depth + 1))
            elif not key.endswith(".zip"):
                out[key] = (name, raw)
        return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--game-dir", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    args = p.parse_args()
    data = args.archive.read_bytes()
    files = read_zip(data)
    found = []
    for key, (name, binary) in files.items():
        if not key.endswith("/quake.exe") and key != "quake.exe":
            continue
        root = key[:-len("quake.exe")]
        pak = root + "id1/pak0.pak"
        if pak in files:
            found.append((root, binary, files[pak][1]))
    if not found:
        paths = sorted(name for name, _ in files.values())
        raise ValueError("no QUAKE.EXE + ID1/PAK0.PAK sibling pair: " + repr(paths[:35]))
    valid = [(root, exe, pak) for root, exe, pak in found
             if sha256(exe).hexdigest() == EXPECTED_QUAKE_SHA256]
    if len(valid) != 1:
        raise ValueError(f"expected exactly one Quake 1.06 SHA match; candidates={len(found)}, matches={len(valid)}")
    root, exe, pak = valid[0]
    args.game_dir.mkdir(parents=True, exist_ok=False)
    (args.game_dir / "ID1").mkdir()
    (args.game_dir / "QUAKE.EXE").write_bytes(exe)
    (args.game_dir / "ID1" / "PAK0.PAK").write_bytes(pak)
    # Some historical DOS executables require a companion DPMI extender.
    # Retain only these optional launch helpers from the verified source root.
    helpers = []
    for helper in ("CWSDPMI.EXE", "DOS4GW.EXE", "GO32.EXE"):
        key = root + helper.casefold()
        if key in files:
            data = files[key][1]
            (args.game_dir / helper).write_bytes(data)
            helpers.append({"name": helper, "sha256": sha256(data).hexdigest()})
    args.manifest.write_text(json.dumps({
        "benchmark_archive_sha256": sha256(data).hexdigest(),
        "quake_exe_sha256": sha256(exe).hexdigest(),
        "pak0_sha256": sha256(pak).hexdigest(),
        "quake_exe_bytes": len(exe), "pak0_bytes": len(pak),
        "package_root": root,
        "optional_dos_extenders": helpers,
        "note": "Do not upload Quake executable or PAK, only hashes."
    }, indent=2) + "\n")
    print(f"Verified Quake 1.06 EXE SHA-256: {sha256(exe).hexdigest()}")
    print(f"PAK0 bytes: {len(pak)}, SHA-256: {sha256(pak).hexdigest()}")


if __name__ == "__main__":
    main()
