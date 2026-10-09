import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "static"


class CloudUiTest(unittest.TestCase):
    def test_original_ui_css_is_embedded_without_changes(self):
        original = (ROOT / "original-fragment.html").read_text()
        product = (ROOT / "index.html").read_text()
        css = original[original.index("<style>"):original.index("</style>") + len("</style>")]
        self.assertIn(css, product)

    def test_demo_state_is_not_in_product_script(self):
        script = (ROOT / "app.js").read_text()
        for term in ("window.openai", "setWidgetState", "日々の余白", "サンプル"):
            self.assertNotIn(term, script)


if __name__ == "__main__":
    unittest.main()
