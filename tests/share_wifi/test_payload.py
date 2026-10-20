"""The payload: a list of byte fields, sent as one fast stream -- the field
count, then each field's length and bytes, all varints -- cut into the fast
blocks' chunks.

The protocol carries fields and attaches no meaning to them, so what is
tested here is structure. Whether the fields are a usable WiFi credential is
the app's question, and lives in test_app.py.
"""

import random

import held
from harness import load_module

protocol = load_module("share_wifi", "protocol")
FB = load_module("share_wifi", "fastblock")

VALID = [
    (b"HomeNet", b"hunter2!!"),
    (b"A" * 32, b"k" * 63),            # longest legal SSID and passphrase
    (b"A" * 32, b"k" * 64),            # 64 byte hex PSK
    (b"x", b"12345678"),               # shortest legal of each
    (b"\xff\xfe raw", b"password1"),   # SSIDs are octets, not text
]


def test_round_trip():
    for fields in VALID:
        stream = protocol.fast_stream(list(fields))
        assert protocol.parse_fast_stream(stream) == list(fields)


def test_any_number_of_fields_round_trips():
    for count in (0, 1, 2, 3, 7, 200):
        fields = [bytes([i % 256]) * (i % 5) for i in range(count)]
        stream = protocol.fast_stream(fields)
        assert protocol.parse_fast_stream(stream) == fields


def test_empty_fields_survive():
    fields = [b"a", b"", b"c", b""]
    assert protocol.parse_fast_stream(protocol.fast_stream(fields)) == fields


def test_lengths_past_a_byte_take_more_varint_bytes():
    stream = protocol.fast_stream([b"x" * 300])
    assert stream[:3] == bytes([1, 300 & 127 | 128, 300 >> 7])
    assert protocol.parse_fast_stream(stream) == [b"x" * 300]


def test_a_stream_that_is_short_or_long_is_not_a_payload():
    stream = protocol.fast_stream([b"HomeNet", b"hunter2!!"])
    assert protocol.parse_fast_stream(stream[:-1]) is None
    assert protocol.parse_fast_stream(stream + b"\x00") is None
    assert protocol.parse_fast_stream(b"") is None
    assert protocol.parse_fast_stream(b"\x80") is None      # a cut varint


def test_blocks_cut_the_stream_at_whole_chunks():
    stream = protocol.fast_stream([b"A" * 700, b"", b"k" * 333])
    for periods in FB.RUNGS:
        most = FB.block_bytes(periods)
        chunk = 0
        rebuilt = b""
        while chunk * FB.CHUNK < len(stream):
            block = protocol.fast_block(FB, stream, chunk, periods)
            assert len(block) <= most
            rebuilt += block
            chunk += (len(block) + FB.CHUNK - 1) // FB.CHUNK
        assert rebuilt == stream, periods


def test_the_deadline_does_not_cap_the_payload():
    # A wall clock on the whole data phase is a length limit, however it is
    # described: counted per block, it bounds the same stalls and scales
    # with the work.
    transfer = held.HeldTransfer(True, (b"A" * 4096,))
    transfer.levels = protocol.Levels()
    transfer.levels.fit(200, 4250, 9200)
    transfer.now = 0
    transfer.start_data(0)
    chunks = len(transfer.fast_data) // FB.CHUNK
    assert chunks > 100, chunks
    transfer.now = protocol.BLOCK_TIMEOUT_MS * 3
    transfer.block_started = transfer.now
    transfer.step(transfer.now + 1000, 0)
    assert transfer.result is None, transfer.result


def test_payloads_of_every_shape_cross_a_pair():
    rng = random.Random(3)

    def seen(level):
        return int(40 + 160 * level + rng.gauss(0, 3))
    for fields in ([b"HomeNet", b"hunter2!!", b"\x03"], [b"OpenCafe", b""],
                   [b""], [], [b"A" * 32, b"k" * 64, b"\x03"]):
        lead = held.HeldTransfer(True, fields)
        follow = held.HeldTransfer(False)
        held.run_pair(lead, follow, seen, limit_ms=30000)
        assert (lead.result, follow.result) == ("OK", "OK"), fields
        assert follow.received == fields, (follow.received, fields)
