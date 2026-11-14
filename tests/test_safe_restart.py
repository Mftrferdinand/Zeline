"""A deployment must defer instead of killing productive in-flight work."""
import ast
import importlib
import inspect
from types import SimpleNamespace
import unittest
from unittest import mock
import test_agent as fixtures


class SafeRestartTests(unittest.TestCase):
    setUp=fixtures.AgentLoopTests.setUp
    tearDown=fixtures.AgentLoopTests.tearDown

    def service(self):
        return importlib.import_module('zeline.gateway_service')

    def test_drain_deadline_never_escalates_to_kill(self):
        s=self.service();state={'pid':12345,'drain':True}
        with mock.patch.object(s,'IS_WINDOWS',False),mock.patch.object(s,'_load_state',return_value=state),mock.patch.object(s,'_process_matches_state',return_value=True),mock.patch.object(s,'_signal_process',return_value=True),mock.patch.object(s.time,'monotonic',side_effect=[0,100]),mock.patch.object(s,'stop',return_value=(True,'forced stop')) as stop:
            ok,message=s.drain_then_stop(drain_timeout=1)
        self.assertFalse(ok)
        stop.assert_not_called()
        self.assertIn('deferred',message.lower())

    def handler(self,finished):
        cli=importlib.import_module('zeline.cli')
        node=next(n for n in ast.walk(ast.parse(inspect.getsource(cli))) if isinstance(n,ast.FunctionDef) and n.name=='drain_and_shutdown')
        namespace={'screen':None,'config':SimpleNamespace(RESTART_DRAIN_TIMEOUT=1),'print':mock.Mock(),
          'review_stop':mock.Mock(),'review_thread':None,'durable_worker':mock.Mock(),
          'cron':mock.Mock(),'runtime':mock.Mock(),'sessions':mock.Mock()}
        namespace['sessions'].drain.return_value=(finished,[] if finished else ['fixture-task'])
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-drain-handler>','exec'),namespace)
        return namespace

    def test_busy_gateway_resumes_admission_and_keeps_workers_alive(self):
        n=self.handler(False);n['drain_and_shutdown']()
        n['sessions'].resume.assert_called_once()
        for key in ['cron','durable_worker','runtime']:
            n[key].stop.assert_not_called()
        n['review_stop'].set.assert_not_called()

    def test_finished_gateway_still_shuts_down_cleanly(self):
        n=self.handler(True);n['drain_and_shutdown']()
        n['sessions'].resume.assert_not_called()
        for key in ['cron','durable_worker','runtime']:
            n[key].stop.assert_called_once()

    def test_restart_does_not_start_or_claim_success_after_deferred_drain(self):
        cli=importlib.import_module('zeline.cli')
        with mock.patch.object(cli.gateway_service,'drain_then_stop',return_value=(False,'Restart deferred: active work continues.')),mock.patch.object(cli.gateway_service,'start',return_value=(True,'already running')) as start,mock.patch.object(cli.gateway_service,'wait_until_connected',return_value=(True,[])),mock.patch.object(cli,'print'):
            result=cli.cmd_gateway_restart()
        self.assertNotEqual(result,0)
        start.assert_not_called()

    def test_native_shell_finishes_once_after_restart_is_deferred(self):
        from pathlib import Path
        import shlex
        import sys
        import threading
        import time
        tools=importlib.import_module('zeline.tools')
        root=Path(self.temp.name);started=root/'started';release=root/'release';written=root/'completed'
        cancel=threading.Event();results=[]
        code=('from pathlib import Path; import time; '
              f'started=Path({str(started)!r}); release=Path({str(release)!r}); out=Path({str(written)!r}); '
              'started.touch()\nwhile not release.exists(): time.sleep(.01)\n'
              'with out.open("a") as f: f.write("completed\\n")\nprint("completed")')
        command=shlex.quote(sys.executable)+' -c '+shlex.quote(code)
        worker=threading.Thread(target=lambda:results.append(tools._run_shell(command,root,timeout=5,identity='fixture-restart',should_stop=cancel.is_set)),daemon=True)
        worker.start()
        try:
            until=time.monotonic()+3
            while not started.exists() and worker.is_alive() and time.monotonic()<until:
                cancel.wait(.01)
            self.assertTrue(started.exists())
            n=self.handler(False);n['runtime'].stop.side_effect=cancel.set
            n['drain_and_shutdown']()
            if cancel.is_set():
                worker.join(2)
            release.touch();worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertTrue(written.exists(),'Restart killed a real foreground task instead of deferring')
            self.assertEqual(written.read_text(),'completed\n')
            self.assertEqual(len(results),1)
            self.assertIn('exit=0',results[0])
        finally:
            cancel.set();release.touch();worker.join(3)

    def test_updater_refuses_to_replace_code_when_drain_is_deferred(self):
        updater=importlib.import_module('zeline.updater');service=self.service()
        with mock.patch.object(service,'status',return_value=(True,'running',{'only':['telegram']})),mock.patch.object(service,'drain_then_stop',return_value=(False,'Restart deferred; work continues.')),mock.patch.object(updater,'print'):
            with self.assertRaises(RuntimeError):
                updater._pause_gateway_for_update()

    def test_deferred_update_does_not_reach_installer_discovery(self):
        updater=importlib.import_module('zeline.updater')
        error=getattr(updater,'_UpdateDeferred',RuntimeError)
        with mock.patch.object(updater,'_pause_gateway_for_update',side_effect=error('Deferred')),mock.patch.object(updater,'_ui_plan'),mock.patch.object(updater,'_ui_result'),mock.patch.object(updater,'_checkout_root') as checkout,mock.patch.object(updater,'print'):
            result=updater.update()
        self.assertNotEqual(result,0)
        checkout.assert_not_called()

    def test_unsupported_drain_does_not_fall_back_to_destructive_stop(self):
        s=self.service()
        for windows,state in [(True,{'pid':12345,'drain':True}),(False,{'pid':12345,'drain':False})]:
            with self.subTest(windows=windows),mock.patch.object(s,'IS_WINDOWS',windows),mock.patch.object(s,'_load_state',return_value=state),mock.patch.object(s,'_process_matches_state',return_value=True),mock.patch.object(s,'stop',return_value=(True,'forced stop')) as stop:
                ok,_message=s.drain_then_stop(drain_timeout=1)
            self.assertFalse(ok)
            stop.assert_not_called()
