---
name: git-stash-message-is-not-content
description: "git stash の \"WIP on <branch>: <HEAD-subject>\" メッセージは作成時の branch HEAD subject が反映されただけで、stash 中身を表さない"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 05bbaeec-404f-44f3-b059-440260e4d04c
---

stash の drop/pop 前は必ず `git stash show stash@{N} --name-only` で実体を確認する。 メッセージだけで「本物の作業 WIP か / ただの noise か」を判定しない。

**Why:** `git stash` (引数なし) はデフォルトで `WIP on <branch>: <HEAD-commit-subject>` というメッセージを自動生成する。 これは stash 作成時点の branch tip コミットの subject が反映されているだけで、stash の中身 (= working tree + index の差分) とは無関係。 draw_piper で「calibration: canvas を 2026-05-26 19:25 セッションの B5 含む版に復旧」というメッセージの stash を「本物の作業 WIP」と早合点して保全しようとしたが、実体は venv noise 1044 ファイルだけで、 本物の calibration 作業は既に commit `6d60b2a` として branch に入っていた。 メッセージに引っ張られて drop 判断を誤りかけた。

**How to apply:** stash 整理 (drop / 部分 apply / 仕分け) では順序を必ず守る:

```
git stash show stash@{N} --name-only          # 全ファイル一覧
git stash show stash@{N} --name-only | grep -v "^venv/"  # noise を除外して非ノイズが残るか
git stash show stash@{N} --stat               # 統計
git stash show stash@{N} -p | head -200       # patch サマリ (詳細確認時)
```

非ノイズ 0 件で stat が venv pyc などのバイナリ群なら drop して OK。 関連: [[gitignore-does-not-untrack]] (venv noise が stash に紛れ込む根本原因)、 [[claude-code-teleport-skipped-branch]] (このトラブルが発生した文脈)。

復元手段の備忘: 誤って drop しても、`git fsck --unreachable | grep commit` で stash の commit object hash を発見 → `git stash store -m "<msg>" <hash>` で stash list に復元できる (gc が走るまでの間)。
