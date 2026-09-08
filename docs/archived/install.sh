#!/usr/bin/env bash
# install.sh — fetch latest PTY-Agent release and install as a Skill
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ming-14/PTY-Agent/main/install.sh | bash
# Env:
#   PTY_AGENT_MIRROR  URL prefix for downloads
#   HTTPS_PROXY       curl proxy
# Opt:
#   --project         install to current project (default: global -g)

set -euo pipefail

PROJECT=0
if [[ "${1:-}" == "--project" ]]; then
    PROJECT=1
fi

case "$(uname -s)" in
    Linux*)   ASSET="pty-agent-linux_x86-64.zip" ;;
    MINGW*|MSYS*|CYGWIN*) ASSET="pty-agent-win_x86-64.zip" ;;
    *)        echo "Unsupported platform: $(uname -s)" >&2; exit 1 ;;
esac

RELEASE_URL="https://github.com/ming-14/PTY-Agent/releases/latest/download/$ASSET"
if [[ -n "${PTY_AGENT_MIRROR:-}" ]]; then
    RELEASE_URL="${PTY_AGENT_MIRROR%/}/$RELEASE_URL"
fi
echo "[1/4] Downloading: $RELEASE_URL"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
ZIP_PATH="$TMP_DIR/$ASSET"
curl -fL --retry 3 -o "$ZIP_PATH" "$RELEASE_URL"
echo "[2/4] Downloaded: $(du -h "$ZIP_PATH" | cut -f1)"

echo "[3/4] Extracting..."
EXTRACT_DIR="$TMP_DIR/extract"
mkdir -p "$EXTRACT_DIR"
if command -v unzip >/dev/null 2>&1; then
    unzip -q "$ZIP_PATH" -d "$EXTRACT_DIR"
else
    python3 -m zipfile -e "$ZIP_PATH" "$EXTRACT_DIR"
fi

SKILL_DIR="$(find "$EXTRACT_DIR" -name SKILL.md -type f -print -quit | xargs -r dirname)"
if [[ -z "$SKILL_DIR" ]]; then
    echo "SKILL.md not found in archive" >&2
    exit 1
fi

echo "[4/4] npx skills add: $SKILL_DIR"
if [[ "$PROJECT" -eq 1 ]]; then
    npx --yes skills add "$SKILL_DIR" -y
else
    npx --yes skills add "$SKILL_DIR" -y -g
fi
echo "PTY-Agent Skill installed"
