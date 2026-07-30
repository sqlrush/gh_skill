#!/usr/bin/env bash
# install-opencode.sh — install the opencode_skill skills into OpenCode.
#
# OpenCode discovers skills from ~/.config/opencode/skills/<name>/SKILL.md
# (global) or .opencode/skills/<name>/SKILL.md (per-project). OpenCode has no
# {baseDir} placeholder, so we substitute it with the real install path in the
# copied SKILL.md, leaving the source tree untouched.
#
# Usage:
#   ./install-opencode.sh                 # install all skills globally
#   ./install-opencode.sh --project DIR   # install into DIR/.opencode/skills
#   ./install-opencode.sh sqltune slowsql # install only the named skills
#   ./install-opencode.sh --dry-run       # show what would happen
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${HOME}/.config/opencode/skills"
DRY=0
ONLY=()

while [ $# -gt 0 ]; do
  case "$1" in
    --project) shift; DEST="${1:?--project needs a dir}/.opencode/skills" ;;
    --dest)    shift; DEST="${1:?--dest needs a dir}" ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    --*) echo "unknown option: $1" >&2; exit 2 ;;
    *) ONLY+=("$1") ;;
  esac
  shift
done

run() { if [ "$DRY" = 1 ]; then echo "  [dry-run] $*"; else eval "$@"; fi; }
want() { [ "${#ONLY[@]}" -eq 0 ] && return 0; for x in "${ONLY[@]}"; do [ "$x" = "$1" ] && return 0; done; return 1; }

# --- prerequisite check: python3 + required modules --------------------------
echo "• checking prerequisites"
command -v python3 >/dev/null || { echo "✗ python3 not found" >&2; exit 1; }
missing=""
for m in pg8000 cryptography yaml; do
  python3 -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('$m') else 1)" \
    || missing="$missing $m"
done
if [ -n "$missing" ]; then
  echo "! missing Python modules:$missing"
  echo "  install with: python3 -m pip install -r \"$SRC/requirements.txt\""
fi

# --- install each skill ------------------------------------------------------
echo "• installing skills into $DEST"
run "mkdir -p \"$DEST\""

# The shared connection layer travels with the skills. It has no SKILL.md, so
# OpenCode ignores it as a skill; the scripts locate it by walking up to here.
echo "  → common/ (shared connection layer)"
run "rm -rf \"$DEST/common\""
run "cp -R \"$SRC/common\" \"$DEST/common\""

count=0
for d in "$SRC"/skills/*/; do
  [ -f "${d}SKILL.md" ] || continue
  name="$(basename "$d")"
  want "$name" || continue
  target="$DEST/$name"
  ver="$(grep -E '^version:' "${d}SKILL.md" | head -1 | sed -E 's/^version:[[:space:]]*//')"
  echo "  → $name (v${ver:-?})"
  run "rm -rf \"$target\""
  run "cp -R \"$d\" \"$target\""
  # Substitute {baseDir} (this skill's install dir) and {kbDir} (the user
  # knowledge base, a sibling of skills/ so a reinstall's rm -rf cannot eat it).
  if [ "$DRY" = 0 ]; then
    python3 - "$target" <<'PY'
import pathlib, sys
base = pathlib.Path(sys.argv[1])          # <root>/skills/<name>
kbdir = base.parent.parent / "kb"         # <root>/kb  —— 与 skills/ 同级
skill = base / "SKILL.md"
skill.write_text(
    skill.read_text().replace("{baseDir}", str(base)).replace("{kbDir}", str(kbdir))
)
PY
  else
    echo "  [dry-run] substitute {baseDir} -> $target, {kbDir} -> <root>/kb in SKILL.md"
  fi
  count=$((count + 1))
done

echo "✓ installed $count skill(s)"
[ "$DRY" = 1 ] && echo "(dry-run: nothing was written)"
echo
echo "Next:"
echo "  1) ensure deps:  python3 -m pip install -r \"$SRC/requirements.txt\""
echo "  2) ensure a DB connection exists in ~/.gdaa (see docs/INSTALL-opencode.md)"
echo "  3) in opencode, the skills appear via the native 'skill' tool"
