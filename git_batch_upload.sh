#!/usr/bin/env bash
# git_batch_upload.sh
# -------------------
# Stages and commits files in batches of ≤100 to work around
# GitHub's 100-file-per-push display limit.
#
# Usage:
#   chmod +x git_batch_upload.sh
#   ./git_batch_upload.sh
#
# Optional env vars:
#   BATCH_SIZE=50     override files per commit (default: 95)
#   COMMIT_MSG="msg"  base commit message (default: "Add cover art batch N/T")
#   DRY_RUN=1         print commands without running them

set -euo pipefail

BATCH_SIZE="${BATCH_SIZE:-95}"
COMMIT_MSG_BASE="${COMMIT_MSG:-Add cover art batch}"
DRY_RUN="${DRY_RUN:-0}"

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY RUN] $*"
  else
    "$@"
  fi
}

# ── Collect all untracked / modified files ────────────────────────────────
echo "Scanning for uncommitted files..."

# New untracked files
mapfile -t UNTRACKED < <(git ls-files --others --exclude-standard)
# Modified tracked files
mapfile -t MODIFIED  < <(git ls-files --modified)

ALL_FILES=("${UNTRACKED[@]}" "${MODIFIED[@]}")
TOTAL=${#ALL_FILES[@]}

if [[ $TOTAL -eq 0 ]]; then
  echo "Nothing to commit. Working tree is clean."
  exit 0
fi

echo "Found $TOTAL file(s) to commit in batches of $BATCH_SIZE."
echo ""

# ── Calculate batch count ─────────────────────────────────────────────────
BATCHES=$(( (TOTAL + BATCH_SIZE - 1) / BATCH_SIZE ))

# ── Loop through batches ──────────────────────────────────────────────────
for (( b=0; b<BATCHES; b++ )); do
  START=$(( b * BATCH_SIZE ))
  END=$(( START + BATCH_SIZE ))
  if (( END > TOTAL )); then END=$TOTAL; fi

  SLICE=("${ALL_FILES[@]:$START:$((END - START))}")
  COUNT=${#SLICE[@]}
  LABEL="$((b+1))/$BATCHES"

  echo "── Batch $LABEL  ($COUNT files, #$((START+1))–$END) ──"

  # Stage the slice
  run git add -- "${SLICE[@]}"

  # Commit
  MSG="$COMMIT_MSG_BASE $LABEL ($COUNT files)"
  run git commit -m "$MSG"

  echo "   ✓ Committed: $MSG"
  echo ""
done

# ── Push everything ───────────────────────────────────────────────────────
echo "Pushing all commits..."
run git push

echo ""
echo "✓ Done. $BATCHES commit(s) pushed."
