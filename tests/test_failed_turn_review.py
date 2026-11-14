"""A failed user turn must not immediately spend another provider call on review."""
import unittest
from unittest import mock
from tests import test_turn_admission as admission


class FailedTurnReviewTests(unittest.TestCase):
    setUp = admission.TurnAdmissionTests.setUp

    def test_provider_failure_defers_review_without_losing_pending_learning(self):
        session=self.store.get_or_create(self.identity,'full')
        session.agent._tool_calls_since_reflection=8
        observer=mock.Mock()
        with mock.patch.object(session.agent,'send',side_effect=self.module.ZelineError('400 fixture rejection')):
            with self.assertRaises(self.module.ZelineError):
                self.store.send(self.identity,'fixture task','full')
        with mock.patch.object(session.agent,'reflect',return_value='Review incomplete') as reflect:
            result=self.store.reflect(self.identity,on_review=observer)
        self.assertIsNone(result)
        reflect.assert_not_called();observer.assert_not_called()
        self.assertEqual(session.agent._tool_calls_since_reflection,8)

    def test_successful_followup_reenables_pending_review(self):
        session=self.store.get_or_create(self.identity,'full')
        session.agent._tool_calls_since_reflection=8
        with mock.patch.object(session.agent,'send',side_effect=self.module.ZelineError('fixture rejection')):
            with self.assertRaises(self.module.ZelineError):self.store.send(self.identity,'first','full')
        with mock.patch.object(session.agent,'send',return_value='Verified follow-up result'):
            self.assertEqual(self.store.send(self.identity,'continue','full'),'Verified follow-up result')
        with mock.patch.object(session.agent,'reflect',return_value='verified review') as reflect:
            self.assertEqual(self.store.reflect(self.identity),'verified review')
        reflect.assert_called_once()
        self.assertEqual(session.agent._tool_calls_since_reflection,8)

    def test_gateway_delivers_provider_error_without_second_review_request(self):
        import importlib
        import threading
        telegram=importlib.import_module('zeline.gateways.telegram')
        session=self.store.get_or_create(self.identity,'full')
        session.agent._tool_calls_since_reflection=8
        real_thread=threading.Thread
        def thread(*args,**kwargs):
            if kwargs.get('name','').startswith('zeline-reflect-'):
                return mock.Mock(start=kwargs['target'])
            return real_thread(*args,**kwargs)
        with mock.patch.object(session.agent,'_call_llm',side_effect=self.module.ZelineError('400 fixture rejection')) as provider, \
             mock.patch.object(telegram,'_api_call',return_value={'ok':True,'result':{'message_id':77}}) as api, \
             mock.patch.object(telegram,'_LiveStatus'), \
             mock.patch.object(telegram,'_start_working_heartbeat',return_value=mock.Mock()), \
             mock.patch.object(telegram.threading,'Thread',side_effect=thread):
            worker=telegram._start_agent_reply('fixture',self.store,chat_id=1,identity=self.identity,
                text='fixture task',tool_profile='full',reply_to_message_id=42)
            worker.join(5)
        self.assertFalse(worker.is_alive())
        provider.assert_called_once()
        replies=[call.kwargs.get('text','') for call in api.call_args_list if call.args[1]=='sendMessage']
        self.assertTrue(any('400 fixture rejection' in text for text in replies))
        self.assertFalse(any('Review incomplete' in text for text in replies))

    def test_stopped_turn_does_not_launch_an_optional_review(self):
        session=self.store.get_or_create(self.identity,'full')
        with mock.patch.object(session.agent,'send',return_value='Stopped.'):
            self.store.send(self.identity,'fixture task','full')
        with mock.patch.object(session.agent,'reflect') as reflect:
            self.assertIsNone(self.store.reflect(self.identity))
        reflect.assert_not_called()
