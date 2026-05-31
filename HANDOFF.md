# HANDOFF — 2026-06-01 朝の再開用 (これだけ読めばOK)

夜間に 4 機能を実装・push 済み。 詳細設計は
`docs/20260601_scatter_and_transparent_board.md`。

## 🌙 夜間バッチ結果 (01:58 完了・push 済 ef8aec4)
全11サンプル × 3パイプライン = **55候補を生成、 失敗ゼロ・空候補ゼロ**。
- shift 11/11 ・ gacha 33/33 (×3 seed) ・ scatter 11/11
- webapp に `overnight_{shift,gacha,scatter}_20260601_010657` 列が追加済
- 確認: https://yushin-jizaie.github.io/draw_piper/selection/ (生成日 20260601 で絞り込み)
- 再実行したい時: `bash scripts/overnight_batch.sh` (ログ logs/overnight_batch_*.log)
- 朝やること: 候補を眺めて良い物を選定 → 実機描画へ

## 🔴 あなた(ユーザー)の判断待ち 4 件
1. **線の色**: 透明ボードのマーカー色は? (黒/青/赤/他)
   → 今は GUI で `dark/black/blue/red/green` 選択可、 既定 `dark`。 実色を教えてくれれば既定変更。
2. **near/far の線**: 手前(自分)/奥(相手) の線、 AI 入力は「両方」 でOK?
   → 単一カメラでは分離困難なため現状 **両方抽出**。 分離が要るなら要相談。
3. **背景リファレンス**: 空ボードを1枚撮って基準にする方式でOK?
   → GUI「背景キャプチャ」 ボタンで実装済。
4. **webapp アップロード時の push**: GUI から都度「push する/しない」 を選ぶ形にした。 これでOK?

## ✅ 実装済み (commit/push 済)
| 機能 | 実体 | テスト方法 |
|---|---|---|
| scatter (キャラを撒く) 本番化 | `modules/stroke_scatter.py`, `scripts/scatter_companions.py` | `webapp の scatter 列` / 下記 cmd |
| 透明ボード 線抽出 | `modules/line_extract.py` + GUI 統合 | `python modules/line_extract.py --smoke` (PASS済) / 実機カメラ |
| webapp アップロード | `scripts/upload_to_webapp.py` + GUI ボタン | GUI 実行 or CLI |
| ローカル webapp 起動 | `scripts/webapp_local` | `~/draw_piper/scripts/webapp_local` |

## 動作確認コマンド
```bash
# scatter 再生成 (GPU不要、 既存シート)
./venv/bin/python -m scripts.scatter_companions \
  --input sketch_variations/_inputs/scatter_input.png \
  --sheet assets/scatter_sheet_lineart_char.png \
  --output sketch_variations/disp_scatter_demo/scatter/v1_seed555 \
  --seed 555 --resolution 704x1472

# 線抽出 単体テスト
./venv/bin/python modules/line_extract.py --smoke

# GUI 起動 (カメラ + 線抽出 + アップロード)
./venv/bin/python scripts/pipeline_test_gui.py

# ローカル webapp
~/draw_piper/scripts/webapp_local   # → http://localhost:8765/docs/selection/index.html
```

## GUI の新 UI
- 入力ソース欄: 「透明ボード線抽出」 = 背景キャプチャ / 色選択 / 差分閾値 / 「線抽出→入力に設定」
- 結果ボタン行: 「⬆ webapp にアップロード」 (表示名入力 → local/push 選択)

## 実機で確認したいこと (明日)
- 実カメラで 線抽出 → 色/閾値の最適値を詰める (現状は合成テストのみ)
- アップロード → ローカル webapp で候補が見えるか
- scatter の散らし方 (cols/rows/fill) の好み調整

## オンライン webapp
https://yushin-jizaie.github.io/draw_piper/selection/  (scatter 列 = 一番下)
