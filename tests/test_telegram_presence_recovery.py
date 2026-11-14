"""Reproduce presence loss at the real API/cooldown and progress-creation seams."""
import threading
import unittest
from unittest import mock
import test_telegram_presence as fixtures


class PresenceRecoveryTests(unittest.TestCase):
    setUp = fixtures.TelegramPresenceTests.setUp
    tearDown = fixtures.TelegramPresenceTests.tearDown

    def test_message_rate_limit_does_not_silence_the_typing_lane(self):
        t=self.telegram
        outcomes=[(False,{'ok':False,'error_code':429,'parameters':{'retry_after':20}}),
                  (True,{'ok':True,'result':True})]
        with mock.patch.object(t,'_API_COOLDOWNS',{}), mock.patch.object(t,'_bounded_api_post',side_effect=outcomes) as post:
            t._api_call('offline','editMessageText',attempts=1,chat_id=1,text='progress')
            result=t._api_call('offline','sendChatAction',attempts=1,chat_id=1,action='typing')
            self.assertTrue(result and result.get('ok'),'A progress 429 suppressed the independent typing heartbeat')
            self.assertEqual(post.call_count,2)

    def test_progress_creation_recovers_after_a_known_unsent_429(self):
        t=self.telegram;now=[100.0];sent=[]
        def post(url,params,*_args):
            sent.append(url)
            if len(sent)==1:return False,{'ok':False,'error_code':429,'parameters':{'retry_after':20}}
            return True,{'ok':True,'result':{'message_id':9}}
        with mock.patch.object(t,'_API_COOLDOWNS',{}), mock.patch.object(t.time,'monotonic',side_effect=lambda:now[0]), mock.patch.object(t,'_bounded_api_post',side_effect=post):
            live=t._LiveStatus('offline',1)
            live.add('📖 Reading agent.py L1-20')
            self.assertFalse(live._creation_unknown,'Telegram explicitly rejected creation; delivery is not unknown')
            now[0]=110.0;live.tick()
            self.assertEqual(len(sent),1,'Progress ignored the server retry_after')
            now[0]=135.0;live.tick()
            self.assertEqual(live.message_id,9,'A known-unsent 429 permanently disabled the whole progress bubble')
            self.assertEqual(sum(url.endswith('/sendMessage') for url in sent),2)

    def test_typing_is_restored_after_a_progress_message_without_new_user_input(self):
        t=self.telegram;progress_sent=threading.Event();restored=threading.Event()
        class Session:
            resumed=False
            def send(self,**callbacks):
                callbacks['on_tool']('read_file',{'path':'fixture.py'})
                if not progress_sent.wait(.5):raise AssertionError('Progress never reached the transport')
                self.resumed=restored.wait(.4)
                return 'Presence fixture completed.'
        session=Session()
        def api(_api,method,**params):
            if method=='sendMessage' and 'Reading fixture.py' in str(params.get('text','')):
                progress_sent.set()
            if method=='sendChatAction' and progress_sent.is_set():restored.set()
            return {'ok':True,'result':{'message_id':7}}
        with mock.patch.object(t,'_api_call',side_effect=api), mock.patch.object(t,'_start_reflection'), mock.patch.object(t,'_TYPING_INTERVAL_SECONDS',2.0):
            t._send_agent_reply('offline',session,chat_id=1,identity='presence:restore',text='fixture',tool_profile='safe')
        self.assertTrue(session.resumed,'Sending progress cleared typing and did not wake presence until another event/timer')

    def test_answer_inside_a_long_running_tool_resumes_typing_before_tool_result(self):
        from zeline import interaction
        t=self.telegram;work_resumed=threading.Event();typed=threading.Event();seen=[]
        identity='presence:approval-inside-tool'
        class Session:
            def send(self,**callbacks):
                callbacks['on_tool']('run_shell',{'command':'fixture'})
                answer=interaction.ask(identity,'Continue fixture?',options=['Continue'])
                if answer!='Continue':raise AssertionError('Fixture answer was not routed')
                work_resumed.set()
                seen.append(typed.wait(.3))
                callbacks['on_tool_result']('run_shell',{},'exit=0')
                return 'Completed.'
        def render(_api,_chat,entry):
            interaction.answer(identity,'Continue',request_id=entry.request_id)
        def api(_api,method,**params):
            if method=='sendChatAction' and work_resumed.is_set():typed.set()
            return {'ok':True,'result':{'message_id':7}}
        with mock.patch.object(t,'_api_call',side_effect=api), mock.patch.object(t,'_render_ask_question',side_effect=render), mock.patch.object(t,'_start_reflection'), mock.patch.object(t,'_TYPING_INTERVAL_SECONDS',.025):
            t._send_agent_reply('offline',Session(),chat_id=1,identity=identity,text='fixture',tool_profile='safe')
        self.assertEqual(seen,[True],'A resolved question kept presence paused throughout the resumed tool')

    def test_typing_is_restored_after_narration_ack(self):
        t=self.telegram;sent=threading.Event();restored=threading.Event();seen=[]
        class Session:
            def send(self,**callbacks):
                callbacks['on_narration']('Narration fixture')
                if not sent.wait(.5):raise AssertionError('Narration was not acknowledged')
                seen.append(restored.wait(.4))
                return 'Complete.'
        def api(_api,method,**params):
            if method=='sendMessage' and params.get('text')=='Narration fixture':sent.set()
            if method=='sendChatAction' and sent.is_set():restored.set()
            return {'ok':True,'result':{'message_id':8}}
        with mock.patch.object(t,'_api_call',side_effect=api),mock.patch.object(t,'_start_reflection'),mock.patch.object(t,'_TYPING_INTERVAL_SECONDS',2.0):
            t._send_agent_reply('offline',Session(),chat_id=1,identity='presence:narration',text='fixture',tool_profile='safe')
        self.assertEqual(seen,[True])

    def test_typing_rate_limit_is_still_respected_without_blocking_progress(self):
        t=self.telegram
        with mock.patch.object(t,'_API_COOLDOWNS',{}), mock.patch.object(t,'_bounded_api_post',side_effect=[
            (False,{'ok':False,'error_code':429,'parameters':{'retry_after':20}}),
            (True,{'ok':True,'result':{'message_id':9}})]) as post:
            t._api_call('offline','sendChatAction',attempts=1,chat_id=1,action='typing')
            t._api_call('offline','sendChatAction',attempts=1,chat_id=1,action='typing')
            result=t._api_call('offline','editMessageText',attempts=1,chat_id=1,text='progress')
            self.assertTrue(result and result.get('ok'))
            self.assertEqual(post.call_count,2)
