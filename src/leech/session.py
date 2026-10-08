# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The state of one compiler invocation."""

import dataclasses

from leech import diag


@dataclasses.dataclass
class Session:
    """One invocation of the compiler: where diagnostics go, and options that affect output.

    Every compilation in a session reports to the session's diagnostics.
    """

    diags: diag.Diags = dataclasses.field(default_factory=diag.Diags)
    opt_level: int = 0
