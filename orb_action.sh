#!/usr/bin/env bash

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOCKET="/tmp/orb-socket"
echo '{ "command": ["loadfile", "'"$DIR"'/animations/output.gif"] }' | socat - "$SOCKET"
alacritty --class OracleOutput -e python3 "$DIR/client.py"
echo '{ "command": ["loadfile", "'"$DIR"'/animations/idle.gif"] }' | socat - "$SOCKET"
