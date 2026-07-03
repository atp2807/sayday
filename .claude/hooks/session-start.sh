#!/bin/bash
# SessionStart hook: .linklore brief → additionalContext 자동 주입
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
WF="$PROJECT_DIR/.linklore"
[[ ! -d "$WF" ]] && exit 0

python3 - "$WF" <<'PYEOF'
import json, sys
from pathlib import Path

wf = Path(sys.argv[1])
settings = {}
sp = wf / "model" / "settings.json"
if sp.exists():
    try:
        settings = json.loads(sp.read_text())
    except Exception:
        pass

name = settings.get("name", "?")
# SQL source of truth (lr-3d75f68c: JSON mirror 폐기). head 활성 lore 수.
import sqlite3
db_path = wf / "system" / "linklore.db"
lore_count = 0
if db_path.exists():
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        lore_count = conn.execute("SELECT count(*) FROM lore WHERE head=1").fetchone()[0]
        conn.close()
    except Exception:
        pass

context = f"[LinkLore] {name} — lore {lore_count}개. brief()로 전체 확인."
print(json.dumps({"additionalContext": context}))
PYEOF
