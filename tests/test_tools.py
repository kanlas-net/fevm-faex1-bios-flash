"""Tests for agesa_scan.py and usb_pack.py.

    python3 -m unittest discover -s tests -t . -v

The images are synthetic: a zlib stream carrying an AGESA string (the scanner
looks exactly for that) plus a decoy that only "mentions" AGESA via a PCD GUID
name. No vendor firmware is needed or included.
"""
import os
import sys
import tempfile
import unittest
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agesa_scan  # noqa: E402
import usb_pack  # noqa: E402

GOOD = b"AGESA!V9 StrixHaloPI-FP11 1.0.0.2b\x00"
DECOY = b"gEfiAmdAgesaPkgTokenSpaceGuid PcdAmdSomething 1.2.3.4\x00"


def stream(plain, pad=6000):
    """A zlib stream that inflates to well over the 3 KB detection threshold."""
    return zlib.compress(plain + b"\x00" * pad, 9)


def image(*streams, size=5 << 20):
    """A fake full-flash image: 0xFF padding with the given zlib streams inside."""
    body = b"".join(streams)
    return body + b"\xff" * max(0, size - len(body))


def scan_image(data):
    hits = set()
    for blob in agesa_scan.inflate_streams(data):
        hits |= set(agesa_scan.scan_bytes(blob))
    return hits


class AgesaScanTest(unittest.TestCase):
    def test_finds_agesa_version(self):
        hits = scan_image(image(stream(GOOD)))
        self.assertEqual(len(hits), 1)
        hit = hits.pop()
        self.assertIn("1.0.0.2b", hit)
        self.assertIn("StrixHaloPI-FP11", hit)

    def test_ignores_pcd_and_guid_noise(self):
        self.assertEqual(scan_image(image(stream(DECOY))), set())

    def test_version_only_matches_full_quad(self):
        # 1.0.0 alone is not an AGESA version - the PI name must be followed by one
        hits = scan_image(image(stream(b"AGESA!V9\x00StrixHaloPI-FP11 1.0.0\x00")))
        self.assertTrue(all("1.0.0.2b" not in h for h in hits))

    def test_zip_member_and_plain_file(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = os.path.join(td, "vendor.zip")
            with zipfile.ZipFile(pkg, "w") as z:
                z.writestr("pkg/BIOS_FAKE_V9.99.bin", image(stream(GOOD)))
            found = agesa_scan.agesa_versions(pkg)
            self.assertTrue(any(h for hits in found.values() for h in hits))
            self.assertEqual(agesa_scan.main([pkg, "--json"]), 0)

    def test_exit_status_without_hits(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "empty.bin")
            with open(p, "wb") as f:
                f.write(b"\xff" * 4096)
            self.assertEqual(agesa_scan.main([p]), 1)


class UsbPackTest(unittest.TestCase):
    def build(self, td, extra=()):
        vendor = os.path.join(td, "vendor.zip")
        with zipfile.ZipFile(vendor, "w") as z:
            z.writestr("pkg/BIOS_FAKEBOARD_V1.07_20260101.bin", image(stream(GOOD)))
            z.writestr("pkg/Tools/AfuEfix64.efi", b"MZ" + b"\x00" * (200 << 10))
            z.writestr("pkg/Tools/nFlash.nsh", b"@echo -off\n")
            z.writestr("pkg/EC/EC_FAKE_V9.9.9.bin", b"\x00" * 4096)
        shell = os.path.join(td, "shell.efi")
        with open(shell, "wb") as f:
            f.write(b"MZ" + b"\x00" * 1024)
        out = os.path.join(td, "usb")
        rc = usb_pack.main(["--vendor", vendor, "--out", out, "--shell", shell] + list(extra))
        return out, rc

    def test_layout_and_scripts(self):
        with tempfile.TemporaryDirectory() as td:
            out, rc = self.build(td)
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(os.path.join(out, "BIOS", "Tools", "AfuEfix64.efi")))
            self.assertTrue(os.path.exists(os.path.join(out, "EFI", "BOOT", "BOOTX64.EFI")))
            with open(os.path.join(out, "startup.nsh"), "rb") as f:
                startup = f.read().decode()
            self.assertIn("1.0.0.2b", startup)
            self.assertIn("\r\n", startup)                     # shell wants CRLF
            self.assertNotIn("echo.", startup)                 # not a shell command
            self.assertIn("BIOS\\Tools\\AfuEfix64.efi", startup)
            with open(os.path.join(out, "flash.nsh")) as f:
                flash = f.read()
            self.assertIn(usb_pack.AFU_ARGS, flash)
            self.assertIn("BIOS_FAKEBOARD_V1.07_20260101.bin", flash)
            self.assertIn("BIOS 1.07", flash)

    def test_ec_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            out, _ = self.build(td)
            self.assertFalse(os.path.exists(os.path.join(out, "EC")))
        with tempfile.TemporaryDirectory() as td:
            out, _ = self.build(td, ["--with-ec"])
            self.assertTrue(os.path.exists(os.path.join(out, "EC", "EC_FAKE_V9.9.9.bin")))

    def test_no_shell_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = os.path.join(td, "vendor.zip")
            with zipfile.ZipFile(vendor, "w") as z:
                z.writestr("pkg/BIOS_FAKEBOARD_V1.07.bin", image(stream(GOOD)))
                z.writestr("pkg/Tools/AfuEfix64.efi", b"MZ" + b"\x00" * (200 << 10))
            out = os.path.join(td, "usb")
            self.assertEqual(usb_pack.main(["--vendor", vendor, "--out", out, "--no-net"]), 0)
            self.assertFalse(os.path.exists(os.path.join(out, "EFI")))
            with open(os.path.join(out, "README.txt")) as f:
                self.assertIn("built-in UEFI Shell", f.read())


if __name__ == "__main__":
    unittest.main()
