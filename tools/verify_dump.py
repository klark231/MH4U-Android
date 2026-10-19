#!/usr/bin/env python3
"""Print identifying info + hashes for a dump and compare with config/versions.json.

Only hashes are ever shared; never upload the files themselves.

usage: verify_dump.py <exheader.bin> <code.bin>
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codebin2elf  # noqa: E402
import exheader as exh  # noqa: E402

VERSIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "versions.json")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], "rb") as f:
        exh_raw = f.read()
    with open(argv[2], "rb") as f:
        code_raw = f.read()

    header = exh.parse(exh_raw)
    code = codebin2elf.load_code(header, code_raw)
    sci_sha1 = hashlib.sha1(exh_raw[:exh.SCI_SIZE]).hexdigest()
    code_sha1 = hashlib.sha1(code).hexdigest()

    print(exh.describe(header))
    print(f"exheader SCI sha1      : {sci_sha1}")
    print(f"code.bin (decomp) sha1 : {code_sha1}")
    print(f"code.bin (decomp) size : 0x{len(code):X}")

    with open(VERSIONS) as f:
        known = json.load(f).get("versions", [])
    for v in known:
        if v.get("code_sha1") == code_sha1:
            print(f"\n✔ Known version: {v.get('name')}")
            return 0
    print("\n? Unknown version. Please open an issue with the output above (hashes only).")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
