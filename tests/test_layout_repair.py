import unittest

from scripts.repair_layout import soft_wrap


class LayoutRepairTests(unittest.TestCase):
    def test_breaks_do_not_enter_asset_paths_or_font_dimensions(self):
        asset = r'\includegraphics[height=14.5pt]{D:/cache/' + 'a' * 64 + '/math.png}'
        line = r'{\fontsize{19}{26}\selectfont ' + asset + ' ' + 'A' * 80 + r'\par}'
        result = soft_wrap(line)
        self.assertIn(asset, result)
        self.assertIn(r'\fontsize{19}{26}', result)
        self.assertIn(r'\allowbreak{}', result)
        self.assertEqual(result.replace(r'\allowbreak{}', ''), line)

    def test_form_blanks_and_identifiers_keep_their_literal_characters(self):
        line = 'specify:' + r'\_' * 50 + ' ' + 'X' * 80 + ' 10.1002/(SICI)1234-5678'
        result = soft_wrap(line)
        self.assertEqual(result.replace(r'\allowbreak{}', ''), line)
        self.assertGreater(result.count(r'\allowbreak{}'), 5)
