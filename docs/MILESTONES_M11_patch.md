# MILESTONES.md への追記内容

このファイルの内容を、`MILESTONES.md` に手作業で 2 箇所反映してください。

## 1. trunk への ● 追加 + 現在地マーカ移動

`MILESTONES.md` の ASCII tree (M10 の行) を以下に変更:

```
変更前:
05-23 18:12   ● M10 drag-teach キャンバスキャリブ実機成功 ★★ 現在地 ★★  [221f0fb]

変更後:
05-23 18:12   ● M10 drag-teach キャンバスキャリブ実機成功                  [221f0fb]
              │
05-23 20:30   ● M11 Phase A キャリブ実装 (rotation 対応含む)  ★★ 現在地 ★★  [<未コミット>]
```

(ASCII tree のインデント・線記号は前後のフォーマットに合わせてください)

## 2. ポジティブ・マイルストーン詳細テーブルに M11 行を追加

```
| M11 | 2026-05-23 20:30 | Phase A キャリブ実装 (rotation 対応含む) — camera.py に rotation_deg、calibrate_panel.py 4 ステージ化、panel_crop.py 自動回転判定 | `<未コミット>` | `calibration/panel_frame.yaml` の `phase_a_calibration:` ブロックが書かれ、`camera_rotation_deg` フィールドを持つ。ドライランで `reproj_err mean<1e-12`。panel_crop.py 単体テスト (生画像/回転済み両方を warp に渡して結果一致) PASS |
```

## コミット手順 (次セッション冒頭、推奨)

```bash
cd ~/draw_piper

# 1. Phase A 関連だけ stage する
git add modules/camera.py modules/panel_crop.py scripts/calibrate_panel.py
git add calibration/panel_frame.yaml
git add MILESTONES.md
git add docs/20260523_2030_phase_a_calibration_rotation.md

# 2. canvas_calibration.yaml と test_vlm_to_image.py は触らない
#    (Claude Code 側 / 要確認のため別コミット予定)

git status

# 3. commit
git commit -m "M11: Phase A calibration with rotation handling

- camera.py: add rotation_deg ({0,90,180,270}), applied in _read_one(),
  retains backward-compat (default 0)
- calibrate_panel.py: 4-stage GUI (capture / rotation preview / 4-pt click
  / warp preview), keymap r=rotate / d=reset|redo / s=save / q=quit
- panel_crop.py: read camera_rotation_deg from yaml, auto-detect raw vs
  rotated input in warp() via shape comparison
- panel_frame.yaml: add camera_rotation_deg field to phase_a_calibration
  block, camera_image_size stored as post-rotation size
- dry-run with prior captured.png passed (reproj_err mean<1e-12)
"

# 4. ハッシュ確定後、MILESTONES.md と docs の <未コミット> を実ハッシュに置換
git log -1 --format="%h"
# 表示されたハッシュを使って sed:
HASH=$(git log -1 --format="%h")
sed -i "s/<未コミット>/${HASH}/g" MILESTONES.md docs/20260523_2030_phase_a_calibration_rotation.md
git diff MILESTONES.md | head -20

# 5. ハッシュ置換コミット (amend で 1 コミットにまとめる方が綺麗)
git add MILESTONES.md docs/20260523_2030_phase_a_calibration_rotation.md
git commit --amend --no-edit
git log -1 --format="%h %s"

# 6. push
git push
```
