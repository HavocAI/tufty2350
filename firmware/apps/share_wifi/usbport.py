"""The USB serial as the dev link uses it: badge-only, like fastadc.py.

    open()    Ctrl-C off (micropython.kbd_intr(-1)), stdin registered for
              polling, and whatever is already queued thrown away
    read(n)   at most n bytes that have arrived, never waiting: one byte at
              a time, each only after poll says one is there, because
              sys.stdin.buffer.read(n) blocks until all n arrive
    write(b)  True if every byte went: a host that holds the port open and
              stops reading makes the firmware give up after 500 ms and
              return short
    close()   the cancel disarmed and Ctrl-C back on (kbd_intr(3), what
              pyexec set for main.py)

and the hard cancel for a custom run, armed only around run(api, args):

    arm_cancel()     B gets a Pin IRQ and 0x03 becomes the interrupt
                     character, so the host's raw 0x03 interrupts the run
    disarm_cancel()  undone, kbd_intr(-1) first

A soft IRQ handler cannot raise into the code it interrupted (the scheduler
calls it through mp_call_function_1_protected, which prints and swallows).
What it can do is hand the firmware an interrupt character: B's handler
attaches CancelTerm, a dupterm stream that reads one 0x03, and calls
os.dupterm_notify(), which finds 0x03 == the interrupt character and
schedules KeyboardInterrupt (mp_os_dupterm_rx_chr, extmod/os_dupterm.c).

Every dependency is handed in, so the logic is tested under CPython; the
firmware's own modules are imported only by badge_port().

Specification: "USB dev link" in .agents/share_wifi/app-spec.md.
"""

import io

INTR = 3                # Ctrl-C: the interrupt character while armed
READ_MAX = 2048
DRAIN_MS = 100


class CancelTerm(io.IOBase):
    """A dupterm stream that reads one byte, INTR, once each time it is
    primed, and swallows writes.

    The firmware calls readinto(buf) with a 1-byte bytearray and needs an
    int back -- the count -- or None for "nothing now" (py/modio.c,
    iobase_read_write). bytes back would be a TypeError, and the firmware
    would print "dupterm: Exception in read() method, deactivating" and
    never schedule the interrupt. Nothing here may raise for the same
    reason.
    """

    def __init__(self):
        self.pending = False

    def readinto(self, buf):
        if self.pending:
            self.pending = False
            buf[0] = INTR
            return 1
        return None

    def write(self, buf):
        return len(buf)

    def ioctl(self, _request, _arg):
        return 0


class UsbPort:
    def __init__(self, pin, stdin, stdout, poller, pollin, kbd_intr,
                 dupterm, notify, sleep_ms, ticks_ms, ticks_diff):
        self.pin = pin
        self.stdin = stdin
        self.stdout = stdout
        self.poller = poller
        self.pollin = pollin
        self.kbd_intr = kbd_intr
        self.dupterm = dupterm
        self.notify = notify
        self.sleep_ms = sleep_ms
        self.ticks_ms = ticks_ms
        self.ticks_diff = ticks_diff
        self.one = bytearray(1)
        self.buf = bytearray(READ_MAX)
        self.term = CancelTerm()
        self.armed = False
        self.b_fired = False
        self.opened = False
        # Bound once: an IRQ must not allocate a bound method per press.
        self.handler = self.b_irq

    # -- the port

    def open(self):
        self.kbd_intr(-1)
        if not self.opened:
            self.poller.register(self.stdin, self.pollin)
            self.opened = True
        self.drain()

    def ready(self):
        for _ in self.poller.ipoll(0):
            return True
        return False

    def read(self, limit=READ_MAX):
        n = 0
        limit = min(limit, READ_MAX)
        one = self.one
        buf = self.buf
        while n < limit and self.ready():
            self.stdin.readinto(one)
            buf[n] = one[0]
            n += 1
        return bytes(buf[:n]) if n else b""

    def drain(self):
        start = self.ticks_ms()
        while self.read(READ_MAX):
            if self.ticks_diff(self.ticks_ms(), start) > DRAIN_MS:
                break

    def write(self, data):
        try:
            written = self.stdout.write(data)
        except OSError:
            return False
        return written == len(data)

    def close(self):
        self.disarm_cancel()
        self.kbd_intr(INTR)

    # -- the hard cancel

    def arm_cancel(self):
        self.b_fired = False
        self.term.pending = False
        self.armed = True
        self.pin.irq(trigger=self.pin.IRQ_FALLING, handler=self.handler)
        self.kbd_intr(INTR)

    def disarm_cancel(self):
        # kbd_intr(-1) first: from then on neither the handler nor a host
        # 0x03 can make a new interrupt. Then the IRQ, then a short sleep
        # inside a try, which raises an interrupt already scheduled here
        # rather than in whatever runs next.
        self.kbd_intr(-1)
        self.armed = False
        self.pin.irq(handler=None)
        try:
            self.sleep_ms(1)
        except KeyboardInterrupt:
            pass

    def b_irq(self, _pin):
        # A falling edge, whichever way B is wired: bounce gives several,
        # and the first disarms, so one run gets one interrupt at most.
        if not self.armed:
            return
        self.armed = False
        self.b_fired = True
        term = self.term
        term.pending = True
        previous = self.dupterm(term, 0)
        self.notify(None)
        self.dupterm(previous, 0)
        # Nothing after notify() may branch or loop: it is safe only because
        # the scheduler is locked while a soft handler runs, so the
        # interrupt notify() scheduled is raised after this returns.


def badge_port(pin):
    """The real port, over the firmware's modules."""
    import micropython
    import os
    import select
    import sys
    import time
    return UsbPort(pin, sys.stdin.buffer, sys.stdout.buffer, select.poll(),
                   select.POLLIN, micropython.kbd_intr, os.dupterm,
                   os.dupterm_notify, time.sleep_ms, time.ticks_ms,
                   time.ticks_diff)
