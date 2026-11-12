"""Guard generated-plan drift and relative links in disclosed source documents."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("p3_package", ROOT / "scripts/validate_package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageRenderTests(unittest.TestCase):
    def test_master_matches_maintained_sources(self):
        self.assertEqual((ROOT / package.MASTER).read_text(), package.render_master())

    def test_prompt_and_chapter_links_are_relocated(self):
        body = "[contract](../docs/00.md#gate) [web](https://example.org/a) [here](#local)"
        rendered = package.relocate_links(body, ROOT / "prompts/RESUME.md", ROOT)
        self.assertEqual(rendered, "[contract](docs/00.md#gate) [web](https://example.org/a) [here](#local)")

    def test_render_uses_source_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("docs", "config", "prompts"):
                (root / name).mkdir()
            (root / "docs/00.md").write_text("first")
            for name in package.CONFIGS:
                (root / "config" / name).write_text("{}")
            for name in ("EXECUTOR.md", "REVIEWER.md", "RESUME.md"):
                (root / "prompts" / name).write_text("prompt")
            before = package.render_master(root)
            (root / "docs/00.md").write_text("changed")
            self.assertNotEqual(before, package.render_master(root))
            self.assertIn("changed", package.render_master(root))
