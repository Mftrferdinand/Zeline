"""Held-out comparison gates use synthetic cases, never the live skill library."""
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class HeldOutComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        environment = patch.dict(os.environ, {'ZELINE_HOME': str(root / 'home')})
        environment.start()
        self.addCleanup(environment.stop)
        skills = importlib.import_module('zeline.skills')
        self.addCleanup(patch.stopall)
        patch.object(skills, 'PRIVATE_SKILLS_DIR', root / 'private').start()
        patch.object(skills, 'PUBLIC_SKILLS_DIR', root / 'public').start()
        skills.PRIVATE_SKILLS_DIR.mkdir()
        self.path = skills.PRIVATE_SKILLS_DIR / 'heldout-fixture.md'
        self.path.write_text(self.content('baseline'), encoding='utf-8')
        module = importlib.import_module('zeline.skillforge')
        self.store = module.ForgeStore(root / 'forge')
        self.proposal = self.store.propose('heldout-fixture', self.content('candidate'), 'Synthetic held-out gate')

    @staticmethod
    def content(body):
        return '---\nname: heldout-fixture\ndescription: Controlled evaluation fixture.\n---\n# Fixture\n' + body + '\n'

    def test_training_gain_without_held_out_gain_cannot_be_approved(self):
        def evaluator(stage):
            candidate = 'candidate' in (stage / 'SKILL.md').read_text(encoding='utf-8')
            return {'cases': {'train': candidate, 'holdout': True}}
        try:
            checks = self.store.test_comparison(self.proposal['id'], evaluator=evaluator,
                training_case_ids=('train',), held_out_case_ids=('holdout',), max_case_evaluations=4)
        except TypeError as exc:
            self.fail('Missing held-out comparison contract: ' + str(exc))
        self.assertFalse(checks['improvement_proven'])
        self.assertEqual(checks['evaluation_split']['held_out_gains'], [])
        with self.assertRaises(ValueError):
            self.store.review(self.proposal['id'], approve=True, reviewer='fixture-reviewer')
        self.assertIn('baseline', self.path.read_text(encoding='utf-8'))

    def compare(self, evaluator, **overrides):
        options = dict(training_case_ids=('train',), held_out_case_ids=('holdout',), max_case_evaluations=4)
        options.update(overrides)
        return self.store.test_comparison(self.proposal['id'], evaluator=evaluator, **options)

    @staticmethod
    def gaining_evaluator(stage):
        candidate = 'candidate' in (stage / 'SKILL.md').read_text(encoding='utf-8')
        return {'cases': {'train': True, 'holdout': candidate}}

    def assert_unapproved(self):
        row = self.store.get(self.proposal['id'])
        self.assertEqual(row['status'], 'tested')
        self.assertEqual(row['reviewer'], '')
        with self.assertRaises(ValueError):
            self.store.review(self.proposal['id'], approve=True, reviewer='fixture-reviewer')
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.content('baseline'))

    def audit_actions(self):
        with self.store.connect() as db:
            return [row[0] for row in db.execute(
                'SELECT action FROM audit WHERE proposal=? ORDER BY id', (self.proposal['id'],))]

    def test_held_out_gain_is_persisted_but_does_not_apply_candidate(self):
        calls = []
        def evaluator(stage):
            candidate = 'candidate' in (stage / 'SKILL.md').read_text(encoding='utf-8')
            calls.append(candidate)
            return {'cases': {'train': True, 'holdout': candidate}}
        checks = self.compare(evaluator)
        self.assertEqual(calls, [False, True])
        self.assertTrue(checks['improvement_proven'])
        self.assertEqual(checks['evaluation_split']['held_out_gains'], ['holdout'])
        self.assertEqual(checks['evaluation_split']['planned_case_evaluations'], 4)
        self.assertEqual(self.store.get(self.proposal['id'])['checks'], checks)
        self.assertEqual(self.store.get(self.proposal['id'])['status'], 'tested')
        with self.assertRaises(ValueError):
            self.store.apply(self.proposal['id'])
        self.assertIn('baseline', self.path.read_text(encoding='utf-8'))

    def test_overlapping_splits_are_rejected_before_evaluator_runs(self):
        calls = []
        with self.assertRaisesRegex(ValueError, 'disjoint'):
            self.compare(lambda stage: calls.append(stage), held_out_case_ids=('train',))
        self.assertEqual(calls, [])
        self.assertEqual(self.store.get(self.proposal['id'])['status'], 'pending')

    def test_budget_is_checked_before_evaluator_runs(self):
        calls = []
        with self.assertRaisesRegex(ValueError, 'budget'):
            self.compare(lambda stage: calls.append(stage), max_case_evaluations=2)
        self.assertEqual(calls, [])
        self.assertEqual(self.store.get(self.proposal['id'])['status'], 'pending')

    def test_invalid_contracts_fail_before_evaluator_runs(self):
        invalid = [dict(training_case_ids=('train', 'train')), dict(training_case_ids='train'),
                   dict(held_out_case_ids=()), dict(held_out_case_ids=None),
                   dict(max_case_evaluations=True), dict(max_case_evaluations=257),
                   dict(training_case_ids=('bad case',))]
        for options in invalid:
            with self.subTest(options=options):
                calls = []
                with self.assertRaises(ValueError):
                    self.compare(lambda stage: calls.append(stage), **options)
                self.assertEqual(calls, [])

    def test_missing_declared_held_out_case_leaves_no_approvable_evidence(self):
        checks = self.compare(lambda stage: {'cases': {'train': True}})
        self.assertFalse(checks['improvement_proven'])
        self.assertTrue(checks['comparison']['errors'])
        self.assertFalse(self.store.get(self.proposal['id'])['checks']['behavioral_passed'])
        with self.assertRaises(ValueError):
            self.store.review(self.proposal['id'], approve=True, reviewer='fixture-reviewer')

    def test_held_out_regression_blocks_training_gain(self):
        def evaluator(stage):
            candidate = 'candidate' in (stage / 'SKILL.md').read_text(encoding='utf-8')
            return {'cases': {'train': candidate, 'holdout': not candidate}}
        checks = self.compare(evaluator)
        self.assertFalse(checks['improvement_proven'])
        self.assertEqual(checks['evaluation_split']['held_out_regressions'], ['holdout'])
        with self.assertRaises(ValueError):
            self.store.review(self.proposal['id'], approve=True, reviewer='fixture-reviewer')

    def test_reused_mutable_result_is_snapshotted_between_evaluations(self):
        report = {'cases': {'train': True, 'holdout': False}}
        calls = []
        def evaluator(stage):
            candidate = 'candidate' in (stage / 'SKILL.md').read_text(encoding='utf-8')
            calls.append(candidate)
            report['cases']['holdout'] = candidate
            return report
        checks = self.compare(evaluator)
        report['cases'].clear()
        self.assertEqual(calls, [False, True])
        self.assertEqual(checks['comparison']['baseline'], {'train': True, 'holdout': False})
        self.assertEqual(checks['comparison']['candidate'], {'train': True, 'holdout': True})
        self.assertEqual(checks['comparison']['gains'], ['holdout'])
        self.assertTrue(checks['improvement_proven'])
        self.assertEqual(self.store.get(self.proposal['id'])['checks'], checks)

    def test_caller_split_list_mutation_cannot_relabel_held_out_gain(self):
        training, held_out = ['train'], ['holdout']
        calls = []
        def evaluator(stage):
            calls.append(stage)
            training[:] = ['holdout']
            held_out[:] = ['train']
            return self.gaining_evaluator(stage)
        checks = self.compare(evaluator, training_case_ids=training, held_out_case_ids=held_out)
        self.assertEqual(len(calls), 2)
        self.assertEqual(training, ['holdout'])
        self.assertEqual(held_out, ['train'])
        self.assertEqual(checks['evaluation_split']['training_case_ids'], ['train'])
        self.assertEqual(checks['evaluation_split']['held_out_case_ids'], ['holdout'])
        self.assertEqual(checks['evaluation_split']['held_out_gains'], ['holdout'])
        self.assertTrue(checks['improvement_proven'])
        self.assertEqual(self.store.get(self.proposal['id'])['checks'], checks)

    def test_extra_or_differing_candidate_ids_are_not_approvable(self):
        variants = [('declared-extra', {'train': True, 'holdout': True, 'extra': True}, True),
                    ('declared-different', {'train': True, 'other': True}, True),
                    ('undeclared-different', {'train': True, 'other': True}, False)]
        for label, candidate_cases, declared in variants:
            with self.subTest(variant=label):
                calls = []
                def evaluator(stage):
                    candidate = 'candidate' in (stage / 'SKILL.md').read_text(encoding='utf-8')
                    calls.append(candidate)
                    return {'cases': candidate_cases if candidate else {'train': True, 'holdout': False}}
                options = {} if declared else dict(training_case_ids=None, held_out_case_ids=None)
                checks = self.compare(evaluator, max_case_evaluations=6, **options)
                self.assertEqual(calls, [False, True])
                self.assertEqual(checks['comparison']['errors'], ['ValueError'])
                self.assertFalse(checks['improvement_proven'])
                self.assertFalse(checks['behavioral_passed'])
                self.assertEqual(self.store.get(self.proposal['id'])['checks'], checks)
                self.assert_unapproved()

    def test_nonboolean_candidate_verdict_is_not_approvable(self):
        for verdict in (1, 0, 'true', None):
            with self.subTest(verdict=verdict):
                calls = []
                def evaluator(stage):
                    candidate = 'candidate' in (stage / 'SKILL.md').read_text(encoding='utf-8')
                    calls.append(candidate)
                    return {'cases': {'train': True, 'holdout': verdict if candidate else False}}
                checks = self.compare(evaluator)
                self.assertEqual(calls, [False, True])
                self.assertEqual(checks['comparison']['errors'], ['ValueError'])
                self.assertFalse(checks['improvement_proven'])
                self.assertFalse(checks['behavioral_passed'])
                self.assert_unapproved()

    def test_review_cannot_approve_half_published_comparison(self):
        original_test = self.store.test
        observed = []
        def review_between_publications(*args, **kwargs):
            checks = original_test(*args, **kwargs)
            row = self.store.get(self.proposal['id'])
            observed.append(row['checks'])
            self.assertEqual(row['status'], 'tested')
            self.assertFalse(row['checks']['behavioral_passed'])
            self.assertFalse(row['checks']['improvement_proven'])
            self.assertNotIn('comparison', row['checks'])
            with self.assertRaisesRegex(ValueError, 'behavioral evaluator'):
                self.store.review(self.proposal['id'], approve=True, reviewer='fixture-reviewer')
            return checks
        with patch.object(self.store, 'test', side_effect=review_between_publications):
            checks = self.compare(self.gaining_evaluator)
        self.assertEqual(len(observed), 1)
        self.assertTrue(checks['improvement_proven'])
        self.assertNotIn('approved', self.audit_actions())
        reviewed = self.store.review(self.proposal['id'], approve=True, reviewer='fixture-reviewer')
        self.assertEqual(reviewed['status'], 'approved')
        self.assertEqual(reviewed['checks'], checks)
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.content('baseline'))

    def test_comparison_cas_preserves_interleaved_replacement_evidence(self):
        original_test = self.store.test
        replacement = []
        def replace_between_publications(*args, **kwargs):
            checks = original_test(*args, **kwargs)
            replacement.append(original_test(self.proposal['id']))
            return checks
        with patch.object(self.store, 'test', side_effect=replace_between_publications):
            with self.assertRaisesRegex(ValueError, 'evidence changed concurrently'):
                self.compare(self.gaining_evaluator)
        self.assertEqual(len(replacement), 1)
        self.assertEqual(self.store.get(self.proposal['id'])['checks'], replacement[0])
        self.assertNotIn('compared', self.audit_actions())
        self.assert_unapproved()

    def test_review_cas_rejects_interleaved_evidence_replacement(self):
        passing = self.compare(self.gaining_evaluator)
        self.assertTrue(passing['improvement_proven'])
        original_unchanged = self.store._unchanged
        replacement = []
        def replace_after_review_read(proposal):
            entry = original_unchanged(proposal)
            # A second real store finishes a retest after review read its proof.
            other_store = type(self.store)(self.store.root)
            replacement.append(other_store.test(self.proposal['id']))
            return entry
        with patch.object(self.store, '_unchanged', side_effect=replace_after_review_read):
            with self.assertRaisesRegex(ValueError, 'evidence changed concurrently'):
                self.store.review(self.proposal['id'], approve=True, reviewer='fixture-reviewer')
        self.assertEqual(len(replacement), 1)
        self.assertEqual(self.store.get(self.proposal['id'])['checks'], replacement[0])
        self.assertNotIn('approved', self.audit_actions())
        self.assert_unapproved()

    def test_active_base_change_between_publications_blocks_comparison(self):
        original_test = self.store.test
        intermediate = []
        def change_base_between_publications(*args, **kwargs):
            checks = original_test(*args, **kwargs)
            intermediate.append(checks)
            self.path.write_text(self.content('changed-base'), encoding='utf-8')
            return checks
        with patch.object(self.store, 'test', side_effect=change_base_between_publications):
            with self.assertRaisesRegex(ValueError, 'Active skill or companions changed'):
                self.compare(self.gaining_evaluator)
        self.assertEqual(len(intermediate), 1)
        row = self.store.get(self.proposal['id'])
        self.assertEqual(row['status'], 'tested')
        self.assertEqual(row['checks'], intermediate[0])
        self.assertFalse(row['checks']['behavioral_passed'])
        self.assertFalse(row['checks']['improvement_proven'])
        self.assertNotIn('compared', self.audit_actions())
        with self.assertRaisesRegex(ValueError, 'Active skill or companions changed'):
            self.store.review(self.proposal['id'], approve=True, reviewer='fixture-reviewer')
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.content('changed-base'))
    def test_comparison_cas_rejects_interleaved_candidate_revision_change(self):
        original_test = self.store.test
        def move_candidate_between_publications(*args, **kwargs):
            checks = original_test(*args, **kwargs)
            with self.store.connect() as db:
                db.execute('UPDATE proposals SET candidate=? WHERE id=?',
                           ('f' * 64, self.proposal['id']))
            return checks
        with patch.object(self.store, 'test', side_effect=move_candidate_between_publications):
            with self.assertRaisesRegex(ValueError, 'evidence changed concurrently'):
                self.compare(self.gaining_evaluator)
        row = self.store.get(self.proposal['id'])
        self.assertEqual(row['candidate'], 'f' * 64)
        self.assertFalse(row['checks']['behavioral_passed'])
        self.assertNotIn('compared', self.audit_actions())


if __name__ == '__main__':
    unittest.main()
