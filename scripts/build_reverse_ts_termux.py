"""Build the pinned optional TS/TSX grammar wheel on Termux without installing it.

The upstream 0.23.2 sdist omits headers needed by Android source builds.
All fetched artifacts are SHA256-pinned and extracted into a temporary directory.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import urllib.request

VERSION = '0.23.2'
SDIST_URL = ('https://files.pythonhosted.org/packages/1e/fc/'
             'bb52958f7e399250aee093751e9373a6311cadbe76b6e0d109b853757f35/'
             'tree_sitter_typescript-0.23.2.tar.gz')
SDIST_SHA256 = '7b167b5827c882261cb7a50dfa0fb567975f9b315e87ed87ad0a0a3aedb3834d'
HEADER_BASE = 'https://raw.githubusercontent.com/tree-sitter/tree-sitter-typescript/v0.23.2/'
HEADERS = {
    'common/scanner.h': 'da66ef2bd14a3f7ea743e25ba068c6c9aae2c3509db200ff80c4a0e6116e564c',
    'typescript/src/tree_sitter/parser.h': 'a1f6ef161fbaf48a0e10fca90ef5290a062462b307b3898aa562993853b9f80a',
    'tsx/src/tree_sitter/parser.h': 'a1f6ef161fbaf48a0e10fca90ef5290a062462b307b3898aa562993853b9f80a',
}


def _download(url: str, digest: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('pinned source exceeds the download limit')
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('SHA256 mismatch for pinned source')
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True,
                        help='Directory for the built wheel; does not install it')
    args = parser.parse_args()
    with TemporaryDirectory(prefix='zeline-ts-') as directory:
        temp = Path(directory)
        archive = temp / 'grammar.tar.gz'
        archive.write_bytes(_download(SDIST_URL, SDIST_SHA256))
        with tarfile.open(archive) as bundle:
            # No unverified tar path is extracted: sdist bytes are SHA256-pinned.
            bundle.extractall(temp, filter='data')
        source = temp / f'tree_sitter_typescript-{VERSION}'
        if not (source / 'pyproject.toml').is_file():
            raise ValueError('pinned sdist structure changed')
        for relative, digest in HEADERS.items():
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_download(HEADER_BASE + relative, digest))
        output = args.output_dir.expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps',
                        '--no-build-isolation', '--no-cache-dir', '--wheel-dir', str(output),
                        str(source)], check=True, timeout=180)
    print(f'Built pinned reverse-ts wheel in {output}; no Zeline install or restart performed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
