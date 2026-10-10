"""Integration contracts at the UI boundary (standard-library test runner too)."""
import json
import unittest
from dataclasses import asdict
from unittest.mock import patch

from src.engine import Dataset, load_rules, run
from src.ui_service import ROOT, analyze, export_json, fingerprint, sample_files


class UIServiceTests(unittest.TestCase):
    def test_same_results_as_cli_engine(self):
        files = sample_files()
        ds, rules, results, blocked = analyze(files)
        expected_rules = load_rules(ROOT)
        expected_ds = Dataset.load(ROOT, unilateral_acts=expected_rules['art391-4-gratuitous']['action_filter']['unilateral_acts'])
        self.assertFalse(blocked)
        self.assertEqual([asdict(r) for r in results], [asdict(r) for r in run(expected_ds, expected_rules)])
        payload = json.loads(export_json(ds, rules, results, fingerprint(files)))
        self.assertEqual(len(payload['results']), 34)
        self.assertEqual(sum(r['candidate'] for r in payload['results']), 20)
        self.assertIn('rule_status', payload)

    def test_critical_input_never_runs_engine(self):
        files = sample_files()
        files['transactions.csv'] = files['transactions.csv'].replace(b'480000000', b'not-a-number', 1)
        with patch('src.ui_service.run') as engine_run:
            _, _, results, blocked = analyze(files)
        self.assertTrue(blocked)
        self.assertEqual(results, [])
        engine_run.assert_not_called()

    def test_missing_file_and_broken_rows(self):
        files = sample_files()
        del files['cases.csv']
        with self.assertRaises(ValueError):
            analyze(files)
        files = sample_files()
        files['cases.csv'] += b'bad,row\n'
        with self.assertRaises(ValueError):
            analyze(files)

    def test_bom_and_optional_links(self):
        files = sample_files()
        del files['transaction_links.csv']
        files['cases.csv'] = b'\xef\xbb\xbf' + files['cases.csv']
        self.assertFalse(analyze(files)[3])

    def test_cp949_and_crlf_upload_match_utf8_results(self):
        """한국어 Windows Excel 저장본(CP949·CRLF)도 UTF-8 원본과 같은 결과를 낸다."""
        # 샘플은 CP949로 표현되는 문자만 쓴다. '—'(U+2014)처럼 CP949에 없는 문자는 Excel 저장 시
        # '?'로 바뀌어 사용자에게 깨진 자료처럼 보이므로, 아래 엄격한 encode가 실패하면 샘플을 고친다.
        texts = {name: content.decode('utf-8') for name, content in sample_files().items()}
        utf8 = {name: text.encode('utf-8') for name, text in texts.items()}
        cp949 = {name: text.encode('cp949').replace(b'\n', b'\r\n') for name, text in texts.items()}
        self.assertNotEqual(cp949['parties.csv'], utf8['parties.csv'].replace(b'\n', b'\r\n'))
        self.assertEqual([asdict(r) for r in analyze(cp949)[2]],
                         [asdict(r) for r in analyze(utf8)[2]])

    def test_undecodable_upload_is_rejected_with_guidance(self):
        files = sample_files()
        files['cases.csv'] = b'\xff\xfe\x00\x81' + files['cases.csv']
        with self.assertRaisesRegex(ValueError, 'CSV UTF-8'):
            analyze(files)


if __name__ == '__main__':
    unittest.main()
