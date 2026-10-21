# AGENTS.md

Runbook for an AI agent (or a human) handed this repo and asked to bring a
**FEVM FAEX1** board (FEVM/Aimax mini-PC, AMD Ryzen AI Max+ 395 "Strix Halo")
to the newest vendor BIOS/EC. Every command below has been executed on a real
FAEX1; if a step does not behave as described, stop and report instead of
improvising on the flash path.

`README.md` = what the two tools do. `docs/fevm-faex1.md` = the case study this
runbook is distilled from. This file = the procedure, end to end, including
where to get the firmware.

## 0. Ground rules

1. **Never flash without the user's explicit go-ahead** for this specific
   machine and this specific version. Downloading, unpacking, reading AGESA and
   building the stick are safe and may be done freely.
2. **Confirm the board is FAEX1.** The vendor's own package says *"please do not
   enter if you are FAEX9"* - FAEX9 is a different board and bricks on this
   image. `dmidecode -s baseboard-product-name` / `dmidecode -s system-product-name`
   on the target, or the label in BIOS setup.
3. **Match the project suffix.** The 1.06 image is
   `..._FEVM_AI_395_S112_..._EC_V0.1.29_...`; a board on `S111` (e.g.
   `V1.03_S111_LEVM_UMA16G`) still takes it, but say so in the report instead of
   silently flashing across project suffixes.
4. **AC power must be connected and stable** during a flash; do not flash from
   a UPS-less outlet in a storm, do not interrupt AFU, do not close the lid.
5. **Record sha256 + size of everything you download** and keep the original
   archive. Never overwrite a known-good archive with a re-download.
6. **Never commit vendor firmware** to this repo - `*.zip/*.bin/*.rom/*.efi/*.exe`
   are gitignored on purpose. Work in a scratch dir outside the repo.
7. Report facts you measured (versions, sizes, hashes, `dmidecode` output), not
   assumptions.

## 1. Environment

- Python 3.8+ (CI runs the tests on 3.9 and 3.12), **stdlib only** - no pip
  installs, no build step.
