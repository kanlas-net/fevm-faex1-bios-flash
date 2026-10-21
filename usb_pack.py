#!/usr/bin/env python3
"""Turn a vendor AMI Aptio BIOS update package into a FAT32 USB stick that
flashes the BIOS from the UEFI Shell, with no Windows involved.

What it does
------------
* unpacks the vendor package (zip) and picks out the BIOS image, the AMI EFI
  flash utility (AfuEfix64.efi) and - optionally - the embedded-controller image;
* reads the AGESA version out of the image (see agesa_scan.py);
* writes startup.nsh / flash.nsh / info.nsh plus a README, and lays everything
  out the way a UEFI Shell expects it;
* optionally drops in the EDK2 UEFI Shell as EFI/BOOT/BOOTX64.EFI so the stick
  boots on its own (downloaded from pbatard/UEFI-Shell if not supplied).

Usage
-----
    usb_pack.py --vendor update.zip --out usb/
    usb_pack.py --vendor update.zip --out usb/ --shell shellx64.efi --zip usb.zip
    usb_pack.py --vendor update.zip --out usb/ --no-net      # no shell download

The stick layout produced (contents go to the ROOT of the FAT32 stick)::

    EFI/BOOT/BOOTX64.EFI     UEFI Shell (optional)
    startup.nsh              finds the pack and runs flash.nsh
    flash.nsh                pauses, then runs AfuEfix64.efi on the image
    info.nsh                 lists the stick contents
    README.txt               step-by-step instructions
    BIOS/<image>.bin
    BIOS/Tools/AfuEfix64.efi

Nothing is written to the target machine by this script - it only prepares the
stick. Flashing stays a manual, deliberate act: flash.nsh waits for a keypress
and AMI AFU asks for its own Y/N confirmation.

stdlib only, Python 3.8+.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sys
import urllib.request
import zipfile

from agesa_scan import agesa_versions

SHELL_API = "https://api.github.com/repos/pbatard/UEFI-Shell/releases/latest"
SHELL_ASSET = "shellx64.efi"
# AMI AFU flags, identical to the ones in the vendor's own nFlash.nsh.
AFU_ARGS = "/CAPSULE /P /B /N /K /L /X /RLC:E"

STARTUP = """@echo -off
echo  ============================================================
echo   {label}
echo   {agesa}
echo  ============================================================
echo
if exist fs0:\\{folder}\\Tools\\AfuEfix64.efi then
  fs0:
endif
if exist fs1:\\{folder}\\Tools\\AfuEfix64.efi then
  fs1:
endif
if exist fs2:\\{folder}\\Tools\\AfuEfix64.efi then
  fs2:
endif
if exist fs3:\\{folder}\\Tools\\AfuEfix64.efi then
  fs3:
endif
if exist {folder}\\Tools\\AfuEfix64.efi then
  flash.nsh
endif
if not exist {folder}\\Tools\\AfuEfix64.efi then
  echo   [ERROR] flash pack not found on the current drive.
  echo   Type  fs1:   then  ls  - the stick must show {folder}.
  echo   Then type  flash  to start the update.
endif
echo
"""

FLASH = """@echo -off
echo  ============================================================
echo   FLASH {label}
echo   {agesa}
echo   file: {image}
echo  ============================================================
echo   BIOS settings will be reset to defaults.
echo   Do NOT switch the power off while it writes.
echo
echo   Press any key to start, or leave it alone to abort.
pause
cd {folder}
if not exist Tools\\AfuEfix64.efi then
  echo   [ERROR] Tools\\AfuEfix64.efi not found - stick not copied correctly
endif
if exist Tools\\AfuEfix64.efi then
  .\\Tools\\AfuEfix64.efi "{image}" {afu_args}
  echo
  echo   AFU exit code: %lasterror%   (0 = OK)
endif
cd ..
echo
echo   When it is done: power off, unplug the power cord for 10 s,
echo   then power on and set the BIOS options again.
echo
"""

INFO = """@echo -off
echo  ---- files on this stick ----
ls
ls {folder}
ls {folder}\\Tools
echo  -----------------------------
"""

README = """{label}
{underline}

