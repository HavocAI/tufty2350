"""How much of the light sensor's view a lit screen fills: the geometry behind
the simulator's predicted swings. Not a test, a tool:

    python3 tests/share_wifi/optics.py

The sensor counts illuminance, and a lit surface of luminance L fills its
view by a projected solid angle P (sr): E = L x P, and P is at most pi. So the
swing a sensor sees from a screen scales with P -- and the ratio of two P's
needs no absolute brightness at all. A mirror run measured the swing at one
geometry; this predicts it at others. See .agents/docs/optical-coupling.md.

Sensor at the origin facing +z; the lit region in the plane z = h. A
rectangle x0..x1, y0..y1 by superposition of the corner form factor (the
differential-area-to-parallel-rectangle view factor, times pi). A cosine
sensor is assumed; a narrower one favours light from straight ahead.
"""
import math

W, H = 56.9, 42.7           # active area, mm (badgeware-hardware.md)
GAP = 1.0                   # sensor outside the right edge, mm [measured-all]

def corner(a, b, h):
    """Projected solid angle of [0,a] x [0,b] at height h (signed a, b)."""
    if a == 0 or b == 0:
        return 0.0
    A, B = abs(a) / h, abs(b) / h
    f = (A / math.sqrt(1 + A * A) * math.atan(B / math.sqrt(1 + A * A))
         + B / math.sqrt(1 + B * B) * math.atan(A / math.sqrt(1 + B * B))) / 2
    return math.copysign(1, a) * math.copysign(1, b) * f

def rect(x0, x1, y0, y1, h):
    return (corner(x1, y1, h) - corner(x0, y1, h) - corner(x1, y0, h)
            + corner(x0, y0, h))

def screen(h, edge=-GAP, lit=((0.0, 1.0),)):
    """The screen whose right edge is at x=edge, lit across these fractions
    of its width measured from the right edge."""
    total = 0.0
    for f0, f1 in lit:
        total += rect(edge - f1 * W, edge - f0 * W, -H / 2, H / 2, h)
    return total

BANDS = ((0.0, 0.25), (0.75, 1.0))     # the old HIRES bands, each a quarter
FULL = ((0.0, 1.0),)
if __name__ == "__main__":
    print("full hemisphere: pi =", round(math.pi, 3))
    print("\nmirror: image plane at h = 2 x (air gap + glass/n)")
    for label, h in (("flat, h=3.5", 3.5), ("flat, h=5.3", 5.3), ("flat, h=7", 7.0),
                     ("tilted ~6mm, h=18", 18.0)):
        print("  %-20s bands %.3f  full %.3f" % (label, screen(h, lit=BANDS), screen(h)))
    print("\npeer badge, full screen, h = gap + recesses; edge = peer screen edge vs sensor")
    for h in (3, 4, 5, 7):
        row = ["  h=%d" % h]
        for edge in (-1, 0, 2, 5, 10):
            row.append("edge%+d: %.3f" % (edge, screen(h, edge=edge)))
        print("  ".join(row))
