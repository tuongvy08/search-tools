import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SearchStockOnlyDomTests(unittest.TestCase):
    def test_toggle_race_abort_selection_modes_and_empty_query(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")
        completed = subprocess.run(
            [node, "tests/search_stock_only_dom_test.js"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_truthful_stock_match_labels_remain_explicit(self):
        source = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
        self.assertIn("Khớp code", source)
        self.assertIn("Cùng CAS — cần đối chiếu", source)
        self.assertIn("Khớp tên tồn", source)


if __name__ == "__main__":
    unittest.main()
