# ami-bios-usb-tools

Two small stdlib-only Python tools for AMI Aptio BIOS updates on x86 boards:

* **`agesa_scan.py`** - read the AGESA version out of a BIOS image or a vendor
  update package. Plain `grep AGESA image.bin` does not work: on current AMD
  boards the AGESA core lives inside a zlib-compressed UEFI firmware volume, and
  the firmware volume headers of a raw flash image are often left empty, so UEFI
  parsers find nothing either. This tool inflates every zlib stream in the image
  and looks for the version string.
* **`usb_pack.py`** - turn a vendor update package into a FAT32 USB stick that
  flashes the BIOS from the UEFI Shell, with no Windows in the loop: it picks the
  BIOS image and the AMI EFI flash utility out of the package, writes the shell
  scripts, and optionally drops in the EDK2 UEFI Shell so the stick boots by
  itself.

Both were written and tested against a real FEVM FAEX1 (Ryzen AI Max+ 395,
Strix Halo) update - see [docs/fevm-faex1.md](docs/fevm-faex1.md) for the
walk-through, including the EDK2 shell scripting traps that cost a couple of
reboots.

No vendor binaries live in this repository. You bring your own update package;
the tools only repack what you already downloaded.

## Requirements

* Python 3.8+, no third-party modules.
* For the USB pack: a USB stick you can format as FAT32, and a board whose UEFI
  has a Shell (built in, or the one this tool can add to the stick).
* Secure Boot must be off - neither the EDK2 Shell nor AMI AFU is signed.

## Read the AGESA version

```console
$ ./agesa_scan.py FAEX1_1.06BIOS_1.29EC.zip
FAEX1_1.06BIOS_1.29EC/FAEX1_1.29EC/EC_FEVM_V9_4_0_1_29F.bin: no AGESA version string found
FAEX1_1.06BIOS_1.29EC/FAEX1_1.06_BIOS/FP11_STXH_P9C35_FEVM_AI_395_S112_V1.06_....bin
    AGESA!V9 StrixHaloPI-FP11 1.0.0.2b
```

`--json` for machine-readable output, `--context` to see the surrounding bytes.
Exit status is 0 when a version was found.

## Build the USB flash stick

```console
$ ./usb_pack.py --vendor FAEX1_1.06BIOS_1.29EC.zip --out usb/ \
                --label "FEVM FAEX1 - BIOS 1.06" --zip FAEX1_1.06_USB.zip
pack    : /home/you/usb
label   : FEVM FAEX1 - BIOS 1.06
image   : FP11_STXH_P9C35_FEVM_AI_395_S112_V1.06_....bin (33554432 bytes)
afu     : AfuEfix64.efi
agesa   : AGESA!V9 StrixHaloPI-FP11 1.0.0.2b
ec      : skipped
shell: https://github.com/pbatard/UEFI-Shell/releases/download/26H2/shellx64.efi (26H2)
zip     : /home/you/FAEX1_1.06_USB.zip (11934028 bytes)
```

Useful flags: `--shell shellx64.efi` (offline), `--no-net`, `--with-ec`,
`--folder NAME`, `--afu-args "..."`.

The resulting tree - its *contents* go to the root of a FAT32 stick:

```
EFI/BOOT/BOOTX64.EFI     EDK2 UEFI Shell (only with --shell / network access)
startup.nsh              finds the pack and runs flash.nsh, no typing needed
flash.nsh                pauses, then AfuEfix64.efi <image> /CAPSULE /P /B /N /K /L /X /RLC:E
info.nsh                 lists the stick
README.txt               step-by-step instructions, generated for this pack
BIOS/<image>.bin         the BIOS image from the vendor package
BIOS/Tools/AfuEfix64.efi AMI flash utility from the vendor package
EC/                      only with --with-ec
```

Boot the stick (`F7` boot menu, "UEFI: <stick>"), the shell runs `startup.nsh`,
which finds the pack and runs `flash.nsh`. Nothing flashes on its own: the script
waits for a keypress and AFU asks for its own `Y` confirmation.

## How it works

`agesa_scan.py` walks the image looking for zlib headers (`78 01|5E|9C|DA`),
inflates every stream that yields more than 3 KB, and then matches an AGESA
family name (`AGESA`, `StrixHaloPI`, `ComboAM4/5`, ...) followed within 64 bytes
by a version. Strings that merely mention AGESA (`Pcd...`, `gEfi...Guid`,
protocol names) are filtered out. Typical yield on a 32 MiB Strix Halo image:
~74 streams, ~17 MB inflated, one AGESA string.

`usb_pack.py` picks the largest `*.bin`/`*.rom` over 4 MiB as the BIOS image,
the largest `Afu*.efi` as the flash utility, derives the version from the image
name, reads AGESA with `agesa_scan`, and writes the scripts with CRLF line
endings, which the shell expects.

The AFU flags are the ones AMI and the vendors ship in their own EFI shell
scripts (`nFlash.nsh`): `/CAPSULE /P /B /N /K /L /X /RLC:E`. Do not "improve"
them without a reason - and note they reset BIOS settings to defaults.

## Tests

```console
$ python3 -m unittest discover -s tests -v
```

The tests build a synthetic image (a zlib stream carrying an AGESA string, plus
a decoy) and check the scanner finds the version and ignores the decoy.

## Limitations

* AMI Aptio only (it relies on AMI's `Afu*.efi`); Insyde/Phoenix boards use
  different utilities.
* The scanner understands zlib-compressed volumes. Images compressed with
  LZMA/Tiano/Brotli would need another decompressor - none of the images we
  tested used them, and no standard compression GUIDs were present at all.
* Vendor packages are matched by file name and size heuristics; check the output
  line by line before you flash anything.
* Flashing firmware can brick a board. You do it at your own risk, by hand, with
  the package made for your exact board revision.

## License

MIT, see [LICENSE](LICENSE).
