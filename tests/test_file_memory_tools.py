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

    def test_batch_json_preserves_report_and_verified_canonical_paths(self):
        self.run_fixture("""
r=json.loads(ex.run('add_memory',{'operations':[
    {'action':'add','text':'Fixture deployment uses PostgreSQL.'},
    {'action':'add','text':'Operator prefers concise fixtures.','kind':'user_profile'}]}))
assert r['success'] is True and len(r['changed'])==2, r
assert r['total']==2 and r['consolidated']==[], r
assert r['file_paths']==sorted(f'memories/{ex.memory.key}/{f}' for f in ['MEMORY.md','USER.md']), r
for path in r['file_paths']:
    assert (config.DATA_DIR/path).is_file(), r
assert 'Fixture deployment uses PostgreSQL.' not in json.dumps(r), r
assert 'Operator prefers concise fixtures.' not in json.dumps(r), r
""")

    def test_batch_journal_failure_keeps_json_and_committed_receipt(self):
        self.run_fixture("""
import zeline.reflection_markdown as markdown
original_record_markdown=markdown.record_markdown
def fail_record_markdown(*args,**kwargs):
    raise OSError('fixture journal unavailable')
markdown.record_markdown=fail_record_markdown
try:
    r=json.loads(ex.run('add_memory',{'operations':[{'action':'add','text':'Fixture memory survives journal failure.'}]}))
finally:
    markdown.record_markdown=original_record_markdown
assert r['success'] is True and len(r['changed'])==1, r
assert r['warning']=='journal unavailable; canonical memory saved', r
assert r['file_paths']==[f'memories/{ex.memory.key}/MEMORY.md'], r
assert 'Fixture memory survives journal failure.' in (config.DATA_DIR/r['file_paths'][0]).read_text()
""")

    def test_batch_revision_checks_noops_and_deletes_preserve_json_contract(self):
        self.run_fixture("""
r=ex.run('add_memory',{'fact':'Fixture database is MySQL.'})
assert r.startswith('OK, saved.') and ' File: ' in r, r
record=json.loads(ex.run('list_memory',{'include_metadata':True}))['records'][0]
op={'action':'replace','id':record['id'],'expected_revision':record['revision'],'text':'Fixture database is PostgreSQL.'}
r=json.loads(ex.run('add_memory',{'operations':[op]}))
assert r['success'] is True and r['changed']==[record['id']], r
assert r['file_paths']==[record['file_path']], r
stale=json.loads(ex.run('add_memory',{'operations':[op]}))
assert stale['success'] is False and not stale.get('file_paths'), stale
current=json.loads(ex.run('list_memory',{'include_metadata':True}))['records'][0]
assert current['revision']>record['revision'] and current['text']==op['text'], current
noop=json.loads(ex.run('add_memory',{'operations':[{'action':'add','text':op['text']}]}))
assert noop['success'] is True and not noop['changed'] and not noop.get('file_paths'), noop
other=ToolExecutor('fixture:other-memory',profile='full',workspace=Path.home())
assert not json.loads(other.run('list_memory',{'include_metadata':True}))['records']
deleted=json.loads(ex.run('add_memory',{'operations':[{'action':'remove','id':current['id'],'expected_revision':current['revision']}]}))
assert deleted['success'] is True and deleted['changed']==[current['id']], deleted
assert not deleted.get('file_paths'), deleted
assert not json.loads(ex.run('list_memory',{'include_metadata':True}))['records']
assert ex.run('add_memory',{'fact':'invalid mixed input','operations':[]}).startswith('ERROR: use fact or operations')
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
