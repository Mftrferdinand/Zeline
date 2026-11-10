"""Run stateful reliability contracts in disposable child interpreters.

Socket guards, configuration reloads and module-cache changes must never leak
into unrelated tests collected by pytest/unittest in the same process.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ResponseReliabilityTests(unittest.TestCase):
    def run_case_module(self, name):
        with tempfile.TemporaryDirectory(prefix='zeline-reliability-') as home:
            allowed={'PATH','LD_PRELOAD','LD_LIBRARY_PATH','PREFIX','SYSTEMROOT',
                     'WINDIR','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','PYTHONUTF8'}
            env={key:value for key,value in os.environ.items() if key in allowed}
            env.update(HOME=home,USERPROFILE=home,ZELINE_HOME=str(Path(home)/'state'),
                       PYTHONPATH=os.pathsep.join((str(ROOT),str(ROOT/'tests'))),PYTHONDONTWRITEBYTECODE='1')
            result=subprocess.run([sys.executable,'-B','-m','unittest',
                                   'reliability_cases.'+name],
                                  cwd=home,env=env,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_memory_lock_contracts(self):self.run_case_module('memory_lock_cases')
    def test_completion_contracts(self):self.run_case_module('completion_cases')
    def test_real_file_tool_contracts(self):self.run_case_module('execution_cases')
    def test_telegram_delivery_contracts(self):self.run_case_module('telegram_cases')
    def test_foreground_priority_contracts(self):self.run_case_module('foreground_cases')
    def test_nonblocking_gateway_contracts(self):self.run_case_module('gateway_cases')
    def test_selective_recall_contracts(self):self.run_case_module('recall_cases')

if __name__=='__main__':unittest.main()
