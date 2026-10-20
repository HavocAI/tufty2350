"""A badge on a mirror, in virtual time, for fast_loopback's and the fast
block's tests: the backlight's PWM slice (double-buffered CC and TOP,
latched at the wrap), a DMA channel paced by its wrap, the DMA ring, and
the simulator's sensor model -- ds/dt = (I - s) * max(s, F) / K, with the
LED driver's ~11% duty threshold -- all on one clock that every call
advances. The subtool runs through the real devserver and host Client, so
the ring interlock (no USB while it runs) is the production one.

Not a test; the runner skips it.
"""

import builtins
import math
import os
import random
import sys
import types
from contextlib import contextmanager

import devlink_host as dh
import simulator as sim
from devlink_fakes import FakePort, Hooks, ds

PWM_SLICE = 0x400A8000 + 5 * 0x14
PWM_CSR, PWM_DIV, PWM_CTR, PWM_CC, PWM_TOP = (PWM_SLICE + 4 * i
                                              for i in range(5))
RING_SAMPLES = 16384
PERIOD_US = 20
COST_US = {"position": 15, "mem": 1, "backlight": 25, "ticks": 5,
           "active": 5, "config": 30}


class Badge:
    """The hardware. `room` and `contrast` in u16 counts, as the simulator
    has them; readings are 12-bit raw, a 16-count step. `sys_hz` is the v2
    badge's 200 MHz, as it reports itself (2026-10-09f) [measured-v2]; v3's
    source sets 250 MHz [source-v3]."""

    def __init__(self, seed=1, room=0.0, contrast=80.0, offset=40.0, sd=6.0,
                 k=sim.SENSOR_K, floor=sim.SENSOR_FLOOR,
                 threshold=sim.LED_THRESHOLD_DUTY, sys_hz=200000000,
                 firmware="v2", flicker=0.0, flicker_hz=100.0):
        self.rng = random.Random(seed)
        # The room's light flickering by this share of itself, a sine at
        # flicker_hz: an LED light on 50 Hz mains by default.
        self.flicker, self.flicker_hz = flicker, flicker_hz
        self.room, self.contrast, self.offset, self.sd = room, contrast, \
            offset, sd
        self.k = k * contrast / 80.0
        self.floor = floor * contrast / 80.0
        self.threshold = threshold
        self.sys_hz = sys_hz
        self.firmware = firmware            # "v3": backlight() has its floor
        self.t = 0.0                        # us
        self.top = self.top_buf = 65535
        self.cc = self.cc_buf = 0
        self.div = 0x10                     # 1.0
        self.next_wrap = self.period()
        self.s = room
        self.sample_t = PERIOD_US
        self.sensor_t = 0.0
        self.ring = Ring(self)
        self.running = False
        self.dma = None                     # [words, next, count]
        self.dma_configs = []
        # Per backlight() call followed straight by a counter read, as
        # A capture logs a change: did the slice wrap between the CC write
        # and that read? A wrap there puts the host's landing a whole period
        # late. Any other call in between forgets the write.
        self.pending = None                 # wraps since the write, or None
        self.straddles = []
        self.top_writes = []                # (TOP, CC's channel A then)

    def period(self):
        return (self.top + 1) * (self.div >> 4) * 1e6 / self.sys_hz

    def light(self):
        duty = min(1.0, (self.cc & 0xFFFF) / (self.top + 1.0))
        return max(0.0, duty - self.threshold) / (1 - self.threshold)

    def advance(self, dt):
        end = self.t + dt
        while True:
            step = min(self.next_wrap, self.sample_t)
            if step > end:
                break
            if self.next_wrap <= self.sample_t:
                self.wrap()
            else:
                self.sample()
        self.t = end

    def wrap(self):
        self.integrate(self.next_wrap)
        self.t = self.next_wrap
        self.cc = self.cc_buf
        self.top = self.top_buf
        if self.pending is not None:
            self.pending += 1
        if self.dma is not None:
            words, i, n = self.dma
            self.cc_buf = words[i]
            self.dma[1] = i + 1
            if i + 1 == n:
                self.dma = None
        self.next_wrap += self.period()

    def integrate(self, to_t):
        """The sensor, from where it was to `to_t`, under the light now."""
        dt = to_t - self.sensor_t
        if dt > 0:
            room = self.room
            if self.flicker:
                room *= 1 + self.flicker * math.sin(
                    2 * math.pi * self.flicker_hz * to_t / 1e6)
            target = room + self.light() * self.contrast
            rate = max(self.s, self.floor) / self.k
            self.s += (target - self.s) * (1 - math.exp(-dt / 1000.0 * rate))
            self.sensor_t = to_t

    def sample(self):
        self.integrate(self.sample_t)
        self.t = self.sample_t
        if self.running:
            value = self.offset + self.s + self.rng.gauss(0, self.sd)
            self.ring.write(max(0, min(4095, int(value) // 16)))
        self.sample_t += PERIOD_US

    # the firmware's calls

    def backlight(self, value):
        """v2's display.backlight(): (b/255)^2.8 onto 0-65535, no floor.
        With firmware "v3", v3's: the same curve onto 8192-65535, a 12.5%
        floor, 0 still off [source-v3; simulator.backlight_duty's formula]."""
        self.advance(COST_US["backlight"])
        b = int(value * 255) & 0xFF
        if self.firmware == "v3":
            level = 8192 + int((b / 255.0) ** 2.8 * (65535 - 8192) + 0.5) \
                if b else 0
        else:
            level = int((b / 255.0) ** 2.8 * 65535 + 0.5)
        self.cc_buf = (self.cc_buf & 0xFFFF0000) | level
        self.pending = 0

    def ticks_ms(self):
        self.pending = None
        self.advance(COST_US["ticks"])
        return int(self.t // 1000)

    def ticks_us(self):
        self.pending = None
        self.advance(COST_US["ticks"])
        return int(self.t)


class Ring:
    def __init__(self, badge):
        self.badge = badge
        self.addr = [0] * RING_SAMPLES
        self.mask = RING_SAMPLES - 1
        self.samples = RING_SAMPLES
        self.period_us = PERIOD_US
        self.count = 0

    def write(self, raw):
        self.addr[self.count & self.mask] = raw
        self.count += 1

    def position(self):
        self.badge.pending = None
        self.badge.advance(COST_US["position"])
        return self.count

    def health(self):
        return 0

    def read(self, index):
        return self.addr[index & self.mask]


class Reader:
    def __init__(self, badge):
        self.badge = badge
        self.ring = badge.ring
        self.running = False
        self.starts = 0

    def start(self):
        self.running = self.badge.running = True
        self.starts += 1

    def stop(self):
        self.running = self.badge.running = False


class Mem:
    def __init__(self, badge):
        self.badge = badge

    def __getitem__(self, addr):
        b = self.badge
        b.advance(COST_US["mem"])
        if addr != PWM_CTR:
            b.pending = None
        if addr == PWM_CSR:
            return 1
        if addr == PWM_DIV:
            return b.div
        if addr == PWM_CTR:
            if b.pending is not None:
                b.straddles.append(b.pending > 0)
                b.pending = None
            left = (b.next_wrap - b.t) / b.period()
            return min(b.top, int((1 - left) * (b.top + 1)))
        if addr == PWM_CC:
            return b.cc_buf
        if addr == PWM_TOP:
            return b.top_buf
        raise KeyError(hex(addr))

    def __setitem__(self, addr, value):
        b = self.badge
        b.advance(COST_US["mem"])
        if addr == PWM_CC:
            b.cc_buf = value
        elif addr == PWM_TOP:
            b.top_buf = value
            b.top_writes.append((value, b.cc_buf & 0xFFFF))
        else:
            raise KeyError(hex(addr))


class DMA:
    """rp2.DMA as fasttx uses it: CC words, paced by slice 5's wrap."""

    badge = None
    fail_on = None              # raise from the nth config(), for tests

    def pack_ctrl(self, **fields):
        return fields

    def config(self, read, write, count, ctrl, trigger):
        b = self.badge
        b.dma_configs.append(ctrl)
        if DMA.fail_on is not None and len(b.dma_configs) >= DMA.fail_on:
            raise OSError("injected DMA fault")
        assert write == PWM_CC and ctrl["treq_sel"] == 37 and trigger
        assert ctrl["size"] == 2 and not ctrl["inc_write"]
        b.advance(COST_US["config"])
        b.dma = [list(read), 0, count]

    def active(self, value=None):
        self.badge.advance(COST_US["active"])
        if value is not None:
            if not value:
                self.badge.dma = None
            return None
        return self.badge.dma is not None

    def close(self):
        pass


class Screen:
    pen = None

    def clear(self):
        pass


class Color:
    @staticmethod
    def rgb(r, g, b):
        return (r, g, b)


def _identity(value):
    return value


@contextmanager
def installed(badge):
    """The badge's modules and globals, for as long as a subtool runs."""
    machine = types.ModuleType("machine")
    machine.mem32 = machine.mem16 = Mem(badge)
    machine.freq = lambda: badge.sys_hz
    micropython = types.ModuleType("micropython")
    micropython.viper = micropython.native = _identity
    rp2 = types.ModuleType("rp2")
    DMA.badge = badge
    rp2.DMA = DMA
    mods = {"machine": machine, "micropython": micropython, "rp2": rp2}
    names = {"ptr8": _identity, "ptr16": _identity, "ptr32": _identity,
             "uint": _identity, "screen": Screen(), "color": Color}
    saved_mods = {n: sys.modules.get(n) for n in mods}
    saved_names = {n: getattr(builtins, n, None) for n in names}
    sys.modules.update(mods)
    for name, value in names.items():
        setattr(builtins, name, value)
    try:
        yield
    finally:
        DMA.fail_on = None
        for name, module in saved_mods.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        for name, value in saved_names.items():
            if value is None:
                delattr(builtins, name)
            else:
                setattr(builtins, name, value)


class Pipe:
    """The host's serial port, the server on its far end."""

    def __init__(self, server, port):
        self.server, self.port = server, port

    def write(self, data):
        self.port.inbox.extend(data)

    def read(self, n):
        if not self.port.outbox:
            self.server.service()
        data = bytes(self.port.outbox[:n])
        del self.port.outbox[:n]
        return data

    def reset_input_buffer(self):
        self.port.outbox = bytearray()

    def close(self):
        pass


def tool_path(name):
    return os.path.join(dh.TOOLS, name + ".py")
