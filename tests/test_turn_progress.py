"""Guard boundaries and one real POSIX shell timeout reproduction."""
import os
import shutil
import pytest


@pytest.mark.parametrize('command', [
    'python -c "print(1)"', 'npm run build',
    'python -c "print(\'grep\')"', 'sleep 0.01',
])
def test_non_search_work_is_not_capped(command):
    from zeline.turn_progress import SlowSearchGuard
    guard = SlowSearchGuard()
    calls = []
    for _ in range(6):
        guard.run('run_shell', {'command': command}, lambda: calls.append(1) or 'exit=124\n')
    assert len(calls) == 6
    assert not guard.blocked_attempted


def test_successful_narrow_search_resets_its_own_failure_count():
    from zeline.turn_progress import SlowSearchGuard
    guard = SlowSearchGuard()
    args = {'command': 'cd ~ && timeout 45 grep -rl fixture .config'}
    for _ in range(2):
        guard.run('run_shell', args, lambda: 'exit=124\n')
    guard.run('run_shell', args, lambda: 'exit=0\nfixture.txt')
    for _ in range(2):
        guard.run('run_shell', args, lambda: 'ERROR: command timed out (>60 seconds).')
    assert guard.failures['grep'] == 2
    assert not guard.blocked_attempted


def test_no_match_is_not_treated_as_a_timeout():
    from zeline.turn_progress import SlowSearchGuard
    guard = SlowSearchGuard()
    for _ in range(5):
        assert guard.run('run_shell', {'command': 'grep fixture file'}, lambda: 'exit=1\n') == 'exit=1\n'
    assert not guard.failures


def test_switching_search_engine_is_allowed():
    from zeline.turn_progress import SlowSearchGuard
    guard = SlowSearchGuard()
    for _ in range(3):
        guard.run('run_shell', {'command':'grep fixture file'}, lambda:'exit=124\n')
    assert guard.run('run_shell', {'command':'rg fixture narrow'}, lambda:'exit=0\nfound') == 'exit=0\nfound'
    assert not guard.blocked_attempted


@pytest.mark.skipif(os.name == 'nt' or not shutil.which('timeout'), reason='requires POSIX FIFO and timeout')
def test_real_shell_timeout_is_blocked_before_fourth_spawn(tmp_path):
    from zeline.turn_progress import SlowSearchGuard
    from zeline.tools import _run_shell
    pipe = tmp_path/'waiting-input'
    os.mkfifo(pipe)
    fd = os.open(pipe, os.O_RDWR | os.O_NONBLOCK)
    guard, invoked = SlowSearchGuard(), []
    args = {'command': 'timeout 0.05 grep fixture waiting-input'}
    def run():
        invoked.append(True)
        return _run_shell(args['command'], tmp_path, timeout=5)
    try:
        results = [guard.run('run_shell', args, run) for _ in range(4)]
    finally:
        os.close(fd)
    assert len(invoked) == 3
    assert all(r.startswith('exit=124\n') for r in results[:3])
    assert 'not executed' in results[-1]
