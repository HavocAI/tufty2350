"""Payload framing: byte fields joined by a black separator.

The protocol carries fields and attaches no meaning to them, so what is
tested here is structure. Whether two particular fields are a usable WiFi
credential is the app's question, and lives in test_app.py.
"""

from harness import load_module

protocol = load_module("share_wifi", "protocol")
build_tokens = protocol.build_tokens
split_blocks = protocol.split_blocks
block_symbols = protocol.block_symbols
block_crc = protocol.block_crc
parse_payload = protocol.parse_payload
crc16 = protocol.crc16
SEP = protocol.SEPARATOR
BLOCK_BYTES = protocol.BLOCK_BYTES

VALID = [
    (b"HomeNet", b"hunter2!!"),
    (b"A" * 32, b"k" * 63),            # longest legal SSID and passphrase
    (b"A" * 32, b"k" * 64),            # 64 byte hex PSK
    (b"x", b"12345678"),               # shortest legal of each
    (b"\xff\xfe raw", b"password1"),   # SSIDs are octets, not text
]


def test_round_trip():
    for ssid, key in VALID:
        assert parse_payload(build_tokens(ssid, key)) == [ssid, key]


def test_blocks_respect_the_byte_limit():
    for ssid, key in VALID:
        blocks = split_blocks(build_tokens(ssid, key))
        counts = [sum(1 for t in b if t != SEP) for b in blocks]
        assert all(n <= BLOCK_BYTES for n in counts), counts
        assert sum(counts) == len(ssid) + len(key)


def test_blocks_reassemble():
    for ssid, key in VALID:
        blocks = split_blocks(build_tokens(ssid, key))
        rejoined = [token for block in blocks for token in block]
        assert parse_payload(rejoined) == [ssid, key]


def test_separator_does_not_count_toward_the_block_limit():
    # 64 SSID bytes would fill a block exactly; the separator must not push a
    # byte out of it.
    blocks = split_blocks(build_tokens(b"A" * 64, b"k" * 8))
    counts = [sum(1 for t in b if t != SEP) for b in blocks]
    assert counts == [64, 8]


def test_payload_symbols_never_contain_black():
    # This is the property that makes every control pattern unforgeable.
    for ssid, key in VALID:
        blocks = split_blocks(build_tokens(ssid, key))
        symbols = [s for b in blocks for s in block_symbols(b)]
        assert set(symbols) <= set("-_ ")
        assert symbols.count(" ") == 1, "exactly one black, the separator"


def test_separator_contributes_a_zero_byte_to_the_crc():
    # Without this a dropped separator passes CRC and only surfaces later as a
    # decode failure, reporting the wrong error and burning the retry budget.
    assert block_crc([1, 2, SEP, 3]) == crc16(bytes([1, 2, 0, 3]))
    assert block_crc([1, 2, SEP, 3]) != block_crc([1, 2, 3])


def test_any_number_of_fields_round_trips():
    # The protocol is not limited to two. Nothing in the framing counts them.
    for fields in ([b"solo"],
                   [b"a", b"b"],
                   [b"a", b"b", b"c", b"d"],
                   [b"x" * 100, b"y"]):
        assert parse_payload(build_tokens(*fields)) == fields


def test_empty_fields_survive():
    # Two adjacent separators mean an empty field, which is structurally fine.
    assert parse_payload(build_tokens(b"a", b"", b"c")) == [b"a", b"", b"c"]


def test_a_stream_with_no_separator_is_one_field():
    assert parse_payload(list(b"justthis")) == [b"justthis"]


def test_separator_count_matches_field_count():
    for n in range(1, 6):
        fields = [bytes([65 + i]) for i in range(n)]
        tokens = build_tokens(*fields)
        assert sum(1 for t in tokens if t == SEP) == n - 1


# --- payloads of any length --------------------------------------------------
# The protocol attaches no meaning to the payload: it does not know how many
# fields there should be, how long they may be, or whether they are text. Length
# used to be an exception to that, and not deliberately -- BLOCK_TIMEOUT_MS was
# counted over the whole data phase, which is about 750 bytes at the 50ms rung.
# A 512-byte payload died with 449 of its 512 tokens already delivered.


def test_a_payload_splits_into_as_many_blocks_as_it_needs():
    for size, want in ((1, 1), (64, 1), (65, 2), (640, 10), (4096, 64)):
        blocks = protocol.split_blocks(protocol.build_tokens(b"A" * size))
        assert len(blocks) == want, \
            "%d bytes made %d blocks, expected %d" % (size, len(blocks), want)
        rebuilt = [t for b in blocks for t in b]
        assert rebuilt == protocol.build_tokens(b"A" * size), \
            "%d bytes did not survive the round trip through split_blocks" % size


def test_every_block_but_the_last_is_full():
    blocks = protocol.split_blocks(protocol.build_tokens(b"A" * 1000))
    for i, block in enumerate(blocks[:-1]):
        payload_bytes = sum(1 for t in block if t != protocol.SEPARATOR)
        assert payload_bytes == protocol.BLOCK_BYTES, \
            "block %d carries %d bytes, not a full %d" % (i, payload_bytes,
                                                          protocol.BLOCK_BYTES)


def test_the_deadline_does_not_cap_the_payload():
    # The bug this replaced: a wall clock on the whole data phase is a length
    # limit, however it is described. Counted per block it bounds the same
    # stalls and scales with the work.
    transfer = protocol.Transfer(True, (b"A" * 4096,), frame_ms=lambda: 11)
    transfer.levels = protocol.Levels()
    transfer.levels.fit(200, 4250, 9200)
    transfer.now = 0
    transfer.start_data(0)
    blocks = len(transfer.blocks)
    assert blocks == 64, blocks
    # a block accepted this late would have been past a whole-transfer deadline
    transfer.now = protocol.BLOCK_TIMEOUT_MS * 3
    transfer.block_started = transfer.now
    assert transfer.step(transfer.now + 1000, 0) is not None, \
        "a long transfer must not be cut off by a deadline meant for one block"
    assert transfer.result is None, transfer.result


def test_a_long_payload_survives_the_whole_round_trip():
    # Structure only, through the real splitter and parser: the protocol has no
    # opinion about what the fields contain.
    fields = (b"A" * 700, b"", b"k" * 333, bytes(range(1, 256)))
    tokens = protocol.build_tokens(*fields)
    blocks = protocol.split_blocks(tokens)
    assert len(blocks) > 10, "should need many blocks"
    rebuilt = [t for b in blocks for t in b]
    assert protocol.parse_payload(rebuilt) == list(fields)




