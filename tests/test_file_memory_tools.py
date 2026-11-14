"""Exercise installed-style tool routing with real disposable Markdown and skills."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class FileMemoryToolTests(unittest.TestCase):
    def run_fixture(self, code):
        with tempfile.TemporaryDirectory(prefix="zeline-file-tools-") as home:
            env = {k: v for k, v in os.environ.items() if k in {
                "PATH", "PREFIX", "LD_LIBRARY_PATH", "LD_PRELOAD", "TMPDIR", "LANG"}}
            env.update(HOME=home, USERPROFILE=home, ZELINE_HOME=home + "/.zeline", PYTHONDONTWRITEBYTECODE="1")
            prefix = "import sys,socket,json\nfrom pathlib import Path\n"
            prefix += f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
            prefix += "socket.create_connection=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden'))\n"
            prefix += "from zeline.tools import ToolExecutor\nfrom zeline import config,skills\n"
            prefix += "ex=ToolExecutor('fixture:file-memory',profile='full',workspace=Path.home())\n"
            result = subprocess.run([sys.executable, "-B", "-c", prefix + code],
                                    cwd=home, env=env, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_prompt_exposes_canonical_targets_not_only_saved_memory(self):
        self.run_fixture("""
assert 'MEMORY.md' in config.SYSTEM_PROMPT_TEMPLATE
assert 'USER.md' in config.SYSTEM_PROMPT_TEMPLATE
assert 'list_memory with include_metadata' in config.SYSTEM_PROMPT_TEMPLATE
assert 'journals are not the source of active memory' in config.SYSTEM_PROMPT_TEMPLATE
""")

    def test_explicit_user_target_writes_user_md_and_reports_real_path(self):
        self.run_fixture("""
r=ex.run('add_memory',{'fact':'Operator prefers concise release receipts.','target':'user'})
assert not r.startswith('ERROR'), r
p=config.DATA_DIR/'memories'/ex.memory.key/'USER.md'
assert p.is_file(), r
assert 'Operator prefers concise release receipts.' in p.read_text()
assert p.relative_to(config.DATA_DIR).as_posix() in r, r
meta=json.loads(ex.run('list_memory',{'include_metadata':True}))
assert meta['storage']=='markdown', meta
assert meta['records'][0]['file_path'].endswith('/USER.md'), meta
""")

    def test_reflection_memory_receipt_points_to_verified_canonical_file(self):
        self.run_fixture("""
ex._reflection_mode=True
r=json.loads(ex.run('add_memory',{'fact':'Fixture project uses focused migration tests.'}))
assert r['state']=='created',r
assert r['file_path']==f'memories/{ex.memory.key}/MEMORY.md',r
assert (config.DATA_DIR/r['file_path']).is_file()
""")

    def test_legacy_explicit_file_metadata_reports_actual_containing_file(self):
        self.run_fixture("""
from zeline.memory import _coerce_record
record=_coerce_record({'id':'fixture-legacy','text':'Legacy placement fixture.','file_path':'USER.md'})
record['file_path']='USER.md'
ex.memory.path.parent.mkdir(parents=True,exist_ok=True)
ex.memory.path.write_text(json.dumps([record]))
meta=json.loads(ex.run('list_memory',{'include_metadata':True}))
assert meta['records'][0]['file_path'].endswith('/USER.md'), meta
""")

    def test_active_skill_receipt_names_real_private_target_not_proposal(self):
        self.run_fixture("""
p=skills.PRIVATE_SKILLS_DIR/'fixture-flow'/'SKILL.md';p.parent.mkdir(parents=True)
p.write_text('---\\nname: fixture-flow\\ndescription: Verify fixture flow\\n---\\n# Fixture\\n\\nRun checks.\\n')
ex._reflection_mode=True
r=json.loads(ex.run('manage_skill',{'action':'patch','name':'fixture-flow','old_text':'Run checks.','new_text':'Run focused checks.'}))
assert r['state']=='updated',r
assert r['target_file']=='skills/private/fixture-flow/SKILL.md',r
assert 'Run focused checks.' in (config.DATA_DIR/r['target_file']).read_text()
""")


if __name__ == '__main__':
    unittest.main()
