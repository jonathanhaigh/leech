# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The Leech compiler package."""

from leech import driver


def main() -> None:
    """Run the compiler's console-script entry point."""
    driver.run()
