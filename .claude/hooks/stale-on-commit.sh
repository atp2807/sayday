#!/bin/bash
# PostToolUse hook: git commit 후 관련 지식 자동 추천
# JSON additionalContext 출력 → Claude AI 컨텍스트에 반영
# 1. stale 페이지 (코드→스펙 매칭)
# 2. 관련 lore (커밋 파일과 매칭되는 함정/룰)

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
# tool_result 또는 tool_response 양쪽 모두 대응
STDOUT=$(echo "$INPUT" | jq -r '(.tool_result.stdout // .tool_response.stdout // empty)')
STDERR=$(echo "$INPUT" | jq -r '(.tool_result.stderr // .tool_response.stderr // empty)')

echo "$COMMAND" | grep -qE '\bgit\s+commit\b' || exit 0
# git commit 성공 감지: stdout 또는 stderr에서 [branch hash] 패턴
echo "$STDOUT$STDERR" | grep -qE '\[.+\]' || exit 0

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
WF="$PROJECT_DIR/.linklore"
[[ ! -d "$WF" ]] && { WF="$PROJECT_DIR/.wireflow"; [[ ! -d "$WF" ]] && exit 0; }

python3 - "$WF" "$PROJECT_DIR" <<'PYEOF'
import json, subprocess, sys
from pathlib import Path

wf = Path(sys.argv[1])
project_dir = sys.argv[2]
hints = []

# 커밋된 파일 수집
out = subprocess.run(
    ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
    capture_output=True, text=True, cwd=project_dir,
)
all_files = [f for f in out.stdout.strip().split("\n") if f]
files = [f for f in all_files if not f.startswith(".linklore/") and not f.startswith(".wireflow/")]

# SQL source of truth (lr-3d75f68c: JSON mirror 폐기). 옛 works.json/lore.json 대체.
import sqlite3
db_path = wf / "system" / "linklore.db"

# 파일↔항목 매칭 정본 (core.base.filematch — write 시점 본문 주입)
def match_file(query: str, candidate: str) -> bool:
    """파일 경로 query 와 등록 경로 candidate 의 관련 여부 (관대한 합집합).

    매칭: 정확 일치, 디렉토리 prefix ("web/" vs "web/app.js"),
    basename suffix ("app.js" vs "web/app.js"), 양방향 substring — 모두 대소문자 무시.
    빈 문자열은 항상 불일치.
    """
    if not query or not candidate:
        return False
    q = query.lower()
    c = candidate.lower()
    return q in c or c in q


def _match(fps, cf):
    return any(match_file(cf, fp) for fp in fps)

if files and db_path.exists():
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    # --- 1. stale doc (코드→doc 파일 매칭) ---
    affected = {}
    for did, title, files_json in conn.execute("SELECT id, title, files FROM docs"):
        try:
            fps = json.loads(files_json or "[]")
        except Exception:
            fps = []
        if any(_match(fps, cf) for cf in files):
            affected[did] = title or did
    if affected:
        names = ", ".join(list(affected.values())[:5])
        extra = f" 외 {len(affected)-5}건" if len(affected) > 5 else ""
        hints.append(f"[stale] doc 업데이트 추천: {names}{extra}")
        hints.append("  → show(type='doc')로 확인 후 edit로 반영")

    # --- 2. 관련 lore (파일 매칭 + head 활성) ---
    matched_lore = []
    for lid, title, body, files_json, status in conn.execute(
        "SELECT id, title, body, files, status FROM lore WHERE head=1"
    ):
        if status in ("dropped", "done"):
            continue
        try:
            fps = json.loads(files_json or "[]")
        except Exception:
            fps = []
        if any(_match(fps, cf) for cf in files):
            matched_lore.append((lid, title, body))
    if matched_lore:
        hints.append(f"[lore] 관련 함정/룰 {len(matched_lore)}건:")
        for lid, title, body in matched_lore[:5]:
            body1 = (body or "").strip().split("\n")[0][:80]
            body_str = f" — {body1}" if body1 else ""
            hints.append(f"  - {title or '?'} [{lid}]{body_str}")
        if len(matched_lore) > 5:
            hints.append(f"  (+{len(matched_lore)-5}건 더)")

    conn.close()

# --- JSON 출력 (additionalContext → Claude AI 컨텍스트) ---
if hints:
    lines = ["─" * 50] + hints + ["─" * 50]
else:
    lines = [
        "─" * 50,
        "[lore-nudge] 함정/삽질/깨달음이 있었다면 add로 기록하세요.",
        "─" * 50,
    ]

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "\n".join(lines),
    }
}))
PYEOF
exit 0
