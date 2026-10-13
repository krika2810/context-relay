#!/usr/bin/env bash
# Builds "Context Relay.app" into <repo>/dist using swiftc (Command Line Tools only).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/dist/Context Relay.app"
ARCH="$(uname -m)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/engine"

# Universal binary: runs natively on Apple Silicon and Intel Macs.
BUILD="$(mktemp -d)"
for arch in arm64 x86_64; do
  swiftc -O -warnings-as-errors \
    -target "${arch}-apple-macos13.0" \
    -o "$BUILD/ContextRelay-$arch" \
    "$ROOT"/app/*.swift
done
lipo -create "$BUILD"/ContextRelay-arm64 "$BUILD"/ContextRelay-x86_64 -output "$APP/Contents/MacOS/ContextRelay"
rm -rf "$BUILD"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.contextrelay.app</string>
  <key>CFBundleName</key><string>Context Relay</string>
  <key>CFBundleDisplayName</key><string>Context Relay</string>
  <key>CFBundleExecutable</key><string>ContextRelay</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.2.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# App icon, drawn by scripts/make-icon.swift.
ICONWORK="$(mktemp -d)"
swiftc -O -target "${ARCH}-apple-macos13.0" -o "$ICONWORK/make-icon" "$ROOT/scripts/make-icon.swift"
"$ICONWORK/make-icon" "$ICONWORK/icon-1024.png"
mkdir -p "$ICONWORK/AppIcon.iconset"
for size in 16 32 128 256 512; do
  sips -z $size $size "$ICONWORK/icon-1024.png" --out "$ICONWORK/AppIcon.iconset/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z $double $double "$ICONWORK/icon-1024.png" --out "$ICONWORK/AppIcon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONWORK/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$ICONWORK"

# Bundle the Python engine (no bytecode caches, which would also break the signature).
ENGINE="$APP/Contents/Resources/engine"
/usr/bin/rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/context_relay" "$ENGINE/"

codesign --force --deep -s - "$APP" >/dev/null

# Distributable zip (ditto keeps the bundle's signature and permissions intact).
ditto -c -k --keepParent "$APP" "$ROOT/dist/ContextRelay.zip"

echo "$APP"
