"""Behavior tests: real CLI, literal expectations, no model/network dependencies."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'p3_evidence.py'


class EvidenceTests(unittest.TestCase):
    def run_cli(self, command, payload, expected=0, root=None, raw=False):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'input.json'
            file.write_text(payload if raw else json.dumps(payload), encoding='utf-8')
            args = [sys.executable, '-B', str(SCRIPT), command, str(file)]
            if root is not None:
                args += ['--root', str(root)]
            proc = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(proc.returncode, expected, proc.stderr + proc.stdout)
        self.assertTrue(proc.stdout.strip(), 'CLI did not emit the required JSON result: '+proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIs(result['release_eligible'], False)
        self.assertEqual(result['scope'], 'arithmetic_or_structure_only')
        return result

    def timing(self, delays):
        return dict(schema_version=1, data_kind='synthetic', units=[dict(
            unit_id=str(i), source_start_ms=0, source_end_ms=1000,
            stable_correct_ms=None if d is None else 1000+d)
            for i, d in enumerate(delays)])

    def record(self, **changes):
        row = dict(scope_id='a', channel='zh', sequence_id=1,
                   accepted_samples=100, consumed_samples=80,
                   source_end_sample=50, text='中文')
        row.update(changes)
        return row

    def prefix(self, records):
        return dict(schema_version=1, data_kind='synthetic', records=records)

    def lock(self, **changes):
        entry = dict(id='model', required=True, source_url='https://example.org/model',
                     revision='a'*40, files=[dict(path='model.bin', sha256=hashlib.sha256(b'abc').hexdigest(), size_bytes=3)])
        entry.update(changes)
        return dict(schema_version=1, project_revision='b'*40, entries=[entry])

    # A wrong inclusive boundary, percentile rank or denominator breaks these.
    def test_timing_inclusive_5s_and_15s(self):
        r = self.run_cli('timing', self.timing([5000, 15000, 15001]))
        self.assertEqual(r['coverage']['5000_ms']['count'], 1)
        self.assertEqual(r['coverage']['15000_ms']['count'], 2)
        self.assertEqual(r['late_count'], 1)

    def test_timing_negative_latency_retained(self):
        r = self.run_cli('timing', self.timing([-500, 0]))
        self.assertEqual(r['units'][0]['latency_ms'], -500)
        self.assertEqual(r['coverage']['2000_ms']['count'], 2)

    def test_timing_nearest_rank_small_sample(self):
        r = self.run_cli('timing', self.timing([100, 200, 300]))
        self.assertEqual(r['matched_within_15000_ms']['p50_ms'], 200)
        self.assertEqual(r['matched_within_15000_ms']['p95_ms'], 300)

    def test_timing_missing_in_denominator(self):
        r = self.run_cli('timing', self.timing([100, None]))
        self.assertEqual(r['coverage']['2000_ms'], {'count': 1, 'fraction': .5})
        self.assertEqual(r['missing_count'], 1)
        self.assertEqual(r['overall_p95'], {'latency_ms': None, 'unbounded': True})

    def test_timing_late_makes_small_sample_overall_unbounded(self):
        r = self.run_cli('timing', self.timing([15001]))
        self.assertTrue(r['overall_p95']['unbounded'])
        self.assertIsNone(r['matched_within_15000_ms']['p95_ms'])

    def test_timing_finite_overall_p95_with_one_missing_of_twenty(self):
        r = self.run_cli('timing', self.timing([100]*19+[None]))
        self.assertEqual(r['overall_p95'], {'latency_ms': 100, 'unbounded': False})

    def test_timing_empty_rejected(self):
        self.run_cli('timing', self.timing([]), 1)

    def test_timing_duplicate_unit_rejected(self):
        p = self.timing([0, 0]); p['units'][1]['unit_id'] = '0'
        self.run_cli('timing', p, 1)

    def test_timing_boolean_time_rejected(self):
        p = self.timing([0]); p['units'][0]['source_start_ms'] = False
        self.run_cli('timing', p, 1)

    def test_timing_nonfinite_rejected(self):
        for number in ['NaN', 'Infinity', '-Infinity', '1e999']:
            with self.subTest(number=number):
                p = json.dumps(self.timing([0])).replace('"source_start_ms": 0', '"source_start_ms": '+number)
                self.run_cli('timing', p, 1, raw=True)

    def test_timing_negative_timestamp_rejected(self):
        p = self.timing([0]); p['units'][0]['source_start_ms'] = -1
        self.run_cli('timing', p, 1)

    def test_timing_reversed_interval_rejected(self):
        p = self.timing([0]); p['units'][0]['source_start_ms'] = 1001
        self.run_cli('timing', p, 1)

    def test_schema_boolean_version_rejected(self):
        p = self.timing([0]); p['schema_version'] = True
        self.run_cli('timing', p, 1)

    def test_unknown_fields_rejected(self):
        p = self.timing([0]); p['release_eligible'] = True
        self.run_cli('timing', p, 1)

    def test_duplicate_json_key_rejected(self):
        self.run_cli('timing', '{"schema_version":1,"schema_version":1}', 1, raw=True)

    # Removing prefix, scope or watermark checks breaks these independently.
    def test_prefix_append_and_same_text_with_watermark_progress(self):
        r = self.run_cli('prefix-audit', self.prefix([self.record(), self.record(
            sequence_id=2, accepted_samples=120, consumed_samples=100), self.record(
            sequence_id=3, accepted_samples=120, consumed_samples=100, text='中文追加')]))
        self.assertEqual(r['record_count'], 3)
        self.assertTrue(r['prefix_invariant_holds'])

    def test_prefix_chinese_rewrite_rejected(self):
        r = self.run_cli('prefix-audit', self.prefix([self.record(), self.record(sequence_id=2, text='中午')]), 1)
        self.assertIn('prefix', r['errors'][0])

    def test_prefix_future_source_sample_rejected(self):
        r = self.run_cli('prefix-audit', self.prefix([self.record(source_end_sample=81)]), 1)
        self.assertIn('source_end_sample', r['errors'][0])

    def test_prefix_consumed_above_accepted_rejected(self):
        self.run_cli('prefix-audit', self.prefix([self.record(consumed_samples=101)]), 1)

    def test_prefix_same_sequence_rejected(self):
        self.run_cli('prefix-audit', self.prefix([self.record(), self.record()]), 1)

    def test_prefix_source_end_regression_rejected(self):
        self.run_cli('prefix-audit', self.prefix([self.record(), self.record(
            sequence_id=2, source_end_sample=49, text='中文追加')]), 1)

    def test_prefix_source_end_independent_between_channels(self):
        self.run_cli('prefix-audit', self.prefix([self.record(), self.record(
            channel='ja', source_end_sample=20, text='日本語')]))

    def test_prefix_cross_scope_independent(self):
        r = self.run_cli('prefix-audit', self.prefix([self.record(), self.record(
            scope_id='b', text='別', accepted_samples=2, consumed_samples=1, source_end_sample=0)]))
        self.assertEqual(r['scope_count'], 2)

    def test_prefix_cross_channel_watermarks_shared(self):
        self.run_cli('prefix-audit', self.prefix([self.record(), self.record(
            channel='ja', accepted_samples=99, text='日本語')]), 1)

    def test_prefix_cross_channel_text_independent(self):
        self.run_cli('prefix-audit', self.prefix([self.record(), self.record(channel='ja', text='日本語')]))

    def test_prefix_boolean_integer_rejected(self):
        self.run_cli('prefix-audit', self.prefix([self.record(sequence_id=True)]), 1)

    def test_prefix_unicode_normalization_not_applied(self):
        self.run_cli('prefix-audit', self.prefix([self.record(text='é'), self.record(sequence_id=2, text='e\u0301')]), 1)

    # Removing integrity, path or requiredness checks breaks these.
    def test_lock_structure_only_does_not_verify_bytes(self):
        r = self.run_cli('check-lock', self.lock())
        self.assertTrue(r['ready'])
        self.assertFalse(r['bytes_verified'])

    def test_lock_null_revision_required_unresolved(self):
        r = self.run_cli('check-lock', self.lock(revision=None), 2)
        self.assertFalse(r['ready'])
        self.assertTrue(r['unresolved'])

    def test_lock_null_hash_size_required_unresolved(self):
        self.run_cli('check-lock', self.lock(files=[dict(path='x', sha256=None, size_bytes=None)]), 2)

    def test_lock_empty_required_files_unresolved(self):
        self.run_cli('check-lock', self.lock(files=[]), 2)

    def test_lock_optional_pending_does_not_block(self):
        r = self.run_cli('check-lock', self.lock(required=False, revision=None, files=[]))
        self.assertTrue(r['ready'])
        self.assertTrue(r['optional_pending'])

    def test_lock_real_bytes_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'model.bin').write_bytes(b'abc')
            r = self.run_cli('check-lock', self.lock(), root=temp)
        self.assertTrue(r['bytes_verified'])

    def test_lock_wrong_sha_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'model.bin').write_bytes(b'abd')
            r = self.run_cli('check-lock', self.lock(), 2, root=temp)
        self.assertFalse(r['bytes_verified'])
        self.assertIn('sha256', str(r['mismatches']))

    def test_lock_wrong_size_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'model.bin').write_bytes(b'abcd')
            self.run_cli('check-lock', self.lock(), 2, root=temp)

    def test_lock_missing_file_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            self.run_cli('check-lock', self.lock(), 2, root=temp)

    def test_lock_path_escape_rejected_without_root(self):
        for path in ['../x', '/tmp/x', 'a/../../x', 'C:\\escape', '']:
            with self.subTest(path=path):
                self.run_cli('check-lock', self.lock(files=[dict(path=path, sha256='a'*64, size_bytes=0)]), 1)

    def test_lock_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            (Path(outside)/'x').write_bytes(b'abc')
            (Path(root)/'model.bin').symlink_to(Path(outside)/'x')
            self.run_cli('check-lock', self.lock(), 2, root=root)

    def test_lock_directory_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'model.bin').mkdir()
            self.run_cli('check-lock', self.lock(), 2, root=root)

    def test_lock_duplicate_entry_id_rejected(self):
        p = self.lock(); p['entries'] *= 2
        self.run_cli('check-lock', p, 1)

    def test_lock_boolean_size_rejected(self):
        p = self.lock(); p['entries'][0]['files'][0]['size_bytes'] = True
        self.run_cli('check-lock', p, 1)

    def test_lock_non_https_url_rejected(self):
        self.run_cli('check-lock', self.lock(source_url='http://example.org/x'), 1)

    def test_lock_invalid_revision_rejected(self):
        self.run_cli('check-lock', self.lock(revision='latest'), 1)

    def test_missing_input_emits_safe_error(self):
        proc = subprocess.run([sys.executable, '-B', str(SCRIPT), 'timing', '/not-existing-p3-input'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(json.loads(proc.stdout)['release_eligible'])


if __name__ == '__main__':
    unittest.main()
