"""Compatibility entrypoint for the canonical Zeline AgentID Watcher helper."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    canonical = Path(__file__).resolve().parents[2] / 'zeline-agentid-watcher' / 'scripts' / 'zeline_agentid.py'
    runpy.run_path(str(canonical), run_name='__main__')
