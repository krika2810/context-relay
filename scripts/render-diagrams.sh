#!/usr/bin/env bash
# Renders docs/diagrams/*.mmd (Mermaid) to PNGs used by the README.
# Needs Node.js. Uses mermaid-cli via npx (or $MMDC), and an installed Chrome if found.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MMDC="${MMDC:-npx -y @mermaid-js/mermaid-cli}"
CONFIG="$(mktemp)"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ -x "$CHROME" ]; then
  printf '{"executablePath": "%s"}\n' "$CHROME" > "$CONFIG"
  export PUPPETEER_SKIP_DOWNLOAD=1
else
  echo '{}' > "$CONFIG"
fi

for src in "$ROOT"/docs/diagrams/*.mmd; do
  out="${src%.mmd}.png"
  $MMDC -q -p "$CONFIG" -i "$src" -o "$out" -b white -s 2
  echo "rendered ${out#$ROOT/}"
done
rm -f "$CONFIG"
