#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
FORCE=""
if [[ "$1" == "--force" ]]; then
    FORCE="--force"
fi
python -m src stop $FORCE