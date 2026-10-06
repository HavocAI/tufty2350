"""Survey control-pattern designs: which ones are legal, and which ones work.

    python3 tests/share_wifi/pattern_survey.py             # the standard survey
    python3 tests/share_wifi/pattern_survey.py --seeds 16  # more evidence
    python3 tests/share_wifi/pattern_survey.py --quick     # legality only, no runs
    python3 tests/share_wifi/pattern_survey.py --guards    # only the guard designs

The control patterns were never justified beyond "every pattern contains black,
so payload data cannot forge it". That rules out a lot of designs and chooses
none of them, so this exists to choose: it puts a candidate set through a static
legality check and then through the real protocol in the simulator, and prints
what happened.

Two stages, because they catch different things and one is far cheaper:

- `audit()` is static and exact. It proves a set cannot work, and it is the only
  thing here that can prove anything -- the rules it checks are properties of the
  wire format, not of any particular run.
- `measure()` runs the real Transfer through the simulator. It cannot prove a set
  is good, only that it survived some seeds, so it reports the sample size and
  the invariant breaks alongside the delivery rate.

**A pass here means "agrees with our model of the hardware", not "works".** The
panel constants are still unmeasured and nothing in this repo has run on a badge.

What it found, 8 seeds x 6 scenarios x 2 payloads = 96 runs each:

    current                    90/96 (93.8%)  overhead 47 slots
    short ack                  90/96 (93.8%)           47
    3 repetitions              90/96 (93.8%)           47
    2 repetitions              87/96 (90.6%)           38
    short verdicts             85/96 (88.5%)           44
    black-white guard, lead    84/96 (87.5%)           51
    2 black guard, lead        79/96 (82.3%)           51
    black-grey / grey-black    65/96 (67.7%)           57
    flag ' - ' guard, lead     72/96 (75.0%)           53
    grey-black / black-grey     8/96 ( 8.3%)           57
    black-white guard, trail   21/96 (21.9%)           53
    any guard at both ends    4-6/96 (<7%)             57-62

**No guard is an improvement.** Every one of them costs delivery, and the
patterns already do the job a guard would: each ends in black, which separates
it from whatever follows, and each begins with a data symbol after silence,
which is what distinguishes it from an idle screen. A guard duplicates that and
pays for it. The best guard measured was still six points below no guard at all.

The one sharp discriminator is whether a trailing guard ends in BLACK or in a
data symbol. `grey-black / black-grey` delivered 8/96 while its mirror
`black-grey / grey-black` delivered 65/96; the difference is that the second
ends in black, so the match strips it and the sender has nothing left to
transmit when the peer replies. Guards ending in a data symbol lose the
separator and collapse. Beyond that the cost of leading guards is measured but
not explained -- do not assume a mechanism.

Shortening is a different question and mostly answered no as well, except for
the Complete Ack Pattern, which shrank from 8 symbols to 3 with no measurable
cost in any run.
"""

import sys
import os

# This imports from firmware/apps/, which is packed onto the badge -- so leave
# no __pycache__ behind there. See tests/run_tests.py.
sys.dont_write_bytecode = True

# Runnable on its own, so it puts the shared harness on the path itself
# rather than relying on the runner having done it. `tests/` is where the
# harness lives -- beside the runner, shared by every app's tests.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import simulator                                    # noqa: E402
from harness import load_module                     # noqa: E402

LOW, MID, HIGH = " ", "_", "-"
DATA = (MID, HIGH)

FIELDS = ("BEGIN_BLOCK", "END_BLOCK", "CRC_GOOD", "CRC_BAD",
          "SYNC_ERROR", "COMPLETE", "COMPLETE_ACK")

# Patterns whose match strips the trailing black. Kept in step with protocol.py:
# the Begin Block Pattern is deliberately absent, because its trailing black
# separates the pattern from the payload and matching a slot early would take it
# for a Field Separator.
STRIPPED = ("CRC_GOOD", "CRC_BAD", "SYNC_ERROR", "COMPLETE", "COMPLETE_ACK")

CURRENT = dict(BEGIN_BLOCK="-_ -_ -_ ", END_BLOCK="_- _- _- ",
               CRC_GOOD="-- -- -- ", CRC_BAD="__ __ __ ",
               SYNC_ERROR="  _  _  _", COMPLETE="- - - ",
               COMPLETE_ACK="__- __- ")


def strip_black(pattern):
    while pattern.endswith(LOW):
        pattern = pattern[:-1]
    return pattern


def matched(name, pattern):
    """What a receiver actually compares against."""
    return strip_black(pattern) if name in STRIPPED else pattern


# --- static legality ---------------------------------------------------------

def _first_match(tail, candidates):
    for name, want in candidates:
        if len(tail) >= len(want) and "".join(tail[-len(want):]) == want:
            return name
    return None


