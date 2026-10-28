"""diff_files: workspace-bound unified diff as a native read-shaped tool."""
import tempfile
import unittest
from pathlib import Path

from zeline import tools


class DiffFilesTests(unittest.TestCase):
    def test_reports_differences_with_unified_hunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("one\ntwo\nthree\n")
            (root / "b.txt").write_text("one\nTWO\nthree\n")
            result = tools._diff_files("a.txt", "b.txt", root)
            self.assertIn("--- a.txt", result)
            self.assertIn("+++ b.txt", result)
            self.assertIn("-two", result)
            self.assertIn("+TWO", result)
            self.assertIn("one", result)  # context survives

    def test_identical_files_report_no_differences(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "same.txt").write_text("identical\n")
            (root / "other.txt").write_text("identical\n")
            self.assertEqual(tools._diff_files("same.txt", "other.txt", root), "(no differences)")

    def test_paths_must_stay_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = Path(tmp).parent / "outside.txt"
            outside.write_text("secret\n")
            result = tools._diff_files(str(outside), "inside.txt", root)
            self.assertIn("ERROR diff files:", result)
            self.assertIn("inside the workspace", result)

    def test_missing_file_is_an_error_not_a_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "exists.txt").write_text("x\n")
            self.assertIn("not a file or not found", tools._diff_files("exists.txt", "nope.txt", root))
            self.assertIn("not a file or not found", tools._diff_files("nope.txt", "exists.txt", root))

    def test_context_is_clamped_to_the_documented_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = "".join(f"line {i}\n" for i in range(40))
            (root / "a.txt").write_text(body)
            (root / "b.txt").write_text(body.replace("line 20", "LINE 20"))
            huge = tools._diff_files("a.txt", "b.txt", root, context=999)
            self.assertNotIn("line 1\n", huge.split("-")[0])  # clamp: not full-file context
            zero = tools._diff_files("a.txt", "b.txt", root, context=0)
            self.assertIn("-line 20", zero)
            self.assertNotIn("line 18", zero)

    def test_symlink_escape_is_blocked_like_every_other_path_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside_dir = Path(tmp).parent / "diff-escape-fixture"
            outside_dir.mkdir(exist_ok=True)
            (outside_dir / "target.txt").write_text("outside\n")
            link = root / "link.txt"
            try:
                link.symlink_to(outside_dir / "target.txt")
            except OSError:
                self.skipTest("symlinks unavailable")
            (root / "b.txt").write_text("inside\n")
            result = tools._diff_files("link.txt", "b.txt", root)
            self.assertIn("ERROR diff files:", result)
            self.assertIn("inside the workspace", result)

    def test_binary_content_degrades_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.bin").write_bytes(b"a\x00b\nc\n")
            (root / "b.bin").write_bytes(b"a\x00b\nd\n")
            result = tools._diff_files("a.bin", "b.bin", root)
            self.assertIn("c", result)
            self.assertIn("d", result)


if __name__ == "__main__":
    unittest.main()
