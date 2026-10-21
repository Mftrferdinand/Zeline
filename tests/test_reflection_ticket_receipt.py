"""Reflection must expose the owned ticket before work starts."""
import importlib
import unittest
from unittest import mock
from tests import test_agent as support


class ReflectionTicketReceiptTests(unittest.TestCase):
    setUp = support.AgentLoopTests.setUp
    tearDown = support.AgentLoopTests.tearDown

    def test_reflection_publishes_owned_ticket_before_review(self):
        sessions = importlib.import_module('zeline.sessions')
        store = sessions.SessionStore()
        session = store.get_or_create('telegram:review-ticket', 'full')
        events = []
        def receipt(ticket):
            self.assertIs(session.active_turn, ticket)
            self.assertFalse(ticket.cancel_event.is_set())
            events.append('ticket')
        def review(**kwargs):
            events.append('review')
            return 'verified fixture review'
        with mock.patch.object(session.agent, 'reflect', side_effect=review):
            result = store.reflect('telegram:review-ticket', on_ticket=receipt)
        self.assertEqual(result, 'verified fixture review')
        self.assertEqual(events, ['ticket', 'review'])
        self.assertFalse(session.running)
        self.assertIsNone(session.active_turn)
