#!/bin/bash
# PreToolUse hook: dirty tree에서 destructive git 명령 차단
# 비정상 종료(exit 2) → Claude에게 tool call 차단 + 메시지 전달

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# git 명령이 아니면 통과
echo "$COMMAND" | grep -qE '\bgit\b' || exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd')
cd "$CWD" 2>/dev/null || exit 0

# git repo가 아니면 통과
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# destructive 패턴 감지
IS_DESTRUCTIVE=false

# git checkout (브랜치 전환/파일 복원) — 단, -b (브랜치 생성)는 허용
echo "$COMMAND" | grep -qE '\bgit\s+checkout\b' && ! echo "$COMMAND" | grep -qE '\bgit\s+checkout\s+-b\b' && IS_DESTRUCTIVE=true

# git switch (브랜치 전환)
echo "$COMMAND" | grep -qE '\bgit\s+switch\b' && IS_DESTRUCTIVE=true

# git reset --hard
echo "$COMMAND" | grep -qE '\bgit\s+reset\s+--hard\b' && IS_DESTRUCTIVE=true

# git restore — 전체 복원(. 또는 인자 없음)만 차단, 단일 파일 허용
echo "$COMMAND" | grep -qE '\bgit\s+restore\s*$' && IS_DESTRUCTIVE=true
echo "$COMMAND" | grep -qE '\bgit\s+restore\s+\.$' && IS_DESTRUCTIVE=true

# git clean
echo "$COMMAND" | grep -qE '\bgit\s+clean\b' && IS_DESTRUCTIVE=true

# git stash drop/clear
echo "$COMMAND" | grep -qE '\bgit\s+stash\s+(drop|clear)\b' && IS_DESTRUCTIVE=true

# destructive가 아니면 통과
[ "$IS_DESTRUCTIVE" = false ] && exit 0

# dirty tree 확인 (staged + unstaged만, untracked 제외)
DIRTY=$(git diff --name-only 2>/dev/null; git diff --cached --name-only 2>/dev/null)

if [ -n "$DIRTY" ]; then
  CHANGED_COUNT=$(echo "$DIRTY" | wc -l | tr -d ' ')
  cat <<EOF
[git-guard] BLOCKED: uncommitted 변경 ${CHANGED_COUNT}개가 있는 상태에서 destructive git 명령 감지.

차단된 명령: $COMMAND

변경 파일:
$(echo "$DIRTY" | head -20)

반드시 먼저:
  1. git stash  (임시 저장)
  2. git add + git commit  (커밋)
  3. 또는 사용자에게 확인 요청

절대 uncommitted 작업물을 날리지 마세요.
EOF
  exit 2
fi

exit 0
