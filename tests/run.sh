#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$script_dir/.."

# Pick an interpreter that actually runs: on Windows, a `python3` shim may
# exist only as the Microsoft Store alias stub, which fails on execution.
if python3 -c 'pass' >/dev/null 2>&1; then
  python_cmd=python3
elif python -c 'pass' >/dev/null 2>&1; then
  python_cmd=python
else
  echo "error: no working python3/python interpreter found" >&2
  exit 1
fi

exec "$python_cmd" -m unittest discover -s tests -v