What is in the pack
{shell_line}  startup.nsh            starts automatically, finds the pack, runs flash.nsh
  flash.nsh              the flash itself: AMI AfuEfix64.efi + the BIOS image
  info.nsh               lists what is on the stick
  {folder}/              BIOS image + Tools/AfuEfix64.efi
{ec_block}
Preparing the stick
  1. Format a USB stick as FAT32. MBR or GPT, any size.
  2. Copy the CONTENTS of this folder to the ROOT of the stick.
     Right: startup.nsh, flash.nsh, EFI/ and {folder}/ are visible at the root.
     Wrong: a single wrapper folder with everything inside it.
  3. Write down the current BIOS settings first - flashing resets them.
     Typical things to note: power/eco mode, iGPU memory carve-out (UMA),
     IOMMU/SVM, boot order, memory timings.

Flashing
  1. Power off, plug the stick in (a rear port is usually safest).
  2. Power on, open the boot menu (often F7) and pick "UEFI: <your stick>".
     The shell picks up startup.nsh, which finds the pack and runs flash.nsh:
     press any key, then answer Y to the AFU confirmation.
  3. No self-booting shell in the boot menu? Use the board's built-in
     "UEFI Shell" entry: type  fs1:  (the number of your stick - check  map ,
     the line that contains  /USB(  ), then  ls  and  flash . No cd needed,
     flash.nsh enters the folder itself.
  4. Shell refuses to start? Disable Secure Boot (the shell and AFU are not
     signed) and, if possible, Fast Boot.
  5. Do not remove power while AFU writes. If the machine does not reboot by
     itself, power off, unplug the cord for 10 s, then power on.
  6. Enter BIOS setup, restore your settings, confirm the new BIOS version.

Notes
  - Flashing is done by a human, on purpose: flash.nsh waits for a keypress
    and AFU asks for its own confirmation. Cancelling is possible until AFU
    starts writing.
  - Use only the vendor package made for your exact board revision.
  - {agesa} is what AMD names as the firmware side of some fixes, but the
    firmware alone is not a guarantee that a fixed driver/OS is not needed.
{ec_note}"""


def _read_entries(path):
    """Return [(relative name, bytes)] for a vendor zip or a directory."""
    entries = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir() or "__MACOSX" in info.filename:
                    continue
                entries.append((info.filename, z.read(info)))
    else:
        for root, _, files in os.walk(path):
            for fn in files:
                full = os.path.join(root, fn)
                entries.append((os.path.relpath(full, path), open(full, "rb").read()))
    return entries


def _pick(entries, want, exclude=(), min_size=0, max_size=None):
    """Pick the largest entry whose basename matches each token in `want`."""
    best = None
    for name, data in entries:
        low = os.path.basename(name).lower()
        if not all(w in low for w in want):
            continue
        if any(x in low for x in exclude):
            continue
        if len(data) < min_size or (max_size and len(data) > max_size):
            continue
        if best is None or len(data) > len(best[1]):
            best = (name, data)
    return best


def fetch_shell(dest):
    """Download the current EDK2 UEFI Shell x64 from pbatard/UEFI-Shell."""
    req = urllib.request.Request(SHELL_API, headers={"User-Agent": "usb_pack"})
    release = json.load(urllib.request.urlopen(req, timeout=30))
    url = next(a["browser_download_url"] for a in release["assets"]
               if a["name"] == SHELL_ASSET)
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return "%s (%s)" % (url, release.get("tag_name", "?"))


def guess_version(image_name, entries_names):
    m = re.search(r"[_\- ]?[Vv](\d+\.\d+[a-zA-Z0-9]*)", os.path.basename(image_name))
    if m:
        return m.group(1)
    m = re.search(r"(\d+\.\d+)", os.path.basename(image_name))
    return m.group(1) if m else "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a UEFI-Shell USB flash pack "
                                             "from a vendor AMI BIOS update package.")
    ap.add_argument("--vendor", required=True, help="vendor update .zip or extracted directory")
    ap.add_argument("--out", required=True, help="output directory for the stick tree")
    ap.add_argument("--folder", default="BIOS", help="folder name on the stick (default: BIOS)")
    ap.add_argument("--shell", help="path to Shell.efi (default: download EDK2 shell)")
    ap.add_argument("--no-net", action="store_true", help="never download anything")
    ap.add_argument("--label", help="title line, e.g. 'FEVM FAEX1 - BIOS 1.06'")
    ap.add_argument("--version", help="override the BIOS version shown in the scripts")
    ap.add_argument("--afu-args", default=AFU_ARGS, help="AFU flags (default: %s)" % AFU_ARGS)
    ap.add_argument("--with-ec", action="store_true",
                    help="also copy the embedded-controller image and its files")
    ap.add_argument("--zip", help="also write the tree to this zip file")
    args = ap.parse_args(argv)

    entries = _read_entries(args.vendor)
    if not entries:
        sys.exit("no files found in %s" % args.vendor)

    bios = _pick(entries, [".bin"], min_size=4 << 20)
    if bios is None:
        bios = _pick(entries, [".rom"], min_size=4 << 20)
    if bios is None:
        sys.exit("BIOS image (*.bin/*.rom > 4 MiB) not found in the vendor package")
    image_name, image_data = bios

    afu = _pick(entries, ["afu", ".efi"], min_size=64 << 10)
    if afu is None:
        sys.exit("AMI EFI flash utility (Afu*.efi) not found in the vendor package")

    version = args.version or guess_version(image_name, [n for n, _ in entries])
    label = args.label or "BIOS %s" % version
    agesa_hits = agesa_versions(args.vendor)
    agesa = sorted({h for hits in agesa_hits.values() for h in hits})
    agesa_line = agesa[0] if agesa else "AGESA version: not detected"

    out = os.path.abspath(args.out)
    folder = args.folder
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, folder, "Tools"), exist_ok=True)

    with open(os.path.join(out, folder, image_name.split("/")[-1]), "wb") as f:
        f.write(image_data)
    with open(os.path.join(out, folder, "Tools", os.path.basename(afu[0])), "wb") as f:
        f.write(afu[1])

    ec_block = ""
    if args.with_ec:
        ec = _pick(entries, ["ec", ".bin"], max_size=2 << 20)
        if ec:
            ec_dir = os.path.dirname(ec[0])
            os.makedirs(os.path.join(out, "EC"), exist_ok=True)
            for name, data in entries:
                if os.path.dirname(name) != ec_dir:
                    continue
                base = os.path.basename(name)
                if base.lower().endswith((".bin", ".efi", ".nsh", ".ini", ".txt")):
                    with open(os.path.join(out, "EC", base), "wb") as f:
                        f.write(data)
            ec_block = ("  EC/                    embedded controller image + utilities\n"
                        "                         (only flash it if the vendor says so)\n")

    shell_note = ""
    shell_dest = os.path.join(out, "EFI", "BOOT", "BOOTX64.EFI")
    if args.shell:
        os.makedirs(os.path.dirname(shell_dest), exist_ok=True)
        shutil.copyfile(args.shell, shell_dest)
        shell_note = "shell: %s" % args.shell
    elif not args.no_net:
        os.makedirs(os.path.dirname(shell_dest), exist_ok=True)
        try:
            shell_note = "shell: " + fetch_shell(shell_dest)
        except Exception as exc:                     # network down - not fatal
            shell_note = "shell: not downloaded (%s)" % exc
            if os.path.exists(shell_dest):
                os.unlink(shell_dest)

    fmt = dict(label=label, agesa=agesa_line, image=image_name.split("/")[-1],
               folder=folder, afu_args=args.afu_args, ec_block=ec_block,
               underline="=" * len(label),
               shell_line=("  EFI/BOOT/BOOTX64.EFI   UEFI Shell, so the stick boots by itself\n"
                           if os.path.exists(shell_dest) else
                           "  (no UEFI Shell included - boot the board's built-in UEFI Shell)\n"),
               ec_note=("\n  - EC image included: flashing it is a separate, riskier step.\n"
                        if args.with_ec else ""))
    for name, text in (("startup.nsh", STARTUP), ("flash.nsh", FLASH),
                       ("info.nsh", INFO), ("README.txt", README)):
        with open(os.path.join(out, name), "w", newline="\r\n") as f:
            f.write(text.format(**fmt))

    if args.zip:
        with zipfile.ZipFile(args.zip, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(out):
                for fn in files:
                    p = os.path.join(root, fn)
                    z.write(p, os.path.relpath(p, out))

    print("pack    : %s" % out)
    print("label   : %s" % label)
    print("image   : %s (%d bytes)" % (image_name.split("/")[-1], len(image_data)))
    print("afu     : %s" % os.path.basename(afu[0]))
    print("agesa   : %s" % (agesa[0] if agesa else "not detected"))
    print("ec      : %s" % ("included" if args.with_ec else "skipped"))
    print(shell_note)
    if args.zip:
        print("zip     : %s (%d bytes)" % (args.zip, os.path.getsize(args.zip)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
