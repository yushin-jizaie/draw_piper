---
name: claude-code-teleport-skipped-branch
description: Teleport で teleport-skipped-branch を踏んだ時の復旧手順 — working tree を clean にして別ターミナルで再実行
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 05bbaeec-404f-44f3-b059-440260e4d04c
---

VSCode 拡張から Teleport を起動して `teleport-skipped-branch` を踏んだら、別ターミナルで `git stash -u -m "pre-teleport"` → `claude --teleport <remote-session-id>` を実行する。

**Why:** working tree が dirty だと Teleport は履歴コピーを skip して空セッション (messageCount: 0) を作り、VSCode 拡張が resume できず `No conversation found with session ID` エラーになる。`--force` フラグは公式 docs 未記載で存在しない。唯一の正攻法は working tree を clean にしてからの再実行。

**How to apply:** 以下のいずれかが該当したら同じ手順を再適用:
- VSCode 拡張ログに `[ERROR] No conversation found with session ID: <id>`
- `~/.claude/projects/<proj>/<id>.jsonl` が 200byte 前後 + 中身が `teleport-skipped-branch` メタ 2 行のみ
- ローカルに同名 branch があり、かつ working tree に大量の M/D が残っている

リモートセッションの履歴は `https://claude.ai/code/<remote-session-id>` でブラウザ目視も可能 (Teleport 失敗時のバックアップ参照経路)。関連: [[gitignore-does-not-untrack]] — Teleport の `skipped-branch` 引き金になりやすい noise の主因。
