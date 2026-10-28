"""The wheel smoke test must import the installed package, not checkout files."""
from pathlib import Path


def test_package_ci_uses_isolated_interpreter_and_real_console_script():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/tests.yml').read_text()
    smoke = workflow.split('      - name: Smoke-install wheel in a fresh environment\n', 1)[1]
    smoke = smoke.split('\n  native-wheel:', 1)[0]
    assert 'ZELINE_HOME="$RUNNER_TEMP/zeline-wheel-smoke-state"' in smoke
    assert '"$RUNNER_TEMP/zeline-wheel-venv/bin/python" -I - <<\'PY\'' in smoke
    assert '"$RUNNER_TEMP/zeline-wheel-venv/bin/python" -m pip install dist/*.whl' in smoke
    # The quoted heredoc is *not* shell-expanded, so this literal cannot name a file.
    assert '["$RUNNER_TEMP/zeline-wheel-venv/bin/zeline"' not in smoke
    assert 'Path(sys.executable).with_name("zeline")' in smoke
