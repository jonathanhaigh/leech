# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The Leech compiler package."""

from leech import driver


def main() -> None:
    """Run the ``leech`` console-script entry point.

    Parses command-line arguments, compiles the given source file, and
    writes out the resulting LLVM IR (see :func:`leech.driver.run`).
    """
    driver.run()
