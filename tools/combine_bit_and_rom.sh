#!/usr/bin/env sh

set -euo pipefail

BITFILE="$1"
ROMFILE="$2"
RESULT="$3"
ROMADDR="524288"
BITSIZE="$(stat -c '%s' "$BITFILE")"
PADDING="$((ROMADDR - BITSIZE))"
PADDINGFILE="$(mktemp)"
head -c "$PADDING" /dev/zero | tr '\0' '\377' > "$PADDINGFILE"
cat "$BITFILE" "$PADDINGFILE" "$ROMFILE" > "$RESULT"
rm "$PADDINGFILE"
