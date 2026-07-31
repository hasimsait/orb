#!/usr/bin/env bash

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOCKET="/tmp/orb-socket"
VENV_PYTHON="$DIR/venv/bin/python3"
echo '{ "command": ["loadfile", "'"$DIR"'/animations/output.gif"] }' | socat - "$SOCKET"
alacritty --class OracleOutput -e "$VENV_PYTHON" "$DIR/client.py"
echo '{ "command": ["loadfile", "'"$DIR"'/animations/idle.gif"] }' | socat - "$SOCKET"
