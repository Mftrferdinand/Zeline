"""Offline tests: run with disposable HOME and candidate-only PYTHONPATH."""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Block accidental network before importing the candidate.
def blocked(*a, **k):
    raise AssertionError('network forbidden')
socket.socket.connect = blocked
from zeline.gateways import telegram as tg


class Session:
    def __init__(self, action=None, reply='FINAL'):
        self.action, self.reply = action, reply
    def send(self, **kw):
        if self.action:
            self.action(kw)
        return self.reply


class TelegramFixTests(unittest.TestCase):
    def setUp(self):
        self.original = tg._api_call
        self.reflection = tg._start_reflection
        tg._start_reflection = lambda *a, **k: None
        self.tmp = tempfile.TemporaryDirectory()
        self.data = tg.config.DATA_DIR
        tg.config.DATA_DIR = Path(self.tmp.name)
    def tearDown(self):
        tg._api_call = self.original
        tg._start_reflection = self.reflection
        tg.config.DATA_DIR = self.data
        self.tmp.cleanup()
    def send(self, session, **kw):
        return tg._send_agent_reply('inert', session, chat_id=1, identity='test:1', text='x', tool_profile='full', **kw)
    def test_slow_typing_does_not_delay_tool_execution(self):
        entered, release, executed = threading.Event(), threading.Event(), threading.Event()
        def api(api, method, **kw):
            if method == 'sendChatAction':
                entered.set()
                release.wait(2)
            return {'ok': True, 'result': {'message_id': 1}}
        tg._api_call = api
        def action(kw):
            kw['on_tool']('terminal', {'command': 'true'})
            executed.set()
        worker = threading.Thread(target=lambda: self.send(Session(action)))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(executed.wait(.3), 'best-effort typing held the tool/model path')
        finally:
            release.set()
            worker.join(3)

    def test_narration_is_ordered_without_blocking_execution(self):
        entered, release, executed = threading.Event(), threading.Event(), threading.Event()
        messages = []
        def api(api, method, **kw):
            if method == 'sendMessage':
                if kw['text'] == 'NARRATION':
                    entered.set()
                    release.wait(2)
                messages.append(kw['text'])
            return {'ok': True, 'result': {'message_id': len(messages)}}
        tg._api_call = api
        def action(kw):
            kw['on_narration']('NARRATION')
            executed.set()
        worker = threading.Thread(target=lambda: self.send(Session(action)))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(executed.wait(.3), 'narration blocks execution')
            self.assertNotIn('FINAL', messages)
        finally:
            release.set()
            worker.join(3)
        self.assertEqual(messages, ['NARRATION', 'FINAL'])

    def test_progress_does_not_hold_agent_callbacks_and_cleanup_failure_keeps_final(self):
        entered, release, executed = threading.Event(), threading.Event(), threading.Event()
        messages = []
        def api(api, method, **kw):
            if method == 'sendMessage':
                if kw['text'] != 'FINAL':
                    entered.set()
                    release.wait(2)
                messages.append(kw['text'])
            return {'ok': True, 'result': {'message_id': 1}}
        tg._api_call = api
        old = tg._LiveStatus.finalize
        tg._LiveStatus.finalize = lambda self: (_ for _ in ()).throw(RuntimeError('cleanup'))
        def action(kw):
            kw['on_tool']('terminal', {'command': 'true'})
            self.assertTrue(entered.wait(1))
            kw['on_tool_result']('terminal', {}, '')
            kw['on_iteration'](2, 3)
            executed.set()
        worker = threading.Thread(target=lambda: self.send(Session(action)))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(executed.wait(.3), 'progress lock held agent callback')
        finally:
            release.set()
            worker.join(3)
            tg._LiveStatus.finalize = old
        self.assertIn('FINAL', messages)

    def test_final_receipt_is_private_and_empty_is_not_success(self):
        import json
        tg._api_call = lambda *a, **k: {'ok': True, 'result': {'message_id': 99}}
        self.assertIs(self.send(Session()), True)
        files = list((tg.config.DATA_DIR / 'telegram-outbox').glob('*.json'))
        self.assertEqual(len(files), 1, 'missing durable final receipt')
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        receipt = json.loads(files[0].read_text())
        self.assertEqual(receipt['parts'][0]['state'], 'ACKED')
        self.assertEqual(receipt['parts'][0]['message_id'], 99)
        self.assertIs(self.send(Session(reply='')), False)

    def test_unknown_final_part_is_never_replayed(self):
        from zeline.telegram_delivery import FinalReceipt
        receipt = FinalReceipt(tg.config.DATA_DIR, 1, ['one', 'two'])
        sent = []
        def send(index, part):
            sent.append(part)
            return {'ok':True,'result':{'message_id':1}} if index == 0 else None
        self.assertFalse(receipt.deliver(send, lambda:False))
        self.assertEqual(sent, ['one','two'])
        self.assertFalse(receipt.deliver(send, lambda:False))
        self.assertEqual(sent, ['one','two'], 'UNKNOWN delivery was replayed')

    def test_api_429_honors_retry_after_and_permanent_400_is_not_retried(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        responses = [SimpleNamespace(ok=False,status_code=429,json=lambda:{'ok':False,'error_code':429,'parameters':{'retry_after':0.02}}),
                     SimpleNamespace(ok=True,status_code=200,json=lambda:{'ok':True,'result':{'message_id':7}})]
        with patch.object(tg._HTTP,'post',side_effect=responses) as post:
            start = time.monotonic()
            result = self.original('inert','sendMessage',attempts=2)
            self.assertEqual(post.call_count, 2)
            self.assertEqual(result['result']['message_id'], 7)
            self.assertGreaterEqual(time.monotonic()-start, .015)
        response = SimpleNamespace(ok=False,status_code=400,json=lambda:{'ok':False,'error_code':400,'description':'bad request'})
        with patch.object(tg._HTTP,'post',return_value=response) as post:
            self.assertIsNone(self.original('inert','sendMessage',attempts=3))
            self.assertEqual(post.call_count,1)

    def test_known_unsent_429_recovers_only_unacknowledged_part(self):
        from zeline.telegram_delivery import FinalReceipt
        receipt = FinalReceipt(tg.config.DATA_DIR,1,['one','two'])
        sent = []
        def send(index, part):
            sent.append(part)
            if sent == ['one','two']:
                return {'ok':False,'error_code':429,'parameters':{'retry_after':0}}
            return {'ok':True,'result':{'message_id':index+1}}
        self.assertTrue(receipt.deliver(send,lambda:False))
        self.assertEqual(sent,['one','two','two'])
        self.assertTrue(all(part['state']=='ACKED' for part in receipt.record['parts']))

    def test_gateway_recovers_explicit_429_without_rerunning_agent(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        self.calls = 0
        sends = []
        def action(kwargs):
            self.calls += 1
        def post(url, **kwargs):
            if url.endswith('/sendMessage'):
                sends.append(kwargs['json']['text'])
                if len(sends)==1:
                    return SimpleNamespace(ok=False,status_code=429,json=lambda:{'ok':False,'error_code':429,'parameters':{'retry_after':0}})
                return SimpleNamespace(ok=True,status_code=200,json=lambda:{'ok':True,'result':{'message_id':9}})
            return SimpleNamespace(ok=True,status_code=200,json=lambda:{'ok':True,'result':True})
        with patch.object(tg._HTTP,'post',side_effect=post):
            self.assertIs(self.send(Session(action)), True)
        self.assertEqual(self.calls,1)
        self.assertEqual(sends,['FINAL','FINAL'])

    def test_cancel_closes_transport_without_sending_stale_final(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from zeline.telegram_delivery import TurnTransport
        created = []
        def create(*args,**kwargs):
            transport = TurnTransport(*args,**kwargs)
            created.append(transport)
            return transport
        stopped = threading.Event()
        tg._api_call = lambda *a,**kw: {'ok':True,'result':{'message_id':1}}
        with patch('zeline.telegram_delivery.TurnTransport',side_effect=create):
            self.send(Session(lambda kw:stopped.set()),_ticket=SimpleNamespace(cancel_event=stopped))
        for transport in created:
            transport.worker.join(.3)
            self.assertFalse(transport.worker.is_alive(),'Cancelled turn leaked an idle transport worker')

    def test_start_does_not_wait_for_initial_typing(self):
        entered, release, executed = threading.Event(), threading.Event(), threading.Event()
        starters = []
        calls = []
        def api(api,method,**kwargs):
            if method=='sendChatAction':
                calls.append(method)
                entered.set()
                release.wait(3)
            return {'ok':True,'result':{'message_id':1}}
        tg._api_call = api
        launcher = threading.Thread(target=lambda:starters.append(tg._start_agent_reply('inert',Session(lambda kw:executed.set()),chat_id=1,identity='test:1',text='x',tool_profile='full')))
        launcher.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(executed.wait(.3),'Polling/launch path waited for initial typing HTTP')
        finally:
            release.set()
            launcher.join(3)
            for starter in starters: starter.join(3)
        self.assertEqual(len(calls),1,'Initial typing was duplicated')

    def test_interrupt_ack_does_not_block_polling_or_tool_execution(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        entered,release,executed = threading.Event(),threading.Event(),threading.Event()
        ticket = SimpleNamespace(cancel_event=threading.Event(),previous_progress=object())
        class Store(Session):
            def admit(self,*a,**k): return ticket
            def wait_turn(self,*a,**k): return True
            def finish_turn(self,*a,**k): pass
        workers = []
        def ack(*a,**k):
            entered.set()
            release.wait(3)
        tg._api_call = lambda *a,**k:{'ok':True,'result':{'message_id':1}}
        with patch.object(tg,'_send_busy_ack',side_effect=ack):
            launcher = threading.Thread(target=lambda:workers.append(tg._start_agent_reply('inert',Store(lambda kw:executed.set()),chat_id=1,identity='test:1',text='x',tool_profile='full')))
            launcher.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(executed.wait(.3),'Interrupt acknowledgement blocked task execution')
            finally:
                release.set()
                launcher.join(3)
                for worker in workers:worker.join(3)

    def test_no_background_cleanup_retry_after_final(self):
        from unittest.mock import patch
        final_sent,late = threading.Event(),threading.Event()
        def api(api,method,**kwargs):
            if method=='editMessageText':
                if final_sent.is_set():late.set()
                return None
            if method=='sendMessage':final_sent.set()
            return {'ok':True,'result':{'message_id':1}}
        tg._api_call = api
        def finalize(live):
            live._settle_terminal('editMessageText',chat_id=1,message_id=9,text='closed')
        with patch.object(tg._LiveStatus,'finalize',finalize):
            self.assertIs(self.send(Session()),True)
            was_late = late.wait(.4)
            for thread in threading.enumerate():
                if thread.name=='zeline-status-cleanup':thread.join(4)
        self.assertFalse(was_late,'Cleanup emitted status edits after the final answer')

    def test_final_ready_coalesces_queued_status_into_one_completed_feed(self):
        from unittest.mock import patch
        from zeline.telegram_delivery import TurnTransport
        entered,release,final_ready = threading.Event(),threading.Event(),threading.Event()
        status_methods = []
        original_submit = TurnTransport.submit
        def submit(transport,action,**kw):
            value = original_submit(transport,action,**kw)
            if kw.get('final'):final_ready.set()
            return value
        def api(api,method,**kw):
            if method=='sendChatAction':
                entered.set();release.wait(3)
            elif kw.get('text')!='FINAL':
                status_methods.append(method)
            return {'ok':True,'result':{'message_id':1}}
        tg._api_call = api
        def action(callbacks):
            for n in range(8):callbacks['on_tool']('terminal',{'command':f'printf {n}'})
        with patch.object(TurnTransport,'submit',submit):
            worker=threading.Thread(target=lambda:self.send(Session(action)))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(final_ready.wait(1))
            finally:
                release.set();worker.join(3)
        self.assertEqual(status_methods,['sendMessage'],'Stale progress redraws delayed final delivery instead of one completed feed')

    def test_ambiguous_server_errors_are_unknown_and_never_replayed(self):
        from zeline.telegram_delivery import FinalReceipt
        for code in (500,502,503,504):
            with self.subTest(code=code):
                receipt=FinalReceipt(Path(self.tmp.name),1,['Final'])
                sent=[]
                def send(*args):
                    sent.append(args)
                    return {'ok':False,'error_code':code,'parameters':{'retry_after':0}} if len(sent)==1 else {'ok':True,'result':{'message_id':1}}
                self.assertFalse(receipt.deliver(send,lambda:False))
                self.assertEqual(len(sent),1)
                self.assertEqual(receipt.record['parts'][0]['state'],'UNKNOWN')
                self.assertFalse(receipt.deliver(send,lambda:False))
                self.assertEqual(len(sent),1)

    def test_receipt_construction_failure_closes_transport(self):
        from unittest.mock import patch
        from zeline import telegram_delivery as delivery
        original=delivery.TurnTransport
        transports=[]
        def create(*args,**kwargs):
            instance=original(*args,**kwargs)
            transports.append(instance)
            return instance
        tg._api_call=lambda *args,**kwargs:{'ok':True,'result':{'message_id':1}}
        with patch.object(delivery,'TurnTransport',side_effect=create), patch.object(delivery,'FinalReceipt',side_effect=OSError('fixture persistence failure')):
            with self.assertRaises(OSError):self.send(Session())
        try:
            self.assertTrue(transports)
            self.assertTrue(all(item.closed for item in transports))
        finally:
            for item in transports:item.close();item.worker.join(1)
        self.assertTrue(all(not item.worker.is_alive() for item in transports))




class ReceiptPortabilityContracts(unittest.TestCase):
    def test_receipt_uses_portable_fallback_without_directory_fsync(self):
        from unittest.mock import patch
        from zeline import telegram_delivery as delivery
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(delivery, '_SUPPORTS_FCHMOD', False, create=True), \
                    patch.object(delivery, '_SUPPORTS_DIRECTORY_FSYNC', False, create=True), \
                    patch.object(delivery.os, 'fchmod', side_effect=AssertionError('unsupported fchmod'), create=True), \
                    patch.object(delivery.os, 'open', wraps=delivery.os.open) as opened:
                receipt = delivery.FinalReceipt(Path(tmp), 1, ['Final'])
                self.assertTrue(receipt.path.is_file())
                self.assertFalse(any(Path(call.args[0]) == receipt.directory for call in opened.call_args_list))
                self.assertTrue(receipt.deliver(lambda *args: {'ok': True, 'result': {'message_id': 1}}, lambda: False))
