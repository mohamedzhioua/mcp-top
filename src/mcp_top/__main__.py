"""Run mcp-top with ``python -m mcp_top``."""

import sys

from mcp_top._version_gate import MESSAGE, check_python_version


if not check_python_version():
    print(MESSAGE, file=sys.stderr)
    raise SystemExit(2)

from mcp_top.cli import main


raise SystemExit(main())
