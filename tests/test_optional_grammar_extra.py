"""Optional CI grammar installation must resolve a declared pinned extra."""
from pathlib import Path
import re


def test_reverse_typescript_extra_matches_native_grammar_pin():
    source = (Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text(encoding='utf-8')
    optional = source.split('[project.optional-dependencies]', 1)[1].split('\n[', 1)[0]
    assert re.search(r'^reverse-ts\s*=\s*\["tree-sitter-typescript==0\.23\.2"\]', optional, re.M)
