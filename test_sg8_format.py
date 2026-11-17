import unittest

from sg8_format import Float32, SaveFile, SaveFormatError


SECTION_X = bytes.fromhex(
    "01 1d000000 "
    "01 01000000 58 ff "
    "22 d6ffffff ff "
    "30 3412807f ff "
    "10 ff 11 ff "
    "01 00000000 ff "
    "ff"
)
SECTION_Y = bytes.fromhex("01 0f000000 01 01000000 59 ff 01 02000000 ff00 ff ff")


class SaveFormatTests(unittest.TestCase):
    def test_preserves_tokens_and_section_order_byte_for_byte(self):
        data = SECTION_X + SECTION_Y
        save = SaveFile.parse(data)

        self.assertEqual(list(save.sections), ["X", "Y"])
        tokens = save.sections["X"]
        self.assertEqual(tokens, [b"X", -42, Float32(b"\x34\x12\x80\x7f"), False, True, b""])
        self.assertIs(type(tokens[1]), int)
        self.assertIs(tokens[3], False)
        self.assertIs(tokens[4], True)
        self.assertEqual(save.sections["Y"], [b"Y", b"\xff\x00"])
        self.assertEqual(save.to_bytes(), data)

    def test_serializes_edited_section_values(self):
        save = SaveFile.parse(SECTION_X)
        save.sections["X"][1] = 42
        save.sections["X"][5] = b"longer"

        encoded = save.to_bytes()
        self.assertEqual(encoded[:5], bytes.fromhex("01 23000000"))
        self.assertEqual(SaveFile.parse(encoded).sections["X"][1], 42)
        self.assertEqual(SaveFile.parse(encoded).sections["X"][5], b"longer")

    def test_rejects_every_truncation_of_a_section(self):
        for end in range(len(SECTION_X)):
            with self.subTest(end=end), self.assertRaises(SaveFormatError):
                SaveFile.parse(SECTION_X[:end])

    def test_rejects_bad_tags_delimiters_or_names(self):
        for label, data in {
            "root tag": b"\x22" + SECTION_X[1:],
            "root delimiter": SECTION_X[:-1] + b"\x00",
            "unknown token": SECTION_X[:12] + b"\x99" + SECTION_X[13:],
            "token delimiter": SECTION_X[:11] + b"\x00" + SECTION_X[12:],
            "non-ASCII name": SECTION_X[:10] + b"\xff" + SECTION_X[11:],
            "duplicate name": SECTION_X + SECTION_X,
        }.items():
            with self.subTest(label=label), self.assertRaises(SaveFormatError):
                SaveFile.parse(data)


if __name__ == "__main__":
    unittest.main()
