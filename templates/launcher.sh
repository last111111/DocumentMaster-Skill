#!/bin/sh
# DocsManagement launcher: walk up from $PWD to the nearest project that has the engine, then run it.
d="$PWD"
while [ "$d" != "/" ]; do
  if [ -f "$d/DocsManagement/DocsManagement.py" ]; then
    exec python3 "$d/DocsManagement/DocsManagement.py" "$@"
  fi
  d=$(dirname "$d")
done
echo "DocsManagement: not inside a project (no DocsManagement/DocsManagement.py found upward from $PWD)" >&2
exit 2
