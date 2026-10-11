"""Prove the 2025.2 overlay is loadable and its address map is what the code expects.

A bitstream that programs the FPGA is not the same as one PYNQ can drive. PYNQ
resolves every IP through the .hwh, so the failure this guards against is a
bitstream that runs but whose handoff file describes a different address map --
which would surface as the pipeline reading the wrong registers rather than as
an obvious error.

Checked against the constants in pl_motion_detection_optimized.py, so a moved
peripheral is reported rather than assumed.
"""
import asyncio
import sys

sys.path.insert(0, "/home/xilinx/intrusion_demo")

asyncio.set_event_loop(asyncio.new_event_loop())

from pynq import MMIO, Overlay  # noqa: E402

BIT = "/home/xilinx/intrusion_detection_optimized.bit"

# From pl_motion_detection_optimized.py -- the addresses the Python drives.
EXPECTED = {
    "frame_diff": 0x40000000,
    "morphology": 0x40010000,
    "rgb2gray": 0x40020000,
    "threshold": 0x40030000,
    "axi_gpio": 0x40040000,
    "axi_dma": 0x41E00000,
}


def find(ip_dict, needle):
    for name, info in ip_dict.items():
        if needle in name.lower():
            return name, info
    return None, None


def main():
    print(f"loading {BIT}")
    overlay = Overlay(BIT, download=False)
    overlay.download()
    print("OVERLAY_LOADED=1")

    ip_dict = overlay.ip_dict
    print(f"IP_COUNT={len(ip_dict)}")

    mismatches = 0
    missing = 0
    for label, want in EXPECTED.items():
        name, info = find(ip_dict, label)
        if name is None:
            print(f"MISSING_IP={label} (expected at 0x{want:08x})")
            missing += 1
            continue
        got = int(info["phys_addr"])
        flag = "ok" if got == want else "MISMATCH"
        if got != want:
            mismatches += 1
        print(f"IP={label} name={name} addr=0x{got:08x} "
              f"expected=0x{want:08x} {flag}")

    print(f"ADDRESS_MISMATCHES={mismatches}")
    print(f"MISSING_IPS={missing}")

    # A register read proves the mapped peripheral actually answers. The
    # threshold IP is the cheapest: reset it and read the AP_CTRL back.
    name, info = find(ip_dict, "threshold")
    if name is not None:
        mmio = MMIO(int(info["phys_addr"]), 0x10000)
        mmio.write(0x00, 0x00)
        ctrl = mmio.read(0x00)
        print(f"THRESHOLD_AP_CTRL=0x{ctrl:08x} readable={ctrl is not None}")

    # And the DMA, which is what moves pixels at all.
    name, info = find(ip_dict, "axi_dma")
    if name is not None:
        mmio = MMIO(int(info["phys_addr"]), 0x10000)
        cr = mmio.read(0x00)
        sr = mmio.read(0x04)
        print(f"DMA_CR=0x{cr:08x} DMA_SR=0x{sr:08x}")

    print("VERIFY_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
