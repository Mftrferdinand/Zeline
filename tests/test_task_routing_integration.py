"""Telegram task references are inbound context, not outgoing reply decoration."""
import importlib
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch


class TaskRoutingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'HOME': self.tmp.name, 'ZELINE_HOME': self.tmp.name,
            'ZELINE_API_KEY': 'fixture', 'ZELINE_MODEL': 'fixture',
            'ZELINE_BASE_URL': 'http://127.0.0.1:1/v1'})
        env.start()
        self.addCleanup(env.stop)
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                sys.modules.pop(name, None)
        self.telegram = importlib.import_module('zeline.gateways.telegram')

    def test_reply_target_and_quote_reach_worker_separately_from_current_instruction(self):
        incoming = {'message': {'chat': {'id': 7}, 'message_id': 102,
            'text': 'lanjut', 'reply_to_message': {'message_id': 101,
            'text': 'Earlier dashboard task', 'from': {'is_bot': True}}}}
        with patch.object(self.telegram, '_start_agent_reply') as start, \
                patch.object(self.telegram.interaction, 'answer', return_value=False):
            self.telegram._dispatch_update('fixture-api', 'fixture-token', Mock(), incoming,
                allowed=[7], tool_profile='safe', stop_event=threading.Event())
        args = start.call_args.kwargs
        reference = args.get('task_reference', {})
        self.assertEqual(reference.get('reply_to_message_id'), 101)
        self.assertEqual(reference.get('message_id'), 102)
        self.assertEqual(reference.get('quoted_text'), 'Earlier dashboard task')
        self.assertEqual(args['text'], 'lanjut')


    def test_worker_preserves_inbound_task_reference(self):
        sessions = Mock()
        sessions.admit = None
        reference = {'message_id': 102, 'reply_to_message_id': 101, 'quoted_text': 'task A'}
        with patch.object(self.telegram, '_api_call', return_value={}), \
                patch.object(self.telegram, '_send_agent_reply', return_value=True) as send:
            worker = self.telegram._start_agent_reply('fixture-api', sessions,
                chat_id=7, identity='telegram:7', text='lanjut', tool_profile='safe',
                reply_to_message_id=102, task_reference=reference)
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(send.call_args.kwargs.get('task_reference'), reference)


    def test_agent_supplies_active_task_checkpoint_without_history_tool_call(self):
        import json
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        module.config.FALLBACK_MODELS = ()
        module.config.FALLBACK_MODEL = ''
        agent = module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='safe')
        response = Mock(ok=True, status_code=200, encoding='utf-8')
        response.text = json.dumps({'choices': [{'message': {'content': 'fixture answer'}}]})
        with patch.object(module.requests, 'post', return_value=response) as post:
            agent.send('Inspect Project Amber and record the next step')
        payload = post.call_args.kwargs['json']['messages']
        self.assertTrue(any('task_checkpoint' in str(item.get('content', ''))
                            for item in payload), 'Active task checkpoint was absent from provider context')
        self.assertIsNotNone(getattr(agent, 'last_task_id', None))
        self.assertEqual(post.call_count, 1)


    def test_delivery_passes_reference_to_session(self):
        sessions = Mock()
        sessions.send.return_value = 'fixture answer'
        reference = {'message_id': 102, 'reply_to_message_id': 101, 'quoted_text': 'task A'}
        with patch.object(self.telegram, '_api_call', return_value={'result': {'message_id': 900}}), \
                patch.object(self.telegram, '_LiveStatus'), \
                patch.object(self.telegram, '_start_working_heartbeat', return_value=Mock()), \
                patch.object(self.telegram, '_start_reflection'):
            self.telegram._send_agent_reply('fixture-api', sessions, chat_id=7,
                identity='telegram:7', text='lanjut', tool_profile='safe', task_reference=reference)
        self.assertEqual(sessions.send.call_args.kwargs.get('task_reference'), reference)


    def test_model_checkpoint_is_saved_as_operational_state_not_user_memory(self):
        import json
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        agent = module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='full')
        def response(message):
            reply = Mock(ok=True, status_code=200, encoding='utf-8')
            reply.text = json.dumps({'choices': [{'message': message}]})
            return reply
        checkpoint = {'summary': 'Inspected current source; no files changed.',
                      'next_step': 'Read amber.py before editing', 'artifacts': ['amber.py'],
                      'status': 'paused'}
        messages = [response({'content': '', 'tool_calls': [{'id': 'checkpoint-1', 'type': 'function',
            'function': {'name': 'task_checkpoint', 'arguments': json.dumps(checkpoint)}}]}),
                    response({'content': 'Source inspection saved.'})]
        with patch.object(module.requests, 'post', side_effect=messages):
            agent.send('Inspect Project Amber')
        task = agent._task_store.get(agent.identity, agent.last_task_id)
        self.assertEqual(task['next_step'], checkpoint['next_step'])
        self.assertEqual(task['artifacts'], ['amber.py'])
        self.assertEqual(task['status'], 'paused')


    def test_real_session_and_outgoing_reply_share_exact_task_identity(self):
        import json
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        sessions = importlib.import_module('zeline.sessions').SessionStore()
        response = Mock(ok=True, status_code=200, encoding='utf-8')
        response.text = json.dumps({'choices': [{'message': {'content': 'Amber inspected.'}}]})
        with patch.object(module.requests, 'post', return_value=response), \
                patch.object(self.telegram, '_api_call', return_value={'result': {'message_id': 900}}), \
                patch.object(self.telegram, '_LiveStatus'), \
                patch.object(self.telegram, '_start_working_heartbeat', return_value=Mock()), \
                patch.object(self.telegram, '_start_reflection'):
            self.telegram._send_agent_reply('fixture-api', sessions, chat_id=7,
                identity='telegram:7', text='Inspect Project Amber', tool_profile='safe',
                task_reference={'message_id': 100})
        store = importlib.import_module('zeline.task_context').TaskContextStore()
        focused = store.focus('telegram:7')
        self.assertIsNotNone(focused, 'Gateway never admitted the task to the real session')
        selected = store.begin('telegram:7', 'lanjut', reply_to_message_id=900)
        self.assertIsNotNone(selected['task'], 'Bot reply was not bound to its producing task')
        self.assertEqual(selected['task']['id'], focused['id'])


    def test_stop_preserves_actual_tool_result_as_checkpoint_evidence(self):
        import json
        from pathlib import Path
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        Path(self.tmp.name, 'amber.py').write_text('verified amber fixture', encoding='utf-8')
        agent = module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='full')
        response = Mock(ok=True, status_code=200, encoding='utf-8')
        response.text = json.dumps({'choices': [{'message': {'content': '', 'tool_calls': [
            {'id': 'read-amber', 'type': 'function', 'function': {'name': 'read_file',
             'arguments': json.dumps({'path': 'amber.py'})}}]}}]})
        stopped = threading.Event()
        with patch.object(module.requests, 'post', return_value=response):
            result = agent.send('Inspect Project Amber', should_stop=stopped.is_set,
                on_tool_result=lambda *args: stopped.set())
        self.assertEqual(result, 'Stopped.')
        record = agent._task_store.get(agent.identity, agent.last_task_id)
        self.assertEqual(record['status'], 'paused')
        self.assertTrue(any('verified amber fixture' in event.get('result', '')
                            and event['status'] == 'success' for event in record['tool_events']))
        self.assertIn('amber.py', record['artifacts'])


    def test_unresolved_continuation_cannot_mutate_workspace(self):
        import json
        from pathlib import Path
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        store = importlib.import_module('zeline.task_context').TaskContextStore()
        task = store.begin('telegram:7', 'Inspect Project Blue')['task']
        store.checkpoint('telegram:7', task['id'], status='completed')
        agent = module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='full')
        def response(message):
            reply = Mock(ok=True, status_code=200, encoding='utf-8')
            reply.text = json.dumps({'choices': [{'message': message}]})
            return reply
        replies = [response({'content': '', 'tool_calls': [{'id': 'bad-write', 'type': 'function',
            'function': {'name': 'write_file', 'arguments': json.dumps(
                {'path': 'accidental.txt', 'content': 'unrelated old work'})}}]}),
                   response({'content': 'Please name the task.'})]
        with patch.object(module.requests, 'post', side_effect=replies):
            agent.send('gas')
        self.assertFalse(Path(self.tmp.name, 'accidental.txt').exists(),
                         'Unresolved continuation was allowed to execute unrelated work')
        self.assertEqual(store.focus('telegram:7')['id'], task['id'])


    def test_unresolved_task_cannot_scan_or_open_unrelated_workspace_files(self):
        module = importlib.import_module('zeline.agent')
        agent = module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='full')
        agent.executor._task_resolution_required = True
        with patch.object(agent.executor, '_execute') as dispatch:
            for name, args in [('search_files', {'pattern': '*'}),
                               ('read_file', {'path': 'unrelated.txt'})]:
                result = agent.executor.run(name, args)
                self.assertIn('Task reference unresolved', result)
            dispatch.assert_not_called()

    def test_ambiguous_resume_uses_real_question_channel_before_model_work(self):
        import json
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        store = importlib.import_module('zeline.task_context').TaskContextStore()
        a = store.begin('telegram:7', 'Build Amber dashboard')['task']
        store.begin('telegram:7', 'Build Blue dashboard')
        interaction = importlib.import_module('zeline.interaction')
        choices = []
        def choose_amber(entry):
            choices.append(entry.options)
            return next(option for option in entry.options if 'Amber' in option)
        interaction.register_channel('telegram:7', choose_amber)
        self.addCleanup(interaction.unregister_channel, 'telegram:7')
        agent = module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='safe')
        response = Mock(ok=True, status_code=200, encoding='utf-8')
        response.text = json.dumps({'choices': [{'message': {'content': 'Amber ready.'}}]})
        with patch.object(module.requests, 'post', return_value=response) as post:
            agent.send('resume dashboard')
        self.assertEqual(len(choices), 1, 'No native task-choice question was asked')
        self.assertEqual(len(choices[0]), 2)
        self.assertEqual(agent.last_task_id, a['id'])
        self.assertEqual(post.call_count, 1)


    def test_a_stop_b_finish_compaction_restart_reply_a_executes_only_next_step(self):
        import json
        from pathlib import Path
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        sessions_module = importlib.import_module('zeline.sessions')
        sessions = sessions_module.SessionStore()
        Path(self.tmp.name, 'amber.py').write_text('AMBER_ALREADY_READ', encoding='utf-8')
        Path(self.tmp.name, 'amber_next.py').write_text('AMBER_NEXT_VERIFIED', encoding='utf-8')
        def response(content='', calls=None):
            message = {'content': content}
            if calls:
                message['tool_calls'] = [{'id': str(index), 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(args)}}
                    for index, (name, args) in enumerate(calls)]
            reply = Mock(ok=True, status_code=200, encoding='utf-8')
            reply.text = json.dumps({'choices': [{'message': message}]})
            return reply
        observed = []
        def stopped_result(name, args, result):
            observed.append((name, args, result))
            if name == 'read_file':
                sessions.stop('telegram:7')
        a_response = response(calls=[('task_checkpoint', {'summary': 'Amber source inspection',
            'next_step': 'Read amber_next.py next; amber.py is already inspected'}),
            ('read_file', {'path': 'amber.py'})])
        with patch.object(module.requests, 'post', return_value=a_response):
            self.assertEqual(sessions.send('telegram:7', 'Inspect Project Amber', 'full',
                workspace=self.tmp.name, task_reference={'message_id': 10},
                on_tool_result=stopped_result), 'Stopped.')
        store = importlib.import_module('zeline.task_context').TaskContextStore()
        a = store.focus('telegram:7')
        sessions.bind_task_message('telegram:7', 901, a['id'])
        b_responses = [response(calls=[('task_checkpoint', {'status': 'completed',
                       'summary': 'Blue fixture inspection complete'})]), response('Blue done.')]
        with patch.object(module.requests, 'post', side_effect=b_responses):
            sessions.send('telegram:7', 'BLUE_SHOULD_NOT_CONTROL_RESUME', 'full',
                workspace=self.tmp.name, task_reference={'message_id': 20})
        b = store.focus('telegram:7')
        history = sessions._sessions['telegram:7'].agent.export_history()
        noise = [item for index in range(45) for item in (
            {'role': 'user', 'content': 'BLUE_SHOULD_NOT_CONTROL_RESUME ' + str(index)},
            {'role': 'assistant', 'content': 'Blue historical noise ' + str(index)})]
        sessions._persistence.save('telegram:7', [*history, *noise], 'Blue')
        rebuilt = sessions_module.SessionStore()
        payloads = []
        replies = [response(calls=[('read_file', {'path': 'amber_next.py'})]), response('Amber next step read.')]
        def resumed_provider(*args, **kwargs):
            payloads.append(json.dumps(kwargs['json']['messages'], ensure_ascii=False))
            return replies.pop(0)
        def resumed_result(name, args, result):
            observed.append((name, args, result))
        with patch.object(module.requests, 'post', side_effect=resumed_provider):
            result = rebuilt.send('telegram:7', 'lanjut', 'full', workspace=self.tmp.name,
                task_reference={'message_id': 30, 'reply_to_message_id': 901},
                on_tool_result=resumed_result)
        self.assertEqual(result, 'Amber next step read.')
        self.assertEqual(store.focus('telegram:7')['id'], a['id'])
        self.assertEqual(store.get('telegram:7', b['id'])['status'], 'completed')
        self.assertEqual([args['path'] for name, args, result in observed if name == 'read_file'],
                         ['amber.py', 'amber_next.py'])
        self.assertTrue(any('AMBER_NEXT_VERIFIED' in result for name, args, result in observed))
        self.assertEqual(len(payloads), 2, 'Resume unexpectedly needed repeated history rounds')
        self.assertTrue(all('Read amber_next.py next' in payload for payload in payloads))
        self.assertTrue(all('BLUE_SHOULD_NOT_CONTROL_RESUME' not in payload for payload in payloads),
                        'Unrelated B transcript leaked into resumed A provider context')


    def test_deictic_followup_keeps_current_conversation_without_forced_recall(self):
        import json
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        agent = module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='safe')
        payloads = []

        def response(*args, **kwargs):
            payloads.append(json.dumps(kwargs['json']['messages'], ensure_ascii=False))
            reply = Mock(ok=True, status_code=200, encoding='utf-8')
            reply.text = json.dumps({'choices': [{'message': {'content': 'Fixture response.'}}]})
            return reply

        with patch.object(module.requests, 'post', side_effect=response):
            agent.send('Current file is amber-list.txt', task_reference={'message_id': 10})
            agent.send('masukin itu juga lalu kirim file-nya', task_reference={'message_id': 20})

        self.assertEqual(len(payloads), 2)
        self.assertIn('Current file is amber-list.txt', payloads[1])
        self.assertIn('masukin itu juga lalu kirim file-nya', payloads[1])

    def test_second_resumed_turn_does_not_reintroduce_other_task_history(self):
        import json
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        agent = module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='safe')
        store = importlib.import_module('zeline.task_context').TaskContextStore()
        def response():
            reply = Mock(ok=True, status_code=200, encoding='utf-8')
            reply.text = json.dumps({'choices': [{'message': {'content': 'Fixture checkpoint retained.'}}]})
            return reply
        with patch.object(module.requests, 'post', side_effect=lambda *a, **kw: response()):
            agent.send('Inspect Project Amber', task_reference={'message_id': 10})
            agent.send('BLUE_PRIVATE_UNRELATED_CONTEXT', task_reference={'message_id': 20})
            agent.send('lanjut', task_reference={'message_id': 30, 'reply_to_message_id': 10})
        payloads = []
        def provider(*args, **kwargs):
            payloads.append(json.dumps(kwargs['json']['messages']))
            return response()
        with patch.object(module.requests, 'post', side_effect=provider):
            agent.send('lanjut')
        self.assertNotIn('BLUE_PRIVATE_UNRELATED_CONTEXT', payloads[0])
        self.assertIn('Fixture checkpoint retained.', payloads[0])
        self.assertEqual(store.focus('telegram:7')['title'], 'Inspect Project Amber')

    def test_restart_with_same_focus_does_not_reintroduce_parked_history(self):
        import json
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        agent = module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='safe')
        def response(*args, **kwargs):
            reply = Mock(ok=True, status_code=200, encoding='utf-8')
            reply.text = json.dumps({'choices': [{'message': {'content': 'Fixture response.'}}]})
            return reply
        with patch.object(module.requests, 'post', side_effect=response):
            agent.send('Inspect Project Amber', task_reference={'message_id': 10})
            agent.send('BLUE_PARKED_HISTORY', task_reference={'message_id': 20})
            agent.send('lanjut', task_reference={'reply_to_message_id': 10})
        rebuilt = module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='safe')
        rebuilt.messages = agent.export_history()
        with patch.object(module.requests, 'post', side_effect=response) as post:
            rebuilt.send('lanjut')
        payload = json.dumps(post.call_args.kwargs['json']['messages'])
        self.assertNotIn('BLUE_PARKED_HISTORY', payload)
        self.assertIn('Inspect Project Amber', payload)

    def test_task_finalization_failure_still_releases_turn_controls(self):
        import json
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        context = importlib.import_module('zeline.task_context')
        agent = module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='safe')
        response = Mock(ok=True, status_code=200, encoding='utf-8')
        response.text = json.dumps({'choices': [{'message': {'content': 'fixture answer'}}]})
        error = None
        with patch.object(module.requests, 'post', return_value=response), \
                patch.object(context.TaskContextStore, 'pause', side_effect=OSError('fixture failure')):
            try:
                agent.send('Inspect Amber', should_stop=lambda: False)
            except Exception as exc:
                error = exc
        self.assertIsNone(agent._should_stop)
        self.assertIsNone(agent.executor._run_budget)
        self.assertIsNone(agent._on_checkpoint)
        self.assertEqual(agent._turn_task_context, '')
        self.assertIsInstance(error, module.ZelineError)
        self.assertNotIn('fixture failure', str(error))


    def test_new_session_detaches_focus_but_preserves_explicit_old_reply(self):
        context = importlib.import_module('zeline.task_context')
        sessions_module = importlib.import_module('zeline.sessions')
        store = context.TaskContextStore()
        a = store.begin('telegram:7', 'Build Amber', message_id=10)['task']
        sessions_module.SessionStore().reset('telegram:7')
        self.assertIsNone(store.focus('telegram:7'))
        self.assertIsNone(store.begin('telegram:7', 'lanjut')['task'])
        recovered = store.begin('telegram:7', 'lanjut', reply_to_message_id=10)
        self.assertEqual(recovered['task']['id'], a['id'])


    def test_narration_progress_and_final_messages_all_resume_producing_task(self):
        import json
        from pathlib import Path
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        Path(self.tmp.name, 'amber.py').write_text('Amber fixture', encoding='utf-8')
        sessions = importlib.import_module('zeline.sessions').SessionStore()
        def response(message):
            result = Mock(ok=True, status_code=200, encoding='utf-8')
            result.text = json.dumps({'choices': [{'message': message}]})
            return result
        replies = [response({'content': 'Amber working.', 'tool_calls': [{'id': 'read', 'type': 'function',
            'function': {'name': 'read_file', 'arguments': json.dumps({'path': 'amber.py'})}}]}),
                   response({'content': 'Amber answer.'})]
        delivered = []
        def api_call(api, method, **kwargs):
            if method == 'sendMessage':
                message_id = 900 + len(delivered)
                delivered.append((message_id, kwargs.get('text')))
                return {'result': {'message_id': message_id}}
            return {'ok': True}
        with patch.object(module.requests, 'post', side_effect=replies), \
                patch.object(self.telegram, '_api_call', side_effect=api_call), \
                patch.object(self.telegram, '_start_working_heartbeat', return_value=Mock()), \
                patch.object(self.telegram, '_start_reflection'):
            self.telegram._send_agent_reply('fixture-api', sessions, chat_id=7, identity='telegram:7',
                text='Inspect Amber', tool_profile='full', task_reference={'message_id': 100})
        store = importlib.import_module('zeline.task_context').TaskContextStore()
        task = store.focus('telegram:7')
        self.assertGreaterEqual(len(delivered), 3)
        for message_id, text in delivered:
            selected = store.begin('telegram:7', 'lanjut', reply_to_message_id=message_id)
            self.assertTrue(selected['task'] is not None, 'Unmapped task output: ' + str(text)[:80])
            self.assertEqual(selected['task']['id'], task['id'])


    def test_file_and_question_outputs_preserve_task_reply_anchor(self):
        import json
        from pathlib import Path
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        sessions = importlib.import_module('zeline.sessions').SessionStore()
        interaction = importlib.import_module('zeline.interaction')
        Path(self.tmp.name, 'report.txt').write_text('Fixture report', encoding='utf-8')
        def response(message):
            reply = Mock(ok=True, status_code=200, encoding='utf-8')
            reply.text = json.dumps({'choices': [{'message': message}]})
            return reply
        responses = [response({'content': '', 'tool_calls': [
            {'id': 'file', 'type': 'function', 'function': {'name': 'send_file',
                'arguments': json.dumps({'path': 'report.txt'})}},
            {'id': 'question', 'type': 'function', 'function': {'name': 'ask_user',
                'arguments': json.dumps({'question': 'Next?', 'options': ['Next', 'Stop']})}}]}),
                     response({'content': 'Fixture report delivered.'})]
        def api_call(api, method, **kwargs):
            if method == 'sendMessage' and kwargs.get('reply_markup'):
                interaction.answer('telegram:7', 'Next')
                return {'result': {'message_id': 920}}
            return {'result': {'message_id': 930}}
        upload = Mock(ok=True)
        upload.json.return_value = {'ok': True, 'result': {'message_id': 910}}
        with patch.object(module.requests, 'post', side_effect=responses), \
                patch.object(self.telegram._HTTP, 'post', return_value=upload), \
                patch.object(self.telegram, '_api_call', side_effect=api_call), \
                patch.object(self.telegram, '_ask_prepare_card', return_value=True), \
                patch.object(self.telegram, '_start_working_heartbeat', return_value=Mock()), \
                patch.object(self.telegram, '_start_reflection'):
            self.telegram._send_agent_reply('fixture-api', sessions, chat_id=7, identity='telegram:7',
                text='Prepare Amber report', tool_profile='full', task_reference={'message_id': 100})
        store = importlib.import_module('zeline.task_context').TaskContextStore()
        task = store.focus('telegram:7')
        for message_id in (910, 920):
            selected = store.begin('telegram:7', 'lanjut', reply_to_message_id=message_id)
            self.assertTrue(selected['task'] is not None, 'Unmapped media/card message: ' + str(message_id))
            self.assertEqual(selected['task']['id'], task['id'])


    def test_attachment_worker_receives_native_reply_reference(self):
        incoming = {'message': {'chat': {'id': 7}, 'message_id': 102,
            'caption': 'lanjut', 'document': {'file_id': 'fixture-doc', 'file_name': 'report.txt'},
            'reply_to_message': {'message_id': 101, 'text': 'Earlier Amber task'}}}
        with patch.object(self.telegram, '_start_agent_reply') as start:
            self.telegram._dispatch_update('fixture-api', 'fixture-token', Mock(), incoming,
                allowed=[7], tool_profile='safe', stop_event=threading.Event())
        reference = start.call_args.kwargs.get('task_reference', {})
        self.assertEqual(reference.get('message_id'), 102)
        self.assertEqual(reference.get('reply_to_message_id'), 101)


    def test_stop_acknowledgement_is_an_anchor_for_the_stopped_task(self):
        import json
        module = importlib.import_module('zeline.agent')
        module.config.STREAM_RESPONSES = False
        sessions = importlib.import_module('zeline.sessions').SessionStore()
        response = Mock(ok=True, status_code=200, encoding='utf-8')
        response.text = json.dumps({'choices': [{'message': {'content': 'Amber paused.'}}]})
        with patch.object(module.requests, 'post', return_value=response):
            sessions.send('telegram:7', 'Inspect Amber', 'safe', workspace=self.tmp.name)
        store = importlib.import_module('zeline.task_context').TaskContextStore()
        task = store.focus('telegram:7')
        with patch.object(self.telegram, '_api_call', return_value={'result': {'message_id': 901}}):
            self.telegram._handle_command_update('fixture-api', '/stop', sessions, 'telegram:7', 7,
                stop_event=threading.Event(), tool_profile='safe', allowed=[7])
        selected = store.begin('telegram:7', 'lanjut', reply_to_message_id=901)
        self.assertTrue(selected['task'] is not None)
        self.assertEqual(selected['task']['id'], task['id'])


if __name__ == '__main__':
    unittest.main()
