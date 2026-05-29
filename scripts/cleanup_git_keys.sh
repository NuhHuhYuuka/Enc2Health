#!/usr/bin/env bash
set -euo pipefail
echo "This script shows commands to remove sensitive files from git history using git-filter-repo."
echo "Install git-filter-repo before running: https://github.com/newren/git-filter-repo"

SENSITIVE_PATHS=(
  "data/keys/"
)

echo "Dry-run: listing commits that touch sensitive paths"
for p in "${SENSITIVE_PATHS[@]}"; do
  git log --pretty=format:"%H %an %ad" -- "${p}" | sed -n '1,10p'
done

cat <<'EOF'
To permanently remove these files from the repository history, run:

  git filter-repo --invert-paths --paths data/keys/

Then force-push the cleaned branch to remote (careful: rewrites history):

  git push --force

Ensure you coordinate with your team before rewriting history.
EOF
