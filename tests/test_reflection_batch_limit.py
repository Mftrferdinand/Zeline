"""Regression for existing host reflection guards, not the manual owner helper."""
import importlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class ReflectionHostBoundaryTests(unittest.TestCase):
    def context(self, reflection=True):
        return SimpleNamespace(
            _reflection_mode=reflection, _cancelled=lambda: False,
            execution_policy=SimpleNamespace(check=Mock(return_value=None)),
            _enabled_native_defs=lambda: [SimpleNamespace(name='add_memory')],
            memory=SimpleNamespace(add_receipt=Mock(side_effect=AssertionError('must not write'))),
            _dispatch=Mock(return_value='owner-operation'),
        )

    def test_reflection_rejects_batches_and_self_assigned_authority_before_storage(self):
        executor = importlib.import_module('zeline.tools').ToolExecutor
        for args in (
            {'operations': [{'action': 'add', 'text': 'One'}]},
            {'operations': [{'action': 'add', 'text': 'One'}, {'action': 'add', 'text': 'Two'}]},
            {'fact': 'One', 'source': 'user'},
            {'fact': 'One', 'confidence': 1.0},
        ):
            with self.subTest(args=args):
                context = self.context()
                reply = executor._execute(context, 'add_memory', args)
                self.assertTrue(reply.startswith('ERROR'), reply)
                context.memory.add_receipt.assert_not_called()
                context._dispatch.assert_not_called()

    def test_manual_owner_batches_keep_the_existing_dispatch_contract(self):
        executor = importlib.import_module('zeline.tools').ToolExecutor
        context = self.context(reflection=False)
        args = {'operations': [{'action': 'add', 'text': 'One'}, {'action': 'add', 'text': 'Two'}]}
        self.assertEqual(executor._execute(context, 'add_memory', args), 'owner-operation')
        context._dispatch.assert_called_once_with('add_memory', args)

    def test_single_reflection_fact_has_host_managed_source(self):
        executor = importlib.import_module('zeline.tools').ToolExecutor
        context = self.context()
        context.memory.add_receipt = Mock(return_value={
            'version': 1, 'kind': 'memory', 'state': 'created', 'message': 'fixture'})
        reply = json.loads(executor._execute(context, 'add_memory', {'fact': 'One lesson'}))
        self.assertTrue(reply['success'])
        context.memory.add_receipt.assert_called_once_with('One lesson', source='reflection', origin_turn=None)