- Run the tools from a checkout: `python3 agesa_scan.py ...`,
  `python3 usb_pack.py ...` (or the wrappers shipped in the caller's `bin/`).
- Outbound HTTPS needed for three hosts: `fevmpc.com` (vendor API),
  `drive.usercontent.google.com` (files), `github.com` (EDK2 shell download,
  unless `--no-net --shell <path>`).
- Tests before any push: `python3 -m unittest discover -s tests -t .`
  (8 tests, ~2 s). CI runs the same on 3.9 and 3.12.

## 2. Find the newest BIOS/EC

The support page <https://www.fevmpc.com/#/package-list> is a JS single-page
app - the HTML has nothing in it. Use the JSON API the page itself calls:

```
POST https://fevmpc.com/api/zip/tech/search
Content-Type: application/json
Accept: application/json
Referer: https://www.fevmpc.com/
User-Agent: <a browser UA, the API rejects empty/default UAs>

{"name": "FAEX1", "start_page": 1, "page_size": 999}
```

The list is at **`message.list`**. Per entry: `id`, `name`/`name_en`,
`description`/`description_en` (Chinese + English), `category`
(**4 = BIOS/EC**), `file_url` (lanzou mirror, hard to script), `out_url`
(Google Drive - prefer this), `created_at` (unix).

```python
import json, urllib.request
req = urllib.request.Request("https://fevmpc.com/api/zip/tech/search",
    data=json.dumps({"name": "FAEX1", "start_page": 1, "page_size": 999}).encode(),
    headers={"Content-Type": "application/json", "Accept": "application/json",
             "Referer": "https://www.fevmpc.com/",
             "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "Chrome/124.0.0.0 Safari/537.36"},
    method="POST")
d = json.loads(urllib.request.urlopen(req, timeout=40).read())
for it in (d.get("message") or {}).get("list") or []:
    if it.get("category") == 4 and "FAEX1" in (it.get("name_en") or it.get("name") or ""):
        print(it["id"], it.get("name_en"), "->", it.get("out_url") or it.get("file_url"))
```

**Trap: the vendor edits an entry in place.** `id` and `created_at` stay the
same while the file behind the link is replaced (1.03 -> 1.06 happened exactly
like that). So a new BIOS is *not* a new `id` and not a newer date - the
fingerprint is the **real filename**, which for Drive links is in the Drive
page `<title>`. Compare `id + filename`, not dates.

```python
html = urllib.request.urlopen(urllib.request.Request(
    out_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=40).read().decode("utf-8", "replace")
# <title>FAEX1_1.06BIOS_1.29EC.zip - Google Drive</title> (the suffix is locale-
# dependent, e.g. "Google Диск" - take the part before the first " - ")
```

## 3. Download and verify

Extract the Drive file id from `out_url` (`/file/d/<ID>/`) and use the direct
endpoint - plain `.../uc?export=download` returns an HTML interstitial for
files this size:

```python
import re, hashlib, urllib.request
fid = re.search(r"/file/d/([-\w]+)/", out_url).group(1)
url = f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
data = urllib.request.urlopen(urllib.request.Request(
    url, headers={"User-Agent": "Mozilla/5.0"}), timeout=300).read()
assert data[:2] == b"PK", "not a zip - Drive returned a page"
print(len(data), hashlib.sha256(data).hexdigest())
```

Expect `Content-Type: application/octet-stream`, a `PK` magic, and a size of
~15 MB. Unpack and look inside: the archive carries `__MACOSX/` and
`.DS_Store` junk plus a Chinese `说明.txt` tutorial - ignore the junk, read the
tutorial. `usb_pack.py` ignores it too.

Known-good baseline (BIOS 1.06 / EC 1.29, what the docs are written against):

| item | value |
|------|-------|
| archive | `FAEX1_1.06BIOS_1.29EC.zip`, 15 707 337 bytes |
| sha256 | `38ebe021ea8c7044b2c976480b4ec90c6742c781cb032b608ec86857258a2e9e` |
| BIOS image | `FP11_STXH_P9C35_FEVM_AI_395_S112_V1.06_M0C0xFF_8000_1_12_EC_V0.1.29_20260720.bin`, 33 554 432 bytes (32 MiB) |
| AGESA in image | `AGESA!V9 StrixHaloPI-FP11 1.0.0.2b` |
| EC image | `EC_FEVM_V9_4_0_1_29F.bin` = `V9.4.0.1.29` |

## 4. Read the package before touching the machine

```console
$ python3 agesa_scan.py FAEX1_1.06BIOS_1.29EC.zip
FP11_STXH_..._EC_V0.1.29_20260720.bin: AGESA!V9 StrixHaloPI-FP11 1.0.0.2b
```

- Plain `grep AGESA image.bin` finds nothing (content is in zlib streams, FV
  headers are empty), and `uefi-firmware-parser`/`UEFIExtract` fail on these
  images too - use `agesa_scan.py`, don't "fix" it with grep.
- **EC rule:** the same EC firmware is already inside the BIOS image. If the
  machine already reports `EC version 9.4.0.1.29`, do not flash the EC
  separately - BIOS alone is enough. Flash EC separately only when the EC is
  actually older, and then follow `EC/FEC64.NSH` from the vendor package.
- AGESA matters when the reason for the upgrade is the GPU+NPU Data Fabric
  crash (upstream TheRock#8709): AMD names AGESA 1.0.0.2b as the required
  level, but it is necessary, not sufficient (see `docs/fevm-faex1.md`).

## 5. Build the stick

```console
$ python3 usb_pack.py --vendor FAEX1_1.06BIOS_1.29EC.zip --out usb/ \
      --label "FEVM FAEX1 - BIOS 1.06 / EC 1.29" --with-ec --zip FAEX1_1.06_USB.zip
```

Produces `EFI/BOOT/BOOTX64.EFI` (EDK2 shell, downloaded unless `--no-net
--shell <path>`), `startup.nsh`, `flash.nsh`, `info.nsh`, `README.txt`,
`BIOS/<image>.bin`, `BIOS/Tools/AfuEfix64.efi` and, with `--with-ec`, `EC/`.
AFU is called with the vendor's own flags - `/CAPSULE /P /B /N /K /L /X /RLC:E`;
do not invent your own unless the vendor's `nFlash.nsh` differs, then pass
`--afu-args`.

Copy the **contents** of `usb/` to the root of a **FAT32** stick (MBR or GPT,
doesn't matter). Do not nest the folder deeper than the pack expects.

## 6. Flash (only after explicit approval)

1. Note current settings first - the flash resets them: eco/power mode, iGPU /
   UMA VRAM carve-out, IOMMU/SVM, boot order, Secure Boot state.
2. **Secure Boot off** - neither the EDK2 shell nor AFU is signed.
3. Boot menu `F7` -> `UEFI: <stick>`. `startup.nsh` runs automatically, finds
   the pack and runs `flash.nsh`; press any key, then `Y` in AFU.
4. Shell traps that cost hours if you hand-write scripts (full list in
   `docs/fevm-faex1.md`): `echo.` does not exist (use `echo`); `if exist` must
   test a file that really exists; `flash` works from the stick root with no
   `cd`; the stick is not necessarily `fs0` (check `map`, the `/USB(` line - on
   the reference machine it was `FS1` and `FS0` was the NVMe ESP); the image
   filename is 88 characters, so run `flash` instead of typing AFU by hand.
5. After AFU finishes: power off, **unplug the power cord for 10 s** (this is
   what makes the new EC take effect), power on, restore the settings from
   step 1.

## 7. Verify and report

```console
# Linux, from the installed system
$ sudo dmidecode -s bios-version        # e.g. V1.06_S111_LEVM_UMA16G -> V1.06
$ sudo dmidecode -s bios-release-date
# or in setup: Mainboard -> BIOS version, EC version
```

Report, with the actual values: archive name + size + sha256, AGESA string
from `agesa_scan.py`, BIOS version before/after, EC version before/after, and
whether the EC was flashed. AGESA is not exposed by `dmidecode` - if it must be
confirmed after the flash, re-read the image that was flashed, not the machine.

## 8. Out of scope for this repo

- Other boards (FAEX9, FA100, Beelink, ...): the tooling is generic, the
  download filter and the caveats are not.
- Windows-side flashing (vendor `Flash.exe` / `nFlash.nsh` under Windows) -
  this repo only builds the UEFI-Shell pack.
- Anything that changes GPU/NPU clocks or the host's container config; those
  are the operator's call, not a firmware task.
