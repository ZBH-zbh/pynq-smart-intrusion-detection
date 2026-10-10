"""Stop the demo server and make sure nothing is left holding the hardware.

pkill does not run the process's cleanup, so if the alarm happened to be
asserted at that moment the buzzer GPIO stays high and keeps sounding. Force it
low afterwards and read it back to prove it.

Stopping the server also releases /dev/video0, which only allows one user.
"""
import asyncio
import sys

sys.path.insert(0, "/home/xilinx/intrusion_demo")

asyncio.set_event_loop(asyncio.new_event_loop())

from pynq import MMIO  # noqa: E402

BUZZER_BASE = 0x40040000


def main():
    mmio = MMIO(BUZZER_BASE, 0x10000)
    before = mmio.read(0x00)
    print(f"buzzer before: 0x{before:08x} "
          f"({'ASSERTED' if before else 'off'})")
    mmio.write(0x00, 0x00)
    after = mmio.read(0x00)
    print(f"buzzer after : 0x{after:08x} "
          f"({'STILL ON' if after else 'off'})")
    if after:
        raise SystemExit("buzzer would not turn off")


if __name__ == "__main__":
    main()