def _confusable(state, candidates, on_air, lead_black=6):
    """Send each pattern a symbol at a time; the first match must be itself."""
    out = []
    for name, pattern in on_air.items():
        stream = [LOW] * lead_black
        for symbol in pattern:
            stream.append(symbol)
            hit = _first_match(stream, candidates)
            if hit is None:
                continue
            if hit != name:
                out.append("%s: sending %s reads as %s" % (state, name, hit))
            break
    return out


def audit(P):
    """Every way a pattern set is provably broken, as a list of strings."""
    out = []

    for name in FIELDS:
        pattern = P[name]
        tail = matched(name, pattern)

        # Payload data is only ever MID and HIGH, so black inside the compared
        # string is what makes a pattern unforgeable. Black that gets stripped
        # before comparison does not count.
        if LOW not in tail:
            out.append("%s: compared as %r, which has no black -- payload "
                       "data could forge it" % (name, tail))

        # A receiver acts when the match fires, and replies on the next slot
        # boundary. Anything the sender still has queued after that goes out
        # while the peer is already answering -- and a badge that is
        # transmitting ignores what it sees, so the reply's opening symbols are
        # eaten. One slot of overlap survives; two does not. Measured: two
        # trailing blacks cost 144 runs out of 144, the Leader reading the
        # acknowledgement exactly one symbol short every time.
        overlap = len(pattern) - len(tail)
        if overlap > 1:
            out.append("%s: %d slots still to send after the match fires; the "
                       "reply starts inside them" % (name, overlap))


    # The End Block Pattern is told from a Field Separator only by its two
    # opening data symbols landing at a two-bit offset. Payload can legitimately
    # carry consecutive separators -- an empty field -- so a leading black here
    # is genuinely ambiguous, not merely awkward to parse.
    end = P["END_BLOCK"]
    if len(end) < 3 or end[0] not in DATA or end[1] not in DATA or end[2] != LOW:
        out.append("END_BLOCK: %r must open with two data symbols then black, "
                   "or it cannot be told from a Field Separator" % end)
    elif end[:2] != MID + HIGH:
        out.append("END_BLOCK: opens %r, but collect_block keys on '_-'"
                   % end[:2])

    # Mutual confusability, in the order each state actually checks.
    out += _confusable(
        "STEP_BLOCK",
        [("COMPLETE", matched("COMPLETE", P["COMPLETE"])),
         ("BEGIN_BLOCK", matched("BEGIN_BLOCK", P["BEGIN_BLOCK"]))],
        {k: P[k] for k in ("COMPLETE", "BEGIN_BLOCK", "SYNC_ERROR")})
    out += _confusable(
        "STEP_VERDICT",
        [(k, matched(k, P[k]))
         for k in ("CRC_GOOD", "CRC_BAD", "SYNC_ERROR", "COMPLETE")],
        {k: P[k] for k in ("CRC_GOOD", "CRC_BAD", "SYNC_ERROR", "COMPLETE")})
    out += _confusable(
        "STEP_FINISH",
        [("COMPLETE_ACK", matched("COMPLETE_ACK", P["COMPLETE_ACK"]))],
        {"COMPLETE_ACK": P["COMPLETE_ACK"]})
    return out


# --- running a set for real --------------------------------------------------

def install(protocol, P):
    """Patch a freshly loaded protocol module to use this pattern set."""
    for name in FIELDS:
        setattr(protocol, name, P[name])
    for name in STRIPPED:
        setattr(protocol, name + "_TAIL", strip_black(P[name]))

    # These are round-trip budgets over the pattern lengths, not free constants.
    # protocol.py hard-codes values that suit the patterns it ships with; a
    # survey that left them fixed would blame the pattern for a budget that no
    # longer covers the exchange. That mistake cost a whole run of this survey.
    protocol.SYNC_LEAD_IN = max(len(P["SYNC_ERROR"]), 9)
    protocol.COMPLETE_RETRY_SLOTS = len(P["COMPLETE_ACK"]) + 4
    protocol.LINGER_SLOTS = (protocol.COMPLETE_RETRY_SLOTS
                             + len(P["COMPLETE"]) + 4)
    return protocol


def overhead(P):
    """Slots spent per block on everything that is not payload."""
    return (4 + len(P["BEGIN_BLOCK"]) + len(P["END_BLOCK"]) + 16
            + len(P["CRC_GOOD"]))


PAYLOADS = ((simulator.TYPICAL, 200), (simulator.WORST, 400))


def measure(P, seeds, scenarios=None):
    protocol = install(load_module("share_wifi", "protocol"), P)
    scenarios = scenarios or simulator.SCENARIOS
    delivered = runs = 0
    breaks = {}
    for payload, limit in PAYLOADS:
        for kw in scenarios.values():
            for seed in range(1, seeds + 1):
                link = simulator.build(protocol, payload, seed=seed, **kw)
                link.run(limit)
                got = protocol.parse_payload(link.follower.transfer.received)
                want = list(payload)
                runs += 1
                if (link.leader.transfer.result == protocol.OK
                        and link.follower.transfer.result == protocol.OK
                        and got == want):
                    delivered += 1
                if link.violation:
                    kind = link.violation.split(" violated")[0]
                    breaks[kind] = breaks.get(kind, 0) + 1
                # A Leader saying OK over a Follower without the payload is the
                # one outcome that must never happen, so it is counted whether
                # or not the tick-by-tick checker saw it first.
                if link.leader.transfer.result == protocol.OK and got != want:
                    breaks["I7"] = breaks.get("I7", 0) + 1
    return delivered, runs, breaks


