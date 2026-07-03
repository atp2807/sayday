#!/bin/bash
# PreToolUse hook (Edit|Write): 파일 관련 lore 자동 surfacing
# 차단 X — additionalContext 만 주입. SQL source of truth (lr-3d75f68c, lr-4d4e6b20).
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[[ -z "$FILE_PATH" ]] && exit 0

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
DB="$PROJECT_DIR/.linklore/system/linklore.db"
[[ ! -f "$DB" ]] && exit 0

python3 - "$DB" "$PROJECT_DIR" "$FILE_PATH" <<'PYEOF'
import sqlite3, json, sys, os

db, project_dir, file_path = sys.argv[1], sys.argv[2], sys.argv[3]
# 절대경로 → 프로젝트 상대경로 정규화 (lore.files 는 상대경로 저장)
try:
    rel = os.path.relpath(file_path, project_dir)
except ValueError:
    rel = file_path
cands = {rel, file_path}

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


def _match(fps):
    return any(match_file(cf, fp) for fp in fps for cf in cands)

hits = []
try:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for lid, title, body, fj in conn.execute(
        "SELECT id, title, body, files FROM lore "
        "WHERE head=1 AND status NOT IN ('dropped','done') "
        "ORDER BY level DESC, updated_at DESC"
    ):
        try:
            fps = json.loads(fj or "[]")
        except Exception:
            fps = []
        if _match(fps):
            hits.append((lid, title, body))
    conn.close()
except Exception:
    sys.exit(0)

if hits:
    lines = [f"⚠️ 이 파일 관련 결정/함정 {len(hits)}건 (LinkLore — 변경 전 확인):"]
    for lid, title, body in hits[:5]:
        b = (body or "").strip().split("\n")[0][:90]
        lines.append(f"  - {title} [{lid}]" + (f" — {b}" if b else ""))
    if len(hits) > 5:
        lines.append(f"  (+{len(hits)-5}건 더 — show(file=) 로 전체)")
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "\n".join(lines),
        }
    }))
PYEOF
exit 0
