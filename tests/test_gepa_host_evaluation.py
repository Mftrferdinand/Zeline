"""GEPA training results are checked by a separate host-owned held-out gate."""
import hashlib
import importlib
import importlib.util
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


@unittest.skipUnless(importlib.util.find_spec('gepa') is not None and
                     'fork' in multiprocessing.get_all_start_methods(),
                     'GEPA host handoff requires optional optimizer and Unix fork')
class GepaHostEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = mock.patch.dict(os.environ, {'ZELINE_HOME': str(self.root / 'state')})
        env.start()
        self.addCleanup(env.stop)
        for key in tuple(sys.modules):
            if key == 'zeline' or key.startswith('zeline.'):
                sys.modules.pop(key, None)
        self.forge = importlib.import_module('zeline.skillforge')
        skills = importlib.import_module('zeline.skills')
        self.store = self.forge.ForgeStore()
        self.active = skills.PRIVATE_SKILLS_DIR / 'arithmetic' / 'SKILL.md'
        self.active.parent.mkdir(parents=True, exist_ok=True)
        self.seed = ('---\nname: arithmetic\ndescription: Arithmetic policy\n---\n'
                     '# Arithmetic\n```json\n{"operation":"subtract"}\n```\n')
        self.good = self.seed.replace('subtract', 'add')
        self.active.write_text(self.seed)
        self.adapter = self.root / 'trusted.py'
        self.adapter.write_text('import json\ndef run(candidate, task_input):\n'
                                '    operation=json.loads(candidate.split("```json\\n")[1].split("```")[0])["operation"]\n'
                                '    a,b=task_input["operands"]\n'
                                '    return a+b if operation=="add" else a-b\n')
        self.manifest = {'training': [{'id': 'train', 'input': {'operands': [2, 3]}, 'expected': 5}],
                         'held_out': [{'id': 'hidden', 'input': {'operands': [7, 4]}, 'expected': 11}]}
        self.seen = []

    def run_search(self, manifest=None, **overrides):
        from zeline.gepa_host_evaluation import evaluate_training_search
        def score(text, case):
            self.seen.append(case['id'])
            return '"add"' in text
        kwargs = dict(store=self.store, name='arithmetic', rationale='Improve arithmetic task operation',
                      seed_text=self.seed, proposals=[self.good], manifest=manifest or self.manifest,
                      adapter_path=self.adapter, adapter_sha256=hashlib.sha256(self.adapter.read_bytes()).hexdigest(),
                      score_case=score, max_metric_calls=16, max_proposals=1, timeout=5)
        kwargs.update(overrides)
        return evaluate_training_search(**kwargs)

    def test_training_selection_handoff_executes_host_heldout_without_applying(self):
        receipt = self.run_search()
        self.assertTrue(receipt['optimizer_executed'])
        self.assertTrue(receipt['checks']['behavioral_passed'])
        self.assertEqual(receipt['checks']['evaluation_split']['held_out_case_ids'], ['hidden'])
        self.assertEqual(receipt['training']['selected_sha256'], self.forge.revision_of(self.good))
        self.assertEqual(self.store.get(receipt['proposal_id'])['status'], 'tested')
        self.assertEqual(self.active.read_text(), self.seed)
        self.assertEqual(receipt['training']['scope'], 'training_only')
        self.assertEqual(receipt['training']['selected_index'], 1)

    def test_training_checker_receives_no_heldout_or_expected_labels(self):
        marker = self.root / 'seen-ids'
        def score(text, case):
            assert set(case) == {'id', 'input'}
            marker.write_text(marker.read_text() + case['id'] + '\n' if marker.exists()
                              else case['id'] + '\n')
            return '"add"' in text
        receipt = self.run_search(score_case=score)
        self.assertTrue(receipt['checks']['behavioral_passed'])
        self.assertEqual(set(marker.read_text().splitlines()), {'train'})

    def test_adapter_changed_by_checker_cannot_create_passing_evidence(self):
        def changes_adapter(text, case):
            self.adapter.write_text(self.adapter.read_text() + '\n# moved\n')
            return '"add"' in text
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            self.run_search(score_case=changes_adapter)
        self.assertFalse(any(row['status'] == 'approved' for row in self.store.proposals()))

    def test_companion_changed_during_search_rejects_before_proposal(self):
        companion = self.active.parent / 'references' / 'policy.md'
        companion.parent.mkdir()
        companion.write_text('initial')
        def changes_companion(text, case):
            companion.write_text('changed')
            return '"add"' in text
        with self.assertRaisesRegex(ValueError, 'companions changed during training search'):
            self.run_search(score_case=changes_companion)
        self.assertEqual(self.store.proposals(), [])
        self.assertEqual(self.active.read_text(), self.seed)

    def test_stale_seed_fails_before_proposal_or_search(self):
        with self.assertRaisesRegex(ValueError, 'seed.*active'):
            self.run_search(seed_text=self.seed + '\nchanged')
        self.assertEqual(self.store.proposals(), [])

    def test_heldout_failure_never_promotes_training_winner(self):
        manifest = {'training': self.manifest['training'],
                    'held_out': [{'id': 'hidden', 'input': {'operands': [7, 4]}, 'expected': 3}]}
        receipt = self.run_search(manifest=manifest)
        self.assertFalse(receipt['checks']['behavioral_passed'])
        self.assertEqual(self.store.get(receipt['proposal_id'])['status'], 'tested')
        self.assertEqual(self.active.read_text(), self.seed)

    def test_no_training_gain_does_not_stage_proposal(self):
        with self.assertRaisesRegex(ValueError, 'training improvement'):
            self.run_search(score_case=lambda *_: True)
        self.assertEqual(self.store.proposals(), [])


if __name__ == '__main__':
    unittest.main()
