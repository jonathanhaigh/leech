"""The l0 compiler package."""

from .main import run


def main() -> None:
    """Run the ``l0`` console-script entry point.

    Parses command-line arguments, compiles the given source file, and
    writes out the resulting LLVM IR (see :func:`l0.main.run`).
    """
    run()
