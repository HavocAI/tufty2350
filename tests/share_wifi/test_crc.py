"""CRC-16/CCITT-FALSE. Getting the variant wrong is silent and total."""

from harness import load_module

protocol = load_module("share_wifi", "protocol")
crc16 = protocol.crc16


def test_standard_check_value():
    # The published check value for CCITT-FALSE. This is what distinguishes it
    # from the half dozen other things called "CRC-16".
    assert crc16(b"123456789") == 0x29B1


def test_empty_input_is_the_initial_value():
    assert crc16(b"") == 0xFFFF


def test_detects_single_bit_flips():
    base = crc16(b"HomeNetwork")
    for i in range(len(b"HomeNetwork")):
        for bit in range(8):
            corrupted = bytearray(b"HomeNetwork")
            corrupted[i] ^= 1 << bit
            assert crc16(bytes(corrupted)) != base


def test_detects_transposition():
    assert crc16(b"ab") != crc16(b"ba")


def test_stays_16_bit():
    for payload in (b"", b"\x00", b"\xff" * 64, bytes(range(256))):
        assert 0 <= crc16(payload) <= 0xFFFF
