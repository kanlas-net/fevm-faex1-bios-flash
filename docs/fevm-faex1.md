# Case study: FEVM FAEX1, BIOS 1.06 (Strix Halo)

Board: FEVM FAEX1, Ryzen AI Max+ 395 (Strix Halo, gfx1151 / Radeon 8060S),
128 GB unified memory, 32 MiB SPI flash. Everything below was measured on a
board running BIOS `V1.03_S111_LEVM_UMA16G` while judging the `1.06` update.

## The vendor package

`FAEX1_1.06BIOS_1.29EC.zip`, 15,707,337 bytes:

```
FAEX1_1.06BIOS_1.29EC/
  FAEX1_1.06_BIOS/
    FP11_STXH_P9C35_FEVM_AI_395_S112_V1.06_M0C0xFF_8000_1_12_EC_V0.1.29_20260720.bin
    Flash.cmd                       AFUWINx64 ... /CAPSULE /P /B /N /K /L /X /RLC:E
    Tools/AFUWINx64.exe             Windows flasher
    Tools/AFWINGUIx64.EXE
    Tools/AfuEfix64.efi             UEFI Shell flasher
    Tools/nFlash.nsh                vendor UEFI Shell script
  FAEX1_1.29EC/
    EC_FEVM_V9_4_0_1_29F.bin        ITE IT5570 embedded controller
    IFU_XE.exe, ifu.efi, FEC64.NSH, uiset.ini, ChecksumF.txt
  step1.jpg, step2.jpg, tutorial.txt  (Chinese; the text still says "check 1.03")
```

Image name decodes as: project `FP11`, platform `STXH` (Strix Halo), AMI project
code `P9C35`, board string `FEVM_AI_395`, silicon revision `S112`, BIOS `V1.06`,
`EC V0.1.29`, build date `2026-07-20`.

## AGESA version in 1.06

```console
$ ./agesa_scan.py FAEX1_1.06BIOS_1.29EC.zip
FAEX1_1.06BIOS_1.29EC/FAEX1_1.29EC/EC_FEVM_V9_4_0_1_29F.bin: no AGESA version string found
FAEX1_1.06BIOS_1.29EC/FAEX1_1.06_BIOS/FP11_STXH_P9C35_....bin
    AGESA!V9 StrixHaloPI-FP11 1.0.0.2b
```

That is the only AGESA string in the image. It matters because AMD named AGESA
`1.0.0.2b` as the firmware precondition for running the GPU and the NPU at the
same time on Strix Halo (ROCm/TheRock issue #8709: a deferred Data Fabric machine
check and a hard hang, sync flood `0x08000800`). Necessary, not sufficient: a
report in the same issue has a Strix Halo device on AGESA 1.0.0.2b that still
crashed, and the Windows-side report pairs the new AGESA with a new Radeon
driver. Treat the BIOS as one half of the fix and keep testing.

## Embedded controller

The EC image self-reports its version as `V9.4.0.1.29`, built `2026/03/13
10:00:14`:

```console
$ python3 - <<'EOF'
import re
d = open("EC_FEVM_V9_4_0_1_29F.bin", "rb").read()
print([s for s in re.findall(rb"[\x20-\x7e]{6,}", d) if re.search(rb"\d+\.\d+\.\d+\.\d+", s)])
EOF
[b'V9.4.0.1.29']
```

The `F` in the file name is a file suffix, not part of the version, and the same
EC 1.29 firmware is also embedded in the BIOS image itself (the raw image
contains the string `V9.4.0.1.29` next to the EC code). So if the BIOS already
shows `EC version 9.4.0.1.29`, flashing the EC separately gains nothing - and a
botched ITE EC flash needs a programmer to recover, so skip it.

## Why grep and UEFI extractors fail

* The 32 MiB image has six firmware volume headers, all of them valid but
  **empty**: every file slot after the header is `0xFF`.
* Only ~2,300 printable runs of 8+ bytes exist in the whole image, i.e. the real
  content is compressed, not plain.
* No LZMA (`EE4E5898-...`), Tiano (`015B17DA-...`), Brotli or LZ4 section GUID
  appears anywhere in the image, so `UEFIExtract` / `uefi-firmware-parser` walk
  the empty volumes and report nothing.
* What is present: plain zlib streams. Inflating them yields ~16.8 MB of the
  real firmware, including all the AGESA/ABL strings.

Useful leftovers from that dump: ABL build date `06/17/24 02:08:02` and version
`113-STRXLGEN-001.STXHL.PCI_EXPRESS.DDR5`, `AGESA!V9` / `StrixHaloPI-FP11`.

## Building and using the stick

```console
$ ./usb_pack.py --vendor FAEX1_1.06BIOS_1.29EC.zip --out usb/ \
                --label "FEVM FAEX1 - BIOS 1.06" --zip FAEX1_1.06_USB.zip
```

Copy the *contents* of `usb/` to the root of a FAT32 stick (MBR or GPT, does not
matter). On the board: `F7` -> `UEFI: <stick>`; the shell auto-runs
`startup.nsh`, which finds the pack and runs `flash.nsh`; press any key, then
`Y` in AFU. Settings are reset - note eco/power mode, the iGPU/UMA carve-out,
IOMMU/SVM and the boot order beforehand. After the flash: power off, unplug the
cord for 10 s (so the new EC takes effect), power on, restore settings.

## EDK2 Shell scripting traps (edk2-stable202602)

1. **`echo.` does not exist.** It prints `echo: Unknown flag - '.'`. A blank line
   is just `echo`.
2. **`if exist` is fine with both `fsN:\dir\file` and relative paths**, but test
   a file that actually exists. The first version of the pack tested
   `BIOS\flash.nsh` while `flash.nsh` lives in the stick root - the check was
   always false and the script printed "folder not found" on a perfectly good
   stick. Use a marker that is really there (`BIOS\Tools\AfuEfix64.efi`).
3. **`flash` works straight from the stick root**, no `cd` needed - `flash.nsh`
   enters the folder itself. Users will notice this before you do.
4. **The stick is not necessarily `fs0`.** Check `map`: the line with `/USB(`
   is your stick (it was `FS1`, with `FS0` being the NVMe ESP). The generated
   `startup.nsh` probes `fs0`..`fs3`.
5. **Secure Boot must be off**: neither `BOOTX64.EFI` (EDK2 shell) nor
   `AfuEfix64.efi` is signed.
6. The image name is 88 characters long - retyping it by hand in the shell is
   painful. That is why `flash.nsh` holds it: run `flash`, do not type AFU
   manually. If you must, `mv *.bin bios.bin` first (the shell expands
   wildcards for its own built-ins) and then pass `bios.bin`.
