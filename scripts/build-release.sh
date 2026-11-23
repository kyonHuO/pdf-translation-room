#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

name="PDF翻訳室 Mark.Ⅱ"
release="PDF-Translation-Room-Mark-II-macOS.zip"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/pdf-room-release.XXXXXXXX")"
trap 'rm -rf "$scratch"' EXIT
package="$scratch/package"
bundle="$package/$name.app"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources" dist

cp app/Info.plist "$bundle/Contents/Info.plist"
cp -p app/Resources/* "$bundle/Contents/Resources/"
cp 最初にお読みください.txt "$package/"

for architecture in arm64 x86_64; do
    xcrun swiftc -warnings-as-errors -parse-as-library -target "$architecture-apple-macosx14.0" \
        src/main.swift -o "$scratch/launch-$architecture"
done
xcrun lipo -create "$scratch/launch-arm64" "$scratch/launch-x86_64" \
    -output "$bundle/Contents/MacOS/launch"
chmod 755 "$bundle/Contents/MacOS/launch"
codesign --force --sign - "$bundle"
codesign --verify --strict "$bundle"
ditto -c -k --norsrc "$package" "$scratch/$release"
mv -f "$scratch/$release" "dist/$release"
printf '%s\n' "dist/$release"
