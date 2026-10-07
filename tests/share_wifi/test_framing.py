"""Symbol-level loopback: the real transmitter's output into the real receiver.

This is where the bugs were. The End Block Pattern opens with two data symbols
(`_-`) before its first black, so a receiver that treats every symbol as a bit
eats them as payload and then trips over the black.
"""

from harness import load_module

protocol = load_module("share_wifi", "protocol")
Transfer = protocol.Transfer
Levels = protocol.Levels
build_tokens = protocol.build_tokens
split_blocks = protocol.split_blocks
block_symbols = protocol.block_symbols
block_crc = protocol.block_crc
symbols_bits = protocol.symbols_bits
parse_payload = protocol.parse_payload
BEGIN_BLOCK = protocol.BEGIN_BLOCK
END_BLOCK = protocol.END_BLOCK
SEP = protocol.SEPARATOR


def receiver():
    rx = Transfer(False)
    rx.levels = Levels()
    rx.start_data(0)
    rx.tx = []
    return rx


def feed(rx, symbols):
    for symbol in symbols:
        rx.rx.append(symbol)
        rx.collect_block()


def loopback(ssid, key):
    """Drive a whole credential through transmit and receive, block by block."""
    blocks = split_blocks(build_tokens(ssid, key))
    rx = receiver()
    collected = []
    for block in blocks:
        rx.sub = rx.STEP_BLOCK
        rx.reset_block()
        rx.rx = []
        feed(rx, list(BEGIN_BLOCK) + block_symbols(block) + list(END_BLOCK))

        assert rx.sub == rx.STEP_VERDICT, "block never terminated"
        assert symbols_bits(rx.tx[:16]) == block_crc(block), "receiver CRC mismatch"
        collected.extend(rx.pending_tokens)
        # As a CRC Good would: a Follower holding an unsettled block refuses
        # the next one.
        rx.accept_pending()
        rx.tx = []
    return collected


def test_credentials_survive_the_round_trip():
    for ssid, key in [(b"HomeNet", b"hunter2!!"),
                      (b"A" * 32, b"k" * 63),
                      (b"\xff\xfe raw", b"password1"),
                      (b"x", b"12345678")]:
        assert parse_payload(loopback(ssid, key)) == [ssid, key]


def test_payload_bytes_beginning_01_are_not_mistaken_for_end_block():
    # END_BLOCK starts '_','-' -- the bit pattern 0,1. A byte whose top two bits
    # are 01 must not trigger it.
    tricky = bytes([0b01000000, 0b01111111, 0b01010101, 0b01000001])
    key = b"aaaa" + tricky + b"bb"
    assert parse_payload(loopback(b"net", key))[1] == key


def test_separator_always_lands_in_the_first_block():
    # An SSID is at most 32 bytes and a block holds 64, so the separator can
    # never fall on a block boundary for a legal credential. Worth pinning:
    # the receiver tracks "have I seen the separator" across blocks, and this
    # is why that state is only ever set in the first one.
    for ssid_len in (1, 16, 32):
        blocks = split_blocks(build_tokens(b"A" * ssid_len, b"k" * 63))
        assert SEP in blocks[0]
        assert all(SEP not in block for block in blocks[1:])
        assert sum(1 for b in blocks for t in b if t == SEP) == 1


def test_black_part_way_through_a_byte_is_a_framing_error():
    rx = receiver()
    rx.rx = []
    feed(rx, list(BEGIN_BLOCK) + ["-", "-", "-", " "])   # black after 3 bits
    assert_framing_failure(rx)


def assert_framing_failure(rx):
    # Decoding stops, and the Follower breaks in: it shows high until the
    # Leader, which reads while it transmits, stops -- and only then sends
    # its Data Sync Error. See test_break.py and test_recovery.py.
    assert rx.sub == rx.STEP_BLOCK
    assert not rx.in_block, "it should have stopped decoding the block"
    assert rx.breaking, "it should be breaking in"
    assert not rx.tx, "the Data Sync Error waits for the Leader to stop"
    assert not rx.pending_slow_down, "and it must not demote on its own"


def test_several_separators_in_one_block_are_fine():
    # A payload may have any number of fields, so a second separator is data,
    # not an error. Telling a separator from the End Block Pattern is done by
    # bit offset, which does not care how many have been seen.
    rx = receiver()
    rx.rx = []
    feed(rx, list(BEGIN_BLOCK)
         + block_symbols([65]) + [" "]
         + block_symbols([66]) + [" "]
         + block_symbols([67])
         + list(END_BLOCK))
    assert rx.sub == rx.STEP_VERDICT, "block should have terminated normally"
    assert rx.pending_tokens == [65, SEP, 66, SEP, 67]


def test_oversized_block_is_a_framing_error():
    rx = receiver()
    rx.rx = []
    feed(rx, list(BEGIN_BLOCK))
    feed(rx, block_symbols([0x41] * (protocol.BLOCK_BYTES + 1)))
    assert_framing_failure(rx)


def test_a_mangled_end_block_is_a_framing_error():
    rx = receiver()
    rx.rx = []
    feed(rx, list(BEGIN_BLOCK) + block_symbols([0x41]) + list("_- _- __ "))
    assert_framing_failure(rx)
