---
name: gitignore-does-not-untrack
description: .gitignore は新規ファイルにしか効かない — すでに tracked のファイルは git rm --cached で index から外す必要
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 05bbaeec-404f-44f3-b059-440260e4d04c
---

`.gitignore` にパターンを追記しても、過去に commit されて index に登録済みのファイルは tracked のまま無視されない。`git status` に M/D が出続けたら、`git rm -r --cached <path>` で index から外して commit する。

**Why:** git の仕様。.gitignore は「新規 untracked ファイルを追跡対象に上げない」フィルタであり、既に tracked のものには遡及しない。`venv/`, `build/`, `node_modules/` など、過去誤って add されたディレクトリで頻発。draw_piper では venv/ 配下 1343 ファイルがこの状態になっており、pip self-update のたびに大量の M/D が `git status` を汚染し、Teleport の `skipped-branch` 引き金にもなっていた (commit 230c9a2 で一掃)。

**How to apply:** `.gitignore` に追記後、`git status --short | head` で対象パターンのファイルが残っていたら:

```
git rm -r --cached <path>      # ディスクは残し、index から削除
git status --short              # 確認
git commit -m "chore: untrack <path>"
```

ディスクのファイルは残るので Python 環境などは壊れない。ただし大規模な削除 commit になるので意味のあるメッセージで単独 commit にしておく。
