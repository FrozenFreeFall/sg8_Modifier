from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sg8_files import SaveSnapshot


class SaveSnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / 'SG8_2.bytes'
        self.source.write_bytes(b'original')
        self.metadata = self.root / 'SG8_2t.bytes'
        self.metadata.write_bytes(b'slot metadata')

    def test_export_keeps_slot_names_and_original(self):
        snapshot = SaveSnapshot.open(self.source)
        destination = snapshot.export(self.root, b'edited')
        self.assertEqual((destination / self.source.name).read_bytes(), b'edited')
        self.assertEqual((destination / self.metadata.name).read_bytes(), b'slot metadata')
        self.assertEqual(self.source.read_bytes(), b'original')

    def test_save_captures_exact_original_in_backup(self):
        snapshot = SaveSnapshot.open(self.source)
        backup = snapshot.replace(b'edited')
        self.assertEqual(self.source.read_bytes(), b'edited')
        self.assertEqual(backup.read_bytes(), b'original')
        self.assertEqual(self.metadata.read_bytes(), b'slot metadata')

    def test_save_rejects_changed_source_without_replacing(self):
        snapshot = SaveSnapshot.open(self.source)
        self.source.write_bytes(b'changed by game')
        with self.assertRaises(ValueError):
            snapshot.replace(b'edited')
        self.assertEqual(self.source.read_bytes(), b'changed by game')
        self.assertEqual(list(self.root.glob('*.bak')), [])


if __name__ == '__main__':
    unittest.main()
