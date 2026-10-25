"""Real offline task execution; no provider or candidate-supplied verdicts."""
import hashlib
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


class OfflineEvaluationTests(unittest.TestCase):
    @staticmethod
    def purge_zeline_modules():
        for key in tuple(sys.modules):
            if key == 'zeline' or key.startswith('zeline.'):
                sys.modules.pop(key, None)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.dict(os.environ, {'ZELINE_HOME': str(self.root / 'state')})
        patch.start()
        self.addCleanup(patch.stop)
        self.purge_zeline_modules()
        self.addCleanup(self.purge_zeline_modules)
        self.forge = importlib.import_module('zeline.skillforge')
        self.store = self.forge.ForgeStore()
        skills = importlib.import_module('zeline.skills')
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        self.active = skills.PRIVATE_SKILLS_DIR / 'arithmetic' / 'SKILL.md'
        self.active.parent.mkdir(parents=True, exist_ok=True)
        self.active.write_text('---\nname: arithmetic\ndescription: Arithmetic task policy\n---\n# Arithmetic\n```json\n{"operation":"subtract"}\n```\n')
        self.candidate = '---\nname: arithmetic\ndescription: Arithmetic task policy\n---\n# Arithmetic\n```json\n{"operation":"add"}\n```\n'
        self.proposal = self.store.propose('arithmetic', self.candidate, 'Correct arithmetic task operation')
        self.adapter = self.root / 'trusted.py'
        self.adapter.write_text('import json\ndef run(candidate, task_input):\n'
                                '    policy = json.loads(candidate.split("```json\\n")[1].split("```")[0])\n'
                                '    a, b = task_input["operands"]\n'
                                '    return a + b if policy["operation"] == "add" else a - b\n')
        self.manifest = {'training': [{'id': 'train', 'input': {'operands': [2, 3]}, 'expected': 5}],
                         'held_out': [{'id': 'hidden', 'input': {'operands': [7, 4]}, 'expected': 11}]}

    def api(self):
        try:
            return importlib.import_module('zeline.offline_evaluation')
        except ModuleNotFoundError:
            self.fail('Bounded offline evaluation API is missing')

    def freeze(self):
        return self.api().freeze_evaluation(self.store, self.proposal['id'], self.manifest,
            adapter_path=self.adapter, adapter_sha256=hashlib.sha256(self.adapter.read_bytes()).hexdigest())

    def test_executed_task_results_feed_explicit_paired_comparison(self):
        api = self.api()
        frozen = self.freeze()
        report = api.evaluate(self.store, frozen)
        self.assertEqual(report['scope'], 'evaluation_readiness')
        self.assertEqual(report['calls'], 4)
        self.assertTrue(report['checks']['behavioral_passed'])
        self.assertEqual(report['checks']['comparison']['baseline'], {'train': False, 'hidden': False})
        self.assertEqual(report['checks']['comparison']['candidate'], {'train': True, 'hidden': True})
        self.assertEqual(report['checks']['evaluation_split']['held_out_case_ids'], ['hidden'])
        self.assertEqual(self.store.get(self.proposal['id'])['status'], 'tested')
        self.assertIn('subtract', self.active.read_text())
        self.assertEqual(len(report['manifest_sha256']), 64)
        self.assertEqual(len(report['checker_sha256']), 64)


    def write_adapter(self, body):
        self.adapter.write_text(body)
        return hashlib.sha256(self.adapter.read_bytes()).hexdigest()

    def test_freeze_rejects_input_overlap_and_wrong_adapter_digest(self):
        duplicate = {'training': [{'id': 'train', 'input': {'same': 1}, 'expected': 1}],
                     'held_out': [{'id': 'hidden', 'input': {'same': 1}, 'expected': 1}]}
        with self.assertRaisesRegex(ValueError, 'inputs must be disjoint'):
            self.api().freeze_evaluation(self.store, self.proposal['id'], duplicate,
                adapter_path=self.adapter, adapter_sha256=hashlib.sha256(self.adapter.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            self.api().freeze_evaluation(self.store, self.proposal['id'], self.manifest,
                adapter_path=self.adapter, adapter_sha256='0' * 64)

    def test_frozen_manifest_and_adapter_movement_fail_closed(self):
        frozen = self.freeze()
        frozen['manifest']['held_out'][0]['expected'] = 12
        with self.assertRaisesRegex(ValueError, 'manifest digest mismatch'):
            self.api().evaluate(self.store, frozen)
        frozen = self.freeze()
        self.adapter.write_text(self.adapter.read_text() + '\n# moved\n')
        with self.assertRaisesRegex(ValueError, 'adapter changed'):
            self.api().evaluate(self.store, frozen)
        self.assertEqual(self.store.get(self.proposal['id'])['status'], 'pending')

    def test_active_source_movement_is_rejected(self):
        frozen = self.freeze()
        self.active.write_text(self.active.read_text() + '\nChanged after freeze.\n')
        with self.assertRaisesRegex(ValueError, 'Active skill or companions changed'):
            self.api().evaluate(self.store, frozen)

    def test_timeout_and_malformed_result_never_produce_passing_evidence(self):
        digest = self.write_adapter('import time\ndef run(candidate, task_input):\n    time.sleep(1)\n    return 1\n')
        frozen = self.api().freeze_evaluation(self.store, self.proposal['id'], self.manifest,
            adapter_path=self.adapter, adapter_sha256=digest, task_timeout=0.05, total_timeout=1)
        report = self.api().evaluate(self.store, frozen)
        self.assertFalse(report['checks']['behavioral_passed'])
        self.assertTrue(report['checks']['comparison']['errors'])
        self.assertEqual(report['calls'], 0)

        # A non-JSON task result makes the trusted worker fail; it is not accepted as a verdict.
        self.store = self.forge.ForgeStore()
        digest = self.write_adapter('def run(candidate, task_input):\n    return {1, 2}\n')
        frozen = self.api().freeze_evaluation(self.store, self.proposal['id'], self.manifest,
            adapter_path=self.adapter, adapter_sha256=digest)
        report = self.api().evaluate(self.store, frozen)
        self.assertFalse(report['checks']['behavioral_passed'])
        self.assertTrue(report['checks']['comparison']['errors'])

    def test_adapter_receives_only_candidate_and_task_input_not_hidden_labels(self):
        digest = self.write_adapter(
            'import json\ndef run(candidate, task_input):\n'
            '    assert "expected" not in task_input and "held_out" not in task_input\n'
            '    policy=json.loads(candidate.split("```json\\n")[1].split("```")[0])\n'
            '    a,b=task_input["operands"]\n'
            '    return a+b if policy["operation"]=="add" else a-b\n')
        frozen = self.api().freeze_evaluation(self.store, self.proposal['id'], self.manifest,
            adapter_path=self.adapter, adapter_sha256=digest)
        report = self.api().evaluate(self.store, frozen)
        self.assertTrue(report['checks']['behavioral_passed'])
        self.assertFalse(report['generalization_proven'])
        self.assertFalse(report['optimizer_executed'])


    def test_oversized_raw_worker_output_fails_closed(self):
        digest = self.write_adapter(
            'import os\ndef run(candidate, task_input):\n'
            '    os.write(1, b"x" * 70000)\n'
            '    return 0\n')
        frozen = self.api().freeze_evaluation(self.store, self.proposal['id'], self.manifest,
            adapter_path=self.adapter, adapter_sha256=digest)
        report = self.api().evaluate(self.store, frozen)
        self.assertFalse(report['checks']['behavioral_passed'])
        self.assertTrue(report['checks']['comparison']['errors'])
        self.assertEqual(report['calls'], 0)

    def test_json_verdict_is_type_exact(self):
        api = self.api()
        self.assertFalse(api._json_equal(True, 1))
        self.assertFalse(api._json_equal(False, 0))
        self.assertFalse(api._json_equal(1, 1.0))
        self.assertTrue(api._json_equal({'b': 2, 'a': 1}, {'a': 1, 'b': 2}))

    def test_self_modifying_adapter_starts_from_pinned_bytes_for_every_case(self):
        source = (
            'import json\nfrom pathlib import Path\n'
            'def run(candidate, task_input):\n'
            '    Path(__file__).write_text("def run(candidate, task_input):\\n    return None\\n")\n'
            '    policy=json.loads(candidate.split("```json\\n")[1].split("```")[0])\n'
            '    a,b=task_input["operands"]\n'
            '    return a+b if policy["operation"]=="add" else a-b\n')
        digest = self.write_adapter(source)
        frozen = self.api().freeze_evaluation(self.store, self.proposal['id'], self.manifest,
            adapter_path=self.adapter, adapter_sha256=digest)
        report = self.api().evaluate(self.store, frozen)
        self.assertTrue(report['checks']['behavioral_passed'])
        self.assertEqual(report['calls'], 4)

    def test_timeout_reaps_adapter_descendants_on_posix(self):
        if os.name == 'nt':
            self.skipTest('POSIX process-group assertion')
        pid_file = self.root / 'child.pid'
        source = (
            'import subprocess,sys,time\nfrom pathlib import Path\n'
            'def run(candidate, task_input):\n'
            f'    child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])\n'
            f'    Path({str(pid_file)!r}).write_text(str(child.pid))\n'
            '    time.sleep(30)\n')
        digest = self.write_adapter(source)
        frozen = self.api().freeze_evaluation(self.store, self.proposal['id'], self.manifest,
            adapter_path=self.adapter, adapter_sha256=digest, task_timeout=0.5, total_timeout=2)
        report = self.api().evaluate(self.store, frozen)
        self.assertFalse(report['checks']['behavioral_passed'])
        child_pid = int(pid_file.read_text())
        deadline = __import__('time').monotonic() + 2
        while __import__('time').monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            __import__('time').sleep(0.02)
        else:
            self.fail('adapter descendant survived worker timeout')

    def test_adapter_is_snapshotted_for_the_entire_paired_run(self):
        api = self.api()
        frozen = self.freeze()
        original = api._run_case
        seen_paths = []

        def mutate_source_after_first_call(path, candidate, task_input, timeout):
            result = original(path, candidate, task_input, timeout)
            seen_paths.append(path)
            if len(seen_paths) == 1:
                self.adapter.write_text('raise RuntimeError("moved")\n')
            return result

        with mock.patch.object(api, '_run_case', side_effect=mutate_source_after_first_call):
            report = api.evaluate(self.store, frozen)
        self.assertTrue(report['checks']['behavioral_passed'])
        self.assertEqual(len(seen_paths), 4)
        self.assertEqual(len(set(seen_paths)), 1)
        self.assertNotEqual(seen_paths[0], self.adapter)


    def test_pycache_bypass_does_not_poison_subsequent_cases(self):
        """A valid poisoned cache must break an unprotected run, not this evaluator."""
        import shutil
        api = self.api()
        source = (
            'import json,os,py_compile\nfrom pathlib import Path\n'
            'def run(candidate, task_input):\n'
            '    path=Path(__file__)\n'
            '    original=path.read_bytes()\n'
            '    poisoned=b"def run(candidate, task_input):\\n    return 999\\n"\n'
            '    path.write_bytes(poisoned.ljust(len(original), b"#"))\n'
            '    os.utime(path, (946684800, 946684800))\n'
            '    py_compile.compile(str(path), doraise=True)\n'
            '    path.write_bytes(original)\n'
            '    os.utime(path, (946684800, 946684800))\n'
            '    policy=json.loads(candidate.split("```json\\n")[1].split("```")[0])\n'
            '    a,b=task_input["operands"]\n'
            '    return a+b if policy["operation"]=="add" else a-b\n')
        digest = self.write_adapter(source)
        # Prove the fixture itself exploits CPython's timestamp/size cache rule
        # even under -B: that flag prevents ordinary writes, not loading .pyc.
        probe = self.root / 'probe' / 'trusted_adapter.py'
        probe.parent.mkdir()
        probe.write_bytes(source.encode('utf-8'))
        self.assertEqual(api._run_case(probe, self.candidate, {'operands': [2, 3]}, 3), 5)
        probe.write_bytes(source.encode('utf-8'))
        os.utime(probe, (946684800, 946684800))
        self.assertEqual(api._run_case(probe, self.candidate, {'operands': [2, 3]}, 3), 999)
        shutil.rmtree(probe.parent / '__pycache__')
        frozen = api.freeze_evaluation(self.store, self.proposal['id'], self.manifest,
            adapter_path=self.adapter, adapter_sha256=digest)
        report = api.evaluate(self.store, frozen)
        self.assertTrue(report['checks']['behavioral_passed'])
        self.assertEqual(report['calls'], 4)


if __name__ == '__main__':
    unittest.main()