# --- the variations ----------------------------------------------------------

def repeated(reps, complete="- - - ", ack="__- __- "):
    return dict(BEGIN_BLOCK="-_ " * reps, END_BLOCK="_- " * reps,
                CRC_GOOD="-- " * reps, CRC_BAD="__ " * reps,
                SYNC_ERROR="  _" * reps, COMPLETE=complete, COMPLETE_ACK=ack)


def guarded(P, lead="", trail="", skip_end_block=True):
    """Wrap each pattern in a leading and/or trailing guard sequence.

    The two ends take separate guards, because they are not the same problem.
    A leading guard has to be told apart from an idle run of black; a trailing
    one has to leave the sender with no more than one slot to send once the
    receiver has matched. A design can easily be right at one end and wrong at
    the other, so mirrored pairs are worth trying rather than assuming symmetry.

    `skip_end_block` leaves the End Block Pattern's opening alone, because it is
    the one pattern that cannot carry a leading guard at all -- see audit().
    """
    out = {}
    for name, pattern in P.items():
        opening = "" if (skip_end_block and name == "END_BLOCK") else lead
        out[name] = opening + pattern + trail
    return out


def same_guard(P, guard, where, skip_end_block=True):
    """The symmetric case: one guard used at whichever ends `where` names."""
    return guarded(P,
                   lead=guard if where in ("lead", "both") else "",
                   trail=guard if where in ("trail", "both") else "",
                   skip_end_block=skip_end_block)


# Candidate guards. A guard has to be distinguishable from the two things that
# can precede a control pattern -- an idle run of black, and a payload bit
# stream -- so the interesting ones mix black with data symbols. Pure black is
# included precisely because it is the obvious idea and it does not work.
GUARDS = [("2 black", "  "), ("1 black", " "), ("black grey", " _"),
          ("black white", " -"), ("grey black", "_ "), ("white black", "- "),
          ("flag ' - '", " - "), ("flag ' _ '", " _ ")]


# Mirrored pairs: the guard at one end is the reverse of the guard at the
# other, so the pattern is bracketed symmetrically in appearance even though the
# two ends are solving different problems. Whether the black faces inward or
# outward is the whole question, and it is not obvious from reading.
MIRRORED = [
    ("grey-black / black-grey", "_ ", " _"),
    ("white-black / black-white", "- ", " -"),
    ("black-grey / grey-black", " _", "_ "),
]


def variations(only_guards=False):
    if only_guards == "mirrored":
        yield "current (baseline)", CURRENT
        for label, lead, trail in MIRRORED:
            yield label, guarded(CURRENT, lead=lead, trail=trail)
        return
    if not only_guards:
        yield "current", CURRENT
        for reps in (1, 2, 3):
            yield "%d repetitions" % reps, repeated(reps)
        yield "short ack", dict(CURRENT, COMPLETE_ACK=" _ ")
        yield "short verdicts", dict(CURRENT, CRC_GOOD="-- -- ",
                                     CRC_BAD="__ __ ")
    for label, guard in GUARDS:
        for where in ("lead", "trail", "both"):
            yield "%s, %s" % (label, where), same_guard(CURRENT, guard, where)
    for label, lead, trail in MIRRORED:
        yield label, guarded(CURRENT, lead=lead, trail=trail)


def main(argv):
    seeds = 4
    if "--seeds" in argv:
        seeds = int(argv[argv.index("--seeds") + 1])
    quick = "--quick" in argv
    only_guards = "--guards" in argv
    if "--mirrored" in argv:
        only_guards = "mirrored"

    print("%-24s %-7s %-17s %-5s %s"
          % ("variation", "legal", "delivered", "ovh", "notes"))
    print("-" * 88)
    for label, P in variations(only_guards):
        problems = audit(P)
        if problems or quick:
            note = problems[0] if problems else ""
            print("%-24s %-7s %-17s %-5d %s"
                  % (label, "no" if problems else "yes", "-", overhead(P), note))
            for extra in problems[1:]:
                print("%-24s %-7s %-17s %-5s %s" % ("", "", "", "", extra))
            continue
        delivered, runs, breaks = measure(P, seeds)
        print("%-24s %-7s %3d/%-3d (%5.1f%%)  %-5d %s"
              % (label, "yes", delivered, runs, 100.0 * delivered / runs,
                 overhead(P), breaks or "no invariant breaks"))

    print("\n%d seeds x %d scenarios x %d payloads per legal variation."
          % (seeds, len(simulator.SCENARIOS), len(PAYLOADS)))
    print("Delivery counts both badges reporting OK AND the payload arriving.")


if __name__ == "__main__":
    main(sys.argv[1:])
