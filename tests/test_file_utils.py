"""archive_file / checksum_file / find_duplicate_files: workspace utilities."""
import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from zeline import file_utils


class ArchiveFileTests(unittest.TestCase):
    def test_create_and_extract_zip_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.txt").write_text("alpha\n")
            (root / "src" / "b.txt").write_text("beta\n")
            created = file_utils.archive_file("create", "src", "bundle.zip", root)
            self.assertIn("Created bundle.zip", created)
            self.assertTrue((root / "bundle.zip").is_file())
            extracted = file_utils.archive_file("extract", "bundle.zip", "out", root)
            self.assertIn("2 file(s)", extracted)
            self.assertEqual((root / "out" / "a.txt").read_text(), "alpha\n")

    def test_tar_gz_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "solo.txt").write_text("solo\n")
            self.assertIn("Created", file_utils.archive_file("create", "solo.txt", "solo.tar.gz", root))
            self.assertIn("file(s)", file_utils.archive_file("extract", "solo.tar.gz", "untar", root))
            self.assertEqual((root / "untar" / "solo.txt").read_text(), "solo\n")

    def test_zip_slip_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as archive:
                archive.writestr("../escape.txt", "pwned\n")
            (root / "evil.zip").write_bytes(payload.getvalue())
            result = file_utils.archive_file("extract", "evil.zip", "out", root)
            self.assertIn("ERROR archive:", result)
            self.assertFalse((root.parent / "escape.txt").exists())

    def test_tar_path_traversal_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                data = b"pwned\n"
                info = tarfile.TarInfo(name="../escape.txt")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            (root / "evil.tar.gz").write_bytes(buffer.getvalue())
            result = file_utils.archive_file("extract", "evil.tar.gz", "out", root)
            self.assertIn("ERROR archive:", result)
            self.assertFalse((root.parent / "escape.txt").exists())

    def test_unknown_extension_and_bad_action_are_clear_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIn("ERROR archive:", file_utils.archive_file("rebalance", "a", "b", root))
            (root / "file.txt").write_text("x\n")
            self.assertIn("ERROR archive:", file_utils.archive_file("extract", "file.txt", "out", root))

    def test_paths_must_stay_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = Path(tmp).parent / "outside.txt"
            outside.write_text("x\n")
            self.assertIn("ERROR archive:", file_utils.archive_file("create", str(outside), "a.zip", root))


class ChecksumFileTests(unittest.TestCase):
    def test_sha256_digest_and_verify_ok(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "f.txt").write_bytes(b"stable bytes\n")
            expected = hashlib.sha256(b"stable bytes\n").hexdigest()
            result = file_utils.checksum_file("f.txt", root)
            self.assertIn(expected, result)
            self.assertIn("OK sha256", file_utils.checksum_file("f.txt", root, verify=expected))

    def test_verify_mismatch_states_both_digests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "f.txt").write_bytes(b"actual\n")
            result = file_utils.checksum_file("f.txt", root, verify="0" * 64)
            self.assertIn("MISMATCH", result)
            self.assertIn("0" * 64, result)

    def test_unknown_algorithm_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "f.txt").write_text("x\n")
            self.assertIn("ERROR checksum:", file_utils.checksum_file("f.txt", root, algorithm="crc1337"))
            self.assertIn("not a file", file_utils.checksum_file("nope.txt", root))


class FindDuplicateFilesTests(unittest.TestCase):
    def test_groups_identical_content_across_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.csv").write_text("a,b\n1,2\n")
            (root / "two.csv").write_text("a,b\n1,2\n")
            (root / "three.csv").write_text("a,b\n1,2\n")
            (root / "unique.csv").write_text("different\n")
            result = file_utils.find_duplicate_files(root)
            self.assertIn("one.csv", result)
            self.assertIn("two.csv", result)
            self.assertIn("three.csv", result)
            self.assertNotIn("unique.csv", result)
            self.assertIn("duplicate group(s)", result)

    def test_no_duplicates_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").write_text("1\n")
            (root / "b").write_text("2\n")
            self.assertIn("No duplicate files", file_utils.find_duplicate_files(root))

    def test_pattern_narrows_the_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            same = "same bytes\n"
            (root / "keep.txt").write_text(same)
            (root / "keep2.txt").write_text(same)
            (root / "skip.log").write_text(same)
            result = file_utils.find_duplicate_files(root, pattern="*.txt")
            self.assertIn("keep.txt", result)
            self.assertNotIn("skip.log", result)


if __name__ == "__main__":
    unittest.main()
