#!/bin/bash

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOCKET="/tmp/orb-socket"
echo '{ "command": ["loadfile", "'"$DIR"'/animations/hover.gif"] }' | socat - "$SOCKET"
