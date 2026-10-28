"""Reject wheel Python code that differs from the reviewed candidate checkout."""

from pathlib import Path
from zipfile import ZipFile


def verify_python_sources(root: Path, wheel: Path) -> int:
    """Compare Python module paths and exact bytes, not only package metadata."""
    package = root / "zeline"
    if not package.is_dir():
        raise ValueError("missing zeline source package")
    source = {path.relative_to(root).as_posix(): path
              for path in package.rglob("*.py") if path.is_file()}
    module_count = len(source)
    soul = package / "SOUL.md"
    if soul.is_file():
        source["zeline/SOUL.md"] = soul
    with ZipFile(wheel) as archive:
        members = [name for name in archive.namelist()
                   if name.startswith("zeline/") and
                   (name.endswith(".py") or name == "zeline/SOUL.md")]
        if len(members) != len(set(members)):
            raise ValueError("duplicate packaged source in wheel")
        wheel_files = set(members)
        missing = sorted(source.keys() - wheel_files)
        if missing:
            raise ValueError("missing packaged source in wheel: " + ", ".join(missing[:5]))
        extra = sorted(wheel_files - source.keys())
        if extra:
            raise ValueError("extra packaged source in wheel: " + ", ".join(extra[:5]))
        for name, path in sorted(source.items()):
            if path.read_bytes() != archive.read(name):
                raise ValueError("changed packaged source in wheel: " + name)
    return module_count


def main() -> None:
    wheel, = Path("dist").glob("*.whl")
    count = verify_python_sources(Path.cwd(), wheel)
    print(f"wheel Python source parity passed: {count} modules")


if __name__ == "__main__":
    main()
