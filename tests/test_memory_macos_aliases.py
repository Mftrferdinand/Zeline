"""Root-owned macOS filesystem aliases are not user-controlled memory links."""
from pathlib import Path
from types import SimpleNamespace
import stat
import sys
import os
import pytest
from zeline import memory_markdown as md


def simulate_var(monkeypatch, *, target='private/var', owner=0):
    if os.name == 'nt':
        pytest.skip('macOS aliases use POSIX paths, not Windows drive paths')
    original_stat, original_readlink = Path.lstat, Path.readlink
    def lstat(path, *args, **kwargs):
        if path == Path('/var'):
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=owner)
        return original_stat(path, *args, **kwargs)
    def readlink(path):
        return Path(target) if path == Path('/var') else original_readlink(path)
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(Path, 'readlink', readlink)


def test_standard_root_owned_macos_var_alias_is_allowed(monkeypatch):
    simulate_var(monkeypatch)
    path=Path('/var/zeline-nonexistent-fixture/MEMORY.md')
    assert md.secure(path) == path


@pytest.mark.parametrize('target,owner', [('private/other',0),('private/var',501)])
def test_macos_alias_with_wrong_target_or_owner_is_refused(monkeypatch,target,owner):
    simulate_var(monkeypatch,target=target,owner=owner)
    with pytest.raises(ValueError,match='Symlink memory component refused'):
        md.secure(Path('/var/zeline-nonexistent-fixture/MEMORY.md'))


def test_symlink_within_memory_directory_is_still_refused(tmp_path):
    target=tmp_path/'target';target.mkdir()
    link=tmp_path/'notes';link.symlink_to(target,target_is_directory=True)
    with pytest.raises(ValueError,match='Symlink memory component refused'):
        md.secure(link/'MEMORY.md')


def test_native_crlf_preserves_escaped_literal_carriage_returns():
    record=dict(id='fixture',revision=1,text='alpha\rvalue\nsecond\\rline',kind='fact',
                source='user',confidence=1.0,created_at=1,updated_at=1,expires_at=None,state='active')
    native=md.render([record],'MEMORY.md').replace('\n','\r\n')
    assert md.parse(native)==[record]
