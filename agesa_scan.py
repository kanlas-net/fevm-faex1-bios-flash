#!/usr/bin/env python3
"""Read the AGESA version out of an AMI Aptio BIOS image (or vendor update zip).

Why this exists
---------------
On modern AMD boards the AGESA version string lives inside a zlib-compressed
UEFI firmware volume, and the firmware volume headers of a full flash image are
often left empty. So neither ``grep AGESA image.bin`` nor UEFI parsers such as
``UEFIExtract``/``uefi-firmware-parser`` show anything useful. What does work:
inflate every zlib stream found in the image and look for the version string,
which the AGESA core exports as, for example::

    AGESA!V9 StrixHaloPI-FP11 1.0.0.2b

Usage
-----
    agesa_scan.py IMAGE.bin
    agesa_scan.py VENDOR_UPDATE.zip          # all *.bin/*.rom members
    agesa_scan.py IMAGE.bin --json
    agesa_scan.py IMAGE.bin --context        # also show surrounding text

Exit status: 0 if at least one version was found, 1 otherwise.

stdlib only, Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
import zlib

# "AGESA" (or an AGESA PI family name) followed, within a few bytes, by a version.
_NAMES = rb"(?i)(agesa|StrixHaloPI|ComboAM[45]|CVA2PI|MullansPI|RenoPI|MiragePI|PhoenixPI|RembrandtPI)"
_VERSION = rb"([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(?:\.[0-9]{1,3}[a-z]?)?)"
# Strings that merely mention AGESA (PCD names, GUID names, protocols) - not versions.
_NOISE = (b"Pcd", b"Guid", b"TokenSpace", b"Protocol", b"Ppi", b"Locate")

_ZLIB_HEADERS = (0x01, 0x5E, 0x9C, 0xDA)


def iter_payloads(path):
    """Yield (name, bytes) for a raw image or for every .bin/.rom inside a zip."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith((".bin", ".rom")) and "__MACOSX" not in name:
                    yield name, z.read(name)
    else:
        with open(path, "rb") as f:
            yield path, f.read()


def inflate_streams(data, min_size=3000, max_out=64 << 20):
    """Return the payload of every zlib stream that decompresses to > min_size."""
    out = []
    for i in range(len(data) - 2):
        if data[i] != 0x78 or data[i + 1] not in _ZLIB_HEADERS:
            continue
        try:
            blob = zlib.decompressobj().decompress(data[i:], max_out)
        except Exception:
            continue
        if len(blob) > min_size:
            out.append(blob)
    return out


def scan_bytes(blob, context=False):
    """Return {version_string: context} for AGESA hits in one inflated blob."""
    hits = {}
    skip_until = -1
    for m in re.finditer(_NAMES, blob):
        if m.start() < skip_until:
            continue          # part of the previous hit (e.g. "AGESA!V9 StrixHaloPI-...")
        tail = blob[m.end():m.end() + 64]
        if any(n in tail for n in _NOISE):
            continue
        v = re.search(_VERSION, tail)
        if not v:
            continue
        skip_until = m.end() + v.end()
        text = blob[m.start():skip_until]
        clean = " ".join(re.sub(rb"[^\x20-\x7e]", b" ", text).decode("ascii", "replace").split())
        if context:
            around = blob[max(0, m.start() - 40):skip_until + 40]
            hits[clean] = re.sub(rb"[^\x20-\x7e]", b".", around).decode("ascii", "replace")
        else:
            hits[clean] = None
    return hits


def agesa_versions(path, context=False):
    """Scan a whole image (raw or zipped) and return {name: {version_string: ctx}}."""
    result = {}
    for name, data in iter_payloads(path):
        found = {}
        for blob in inflate_streams(data):
            for clean, ctx in scan_bytes(blob, context).items():
                found.setdefault(clean, ctx)
        result[name] = found
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Print the AGESA version embedded in an AMI Aptio BIOS image.")
    ap.add_argument("image", nargs="+", help="BIOS image (.bin/.rom) or vendor update (.zip)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--context", action="store_true", help="also print surrounding bytes")
    args = ap.parse_args(argv)

    all_hits = {}
    for path in args.image:
        try:
            all_hits[path] = agesa_versions(path, context=args.context)
        except (OSError, zipfile.BadZipFile) as exc:
            print("%s: %s" % (path, exc), file=sys.stderr)
            all_hits[path] = {}

    if args.json:
        print(json.dumps(all_hits, indent=2, sort_keys=True))
    else:
        for path, per_file in all_hits.items():
            for name, hits in per_file.items():
                if not hits:
                    print("%s: no AGESA version string found" % name)
                    continue
                for clean in sorted(hits):
                    print("%s\n    %s" % (name, clean))
                    if args.context and hits[clean]:
                        print("    %s" % hits[clean])

    return 0 if any(h for per_file in all_hits.values() for h in per_file.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
