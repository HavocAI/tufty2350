"""usbport.py: the USB serial as the dev link uses it, and the hard cancel.

The firmware is faked here, but the dupterm fake follows the firmware's own
contract (py/modio.c iobase_read_write, extmod/os_dupterm.c
mp_os_dupterm_rx_chr): readinto() is called with a 1-byte bytearray and its
result must be an int (or None, EAGAIN) -- bytes back would be a TypeError
there and deactivate the stream with no interrupt. CancelTerm itself is also
run under the real MicroPython unix port, where it is found.
"""

import importlib.util
import operator
import os
import shutil
import subprocess
import tempfile

from harness import APPS, Skipped

# From the file, not sys.modules: the app's tests put a stand-in there.
_spec = importlib.util.spec_from_file_location(
    "usbport_under_test", os.path.join(APPS, "share_wifi", "usbport.py"))
up = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(up)


class Firmware:
    """The parts of MicroPython the port reaches: the interrupt character,
    one dupterm slot, dupterm_notify, and a log of what was called."""

    def __init__(self):
        self.intr = 3
        self.slot = None
        self.interrupts = 0
        self.stdin = bytearray()
        self.calls = []
        self.deactivated = 0

    def kbd_intr(self, c):
        self.calls.append(("kbd_intr", c))
        self.intr = c

    def dupterm(self, term, index=0):
        previous = self.slot
        self.slot = term
        self.calls.append(("dupterm", term))
        return previous

    def notify(self, _arg):
        # mp_os_dupterm_notify: read chars until none; one that is the
        # interrupt character schedules KeyboardInterrupt and stops.
        term = self.slot
        while term is not None:
            buf = bytearray(1)
            try:
                result = term.readinto(buf)
                if result is None:
                    return                  # EAGAIN: nothing now
                count = operator.index(result)      # mp_obj_get_int
            except Exception:   # noqa: BLE001 - as the firmware does
                self.deactivated += 1
                self.slot = None
                return
            if count == 0:
                self.deactivated += 1
                self.slot = None
                return
            if buf[0] == self.intr:
                self.interrupts += 1
                return
            self.stdin.append(buf[0])

    def sleep_ms(self, ms):
        self.calls.append(("sleep", ms))


class Pin:
    IRQ_FALLING = 2

    def __init__(self):
        self.handler = None
        self.calls = []

    def irq(self, trigger=None, handler=None):
        self.calls.append((trigger, handler))
        self.handler = handler


class Poller:
    def __init__(self, stdin):
        self.stdin = stdin
        self.registered = []

    def register(self, obj, flags):
        self.registered.append((obj, flags))

    def ipoll(self, timeout):
        return iter([(self.stdin, 1)] if self.stdin.data else [])


class Stdin:
    def __init__(self, data=b""):
        self.data = bytearray(data)

    def readinto(self, buf):
        buf[0] = self.data.pop(0)
        return 1


class Stdout:
    def __init__(self):
        self.data = bytearray()
        self.short = False
        self.fail = False

    def write(self, data):
        if self.fail:
            raise OSError(5)
        if self.short:
            self.data.extend(data[:1])
            return 1
        self.data.extend(data)
        return len(data)


def port(data=b""):
    fw = Firmware()
    pin = Pin()
    stdin = Stdin(data)
    stdout = Stdout()
    clock = [0]
    p = up.UsbPort(pin, stdin, stdout, Poller(stdin), 1, fw.kbd_intr,
                   fw.dupterm, fw.notify, fw.sleep_ms,
                   lambda: clock[0], lambda a, b: a - b)
    return p, fw, pin, stdin, stdout


def test_cancel_term_follows_the_firmware_contract():
    term = up.CancelTerm()
    buf = bytearray(1)
    assert term.readinto(buf) is None
    term.pending = True
    assert term.readinto(buf) == 1 and buf[0] == 3
    assert term.readinto(buf) is None
    assert term.write(b"abc") == 3 and term.ioctl(1, 0) == 0


def test_the_contract_fake_catches_a_term_that_returns_bytes():
    class Wrong(up.CancelTerm):
        def readinto(self, buf):
            return b"\x03"
    fw = Firmware()
    fw.dupterm(Wrong())
    fw.notify(None)
    assert fw.interrupts == 0 and fw.deactivated == 1


def test_open_turns_ctrl_c_off_registers_and_drains():
    p, fw, pin, stdin, stdout = port(b"stale run clear\n")
    p.open()
    assert fw.calls[0] == ("kbd_intr", -1)
    assert p.poller.registered and not stdin.data
    p.close()
    assert fw.calls[-1] == ("kbd_intr", 3)


def test_read_takes_only_what_has_arrived_up_to_the_limit():
    p, fw, pin, stdin, stdout = port(b"abcdef")
    assert p.read(4) == b"abcd"
    assert p.read(10) == b"ef"
    assert p.read(10) == b""


def test_a_short_or_failed_write_says_so():
    p, fw, pin, stdin, stdout = port()
    assert p.write(b"abc")
    stdout.short = True
    assert not p.write(b"abc")
    stdout.short = False
    stdout.fail = True
    assert not p.write(b"abc")


def test_b_while_armed_interrupts_once_and_restores_dupterm():
    p, fw, pin, stdin, stdout = port()
    previous = object()
    fw.slot = previous
    p.arm_cancel()
    assert fw.intr == 3 and pin.handler is p.handler
    pin.handler(pin)
    assert fw.interrupts == 1 and p.b_fired
    assert fw.slot is previous, "the slot it found is put back"
    pin.handler(pin)            # bounce: a second edge, already disarmed
    assert fw.interrupts == 1


def test_disarm_turns_ctrl_c_off_first_then_the_irq():
    p, fw, pin, stdin, stdout = port()
    p.arm_cancel()
    fw.calls = []
    p.disarm_cancel()
    assert fw.calls[0] == ("kbd_intr", -1)
    assert pin.handler is None and fw.calls[-1][0] == "sleep"
    # A handler already queued finds it disarmed and does nothing.
    p.handler(pin)
    assert fw.interrupts == 0


def test_after_disarm_the_byte_is_only_noise():
    p, fw, pin, stdin, stdout = port()
    p.arm_cancel()
    p.armed = True              # as if the handler ran after kbd_intr(-1)
    fw.intr = -1
    p.handler(pin)
    assert fw.interrupts == 0 and fw.stdin == bytearray(b"\x03")


def test_arming_clears_the_last_runs_flags():
    p, fw, pin, stdin, stdout = port()
    p.arm_cancel()
    pin.handler(pin)
    p.disarm_cancel()
    p.arm_cancel()
    assert not p.b_fired and not p.term.pending and p.armed


UNIX_SCRIPT = r"""
import sys, io
sys.path.insert(0, %r)
import usbport
t = usbport.CancelTerm()
print(isinstance(t, io.IOBase))
b = bytearray(1)
print(t.readinto(b))
t.pending = True
print(t.readinto(b), b[0])
print(t.readinto(b), t.write(b"ab"), t.ioctl(1, 0))
"""


def test_cancel_term_under_micropython():
    tool = os.environ.get("MICROPYTHON") or shutil.which("micropython")
    if not tool or not os.path.exists(tool):
        raise Skipped("no MicroPython unix port: set MICROPYTHON")
    with tempfile.TemporaryDirectory() as out:
        script = os.path.join(out, "u.py")
        with open(script, "w") as handle:
            handle.write(UNIX_SCRIPT % os.path.join(APPS, "share_wifi"))
        result = subprocess.run([tool, script], capture_output=True,
                                text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n")[:4] == ["True", "None", "1 3",
                                             "None 2 0"], result.stdout
