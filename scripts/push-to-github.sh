#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# push-to-github.sh — One-time GitHub push setup
#
# Run this script to push OpenClaw to GitHub.
# You'll need a GitHub Personal Access Token (PAT):
#   1. Go to: https://github.com/settings/tokens/new
#   2. Note: "openclaw-local push"
#   3. Expiration: 90 days (or No expiration)
#   4. Scopes: check "repo"
#   5. Click "Generate token" and copy it
# ─────────────────────────────────────────────────────────────────
set -e

REPO_URL="https://github.com/resupaolo02/openclaw-local.git"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "========================================"
echo "  OpenClaw → GitHub Push Setup"
echo "========================================"
echo ""
echo "Repository: $REPO_URL"
echo "Local path: $REPO_DIR"
echo ""

cd "$REPO_DIR"

# Confirm remote is correct
git remote set-url origin "$REPO_URL"

echo "Enter your GitHub username:"
read -r GH_USER

echo "Enter your GitHub PAT (token, not password):"
read -rs GH_TOKEN
echo ""

# Store credentials temporarily
git config --global credential.helper store
echo "https://${GH_USER}:${GH_TOKEN}@github.com" >> ~/.git-credentials
chmod 600 ~/.git-credentials

echo "Pushing to GitHub..."
git push -u origin master 2>&1

echo ""
echo "✅ Done! Your repository is live at:"
echo "   https://github.com/resupaolo02/openclaw-local"
echo ""
echo "Note: To remove stored credentials later, run:"
echo "   rm ~/.git-credentials"
