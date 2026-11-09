import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ReadingCopyLinks(unittest.TestCase):
    def test_chapter_and_resume_links_keep_their_targets_after_bundling(self):
        source=Path(__file__).resolve().parents[1]/'scripts/validate_package.py'
        spec=importlib.util.spec_from_file_location('package_renderer',source)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'package'
            for folder in ('docs','prompts','config','schemas'):(root/folder).mkdir(parents=True)
            (root.parent/'report.md').write_text('Report\n')
            link='[report](../../report.md)\n'
            (root/'docs/chapter.md').write_text(link)
            (root/'prompts/RESUME.md').write_text(link)
            with patch.object(module,'ROOT',root):rendered=module.render_master()
            self.assertEqual(rendered.count('[report](../report.md)'),2)
            self.assertNotIn('[report](../../report.md)',rendered)
