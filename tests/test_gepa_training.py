"""Offline, opt-in GEPA training bridge; never supply held-out cases to search."""
import importlib.util
import multiprocessing
import unittest


@unittest.skipUnless(importlib.util.find_spec('gepa') is not None,
                     'GEPA optimizer is an opt-in dependency')
class GepaTrainingTests(unittest.TestCase):
    def test_training_only_search_is_report_only(self):
        from zeline.gepa_training import search_training
        seen = []
        result = search_training(
            seed_text='bad', training=[{'id': 'train', 'input': {'x': 1}}],
            score_case=lambda text, case: seen.append(case['id']) or (text == 'good'),
            proposals=['good'], max_metric_calls=16, max_proposals=1, timeout=5,
        )
        self.assertEqual(set(seen), {'train'})
        self.assertIn('good', result['candidates'])
        self.assertTrue(result['report_only'])
        self.assertLessEqual(result['metric_calls'], 16)

    def test_host_cap_rejects_optimizer_overshoot_even_if_engine_swallows_exception(self):
        from zeline.gepa_training import search_training
        with self.assertRaisesRegex((RuntimeError, ValueError), 'metric.*budget'):
            search_training(seed_text='bad', training=[{'id': 'train', 'input': 1}],
                            score_case=lambda text, case: text == 'good',
                            proposals=['good'], max_metric_calls=3, max_proposals=1,
                            timeout=5)

    def test_input_validation_before_optimizer_import(self):
        from zeline.gepa_training import search_training
        with self.assertRaises(ValueError):
            search_training(seed_text='bad', training=[], score_case=lambda *_: True,
                            proposals=['good'], max_metric_calls=4, max_proposals=1,
                            timeout=5)

    def test_nonboolean_checker_fails_closed(self):
        from zeline.gepa_training import search_training
        with self.assertRaises(ValueError):
            search_training(seed_text='bad', training=[{'id': 'train', 'input': 1}],
                            score_case=lambda *_: 1, proposals=['good'],
                            max_metric_calls=8, max_proposals=1, timeout=5)

    def test_expired_deadline_rejects_result(self):
        import time
        from zeline.gepa_training import search_training
        with self.assertRaises(TimeoutError):
            search_training(seed_text='bad', training=[{'id': 'train', 'input': 1}],
                            score_case=lambda *_: (time.sleep(0.06), True)[1],
                            proposals=['good'], max_metric_calls=8,
                            max_proposals=1, timeout=0.05)

    def test_training_ids_must_be_unique(self):
        from zeline.gepa_training import search_training
        with self.assertRaises(ValueError):
            search_training(seed_text='bad', training=[{'id': 'same', 'input': 1},
                                                       {'id': 'same', 'input': 2}],
                            score_case=lambda *_: True, proposals=['good'],
                            max_metric_calls=8, max_proposals=1, timeout=5)

    @unittest.skipUnless('fork' in multiprocessing.get_all_start_methods(),
                         'Bounded GEPA workers require Unix fork')
    def test_bounded_worker_returns_report(self):
        from zeline.gepa_training import search_training_bounded
        report = search_training_bounded(
            seed_text='bad', training=[{'id': 'train', 'input': 1}],
            score_case=lambda text, _: text == 'good', proposals=['good'],
            max_metric_calls=16, max_proposals=1, timeout=5,
        )
        self.assertEqual(report['candidates'], ['bad', 'good'])
        self.assertLessEqual(report['metric_calls'], 16)

    @unittest.skipUnless('fork' in multiprocessing.get_all_start_methods(),
                         'Bounded GEPA workers require Unix fork')
    def test_bounded_worker_kills_hanging_checker(self):
        import os
        import tempfile
        import time
        from pathlib import Path
        from zeline.gepa_training import search_training_bounded
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td, 'worker.pid')
            def hangs(*_):
                marker.write_text(str(os.getpid()))
                time.sleep(20)
                return False
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                search_training_bounded(
                    seed_text='bad', training=[{'id': 'train', 'input': 1}],
                    score_case=hangs, proposals=['good'], max_metric_calls=16,
                    max_proposals=1, timeout=1,
                )
            self.assertLess(time.monotonic() - started, 4)
            self.assertTrue(marker.exists(), 'checker must actually begin')
            with self.assertRaises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)

    @unittest.skipUnless('fork' in multiprocessing.get_all_start_methods(),
                         'Bounded GEPA workers require Unix fork')
    def test_bounded_worker_kills_descendant_process_group(self):
        import subprocess
        import sys
        import tempfile
        import time
        from pathlib import Path
        from zeline.gepa_training import search_training_bounded
        with tempfile.TemporaryDirectory() as td:
            ready = Path(td, 'ready')
            late = Path(td, 'late')
            def spawns(*_):
                subprocess.Popen([sys.executable, '-c',
                                  f'import time; from pathlib import Path; '
                                  f'time.sleep(1.5); Path({str(late)!r}).write_text("survived")'])
                ready.write_text('started')
                time.sleep(20)
                return False
            with self.assertRaises(TimeoutError):
                search_training_bounded(
                    seed_text='bad', training=[{'id': 'train', 'input': 1}],
                    score_case=spawns, proposals=['good'], max_metric_calls=16,
                    max_proposals=1, timeout=1,
                )
            self.assertTrue(ready.exists(), 'descendant must actually start')
            time.sleep(1.6)
            self.assertFalse(late.exists(), 'descendant must die with worker group')

    @unittest.skipUnless('fork' in multiprocessing.get_all_start_methods(),
                         'Bounded GEPA workers require Unix fork')
    def test_bounded_worker_cleans_descendants_after_success(self):
        import subprocess
        import sys
        import tempfile
        import time
        from pathlib import Path
        from zeline.gepa_training import search_training_bounded
        with tempfile.TemporaryDirectory() as td:
            late = Path(td, 'late')
            def spawns(*_):
                subprocess.Popen([sys.executable, '-c',
                                  f'import time; from pathlib import Path; '
                                  f'time.sleep(1); Path({str(late)!r}).write_text("leaked")'])
                return True
            report = search_training_bounded(
                seed_text='bad', training=[{'id': 'train', 'input': 1}],
                score_case=spawns, proposals=['good'], max_metric_calls=16,
                max_proposals=1, timeout=5,
            )
            self.assertTrue(report['report_only'])
            time.sleep(1.2)
            self.assertFalse(late.exists(), 'success must not leave callback children')

    @unittest.skipUnless('fork' in multiprocessing.get_all_start_methods(),
                         'Bounded GEPA workers require Unix fork')
    def test_bounded_worker_rejects_oversized_report(self):
        from zeline.gepa_training import search_training_bounded
        with self.assertRaisesRegex(ValueError, 'output.*budget'):
            search_training_bounded(
                seed_text='bad', training=[{'id': 'train', 'input': 1}],
                score_case=lambda text, _: text == 'good', proposals=['good'],
                max_metric_calls=16, max_proposals=1, timeout=5,
                max_output_bytes=32,
            )


if __name__ == '__main__':
    unittest.main()
