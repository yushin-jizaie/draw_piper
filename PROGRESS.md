
---

## 2026-05-22 22:50 — Step C 単体 VLM 計測完了 (manual / parallel)

Claude Code が Step E 中に、別ターミナルで Qwen2.5-VL-7B (NF4) の VRAM 計測を実施。

### 結果サマリー

| 項目 | 値 |
|---|---|
| ロード後 VRAM | 5.51 GB |
| 推論ピーク VRAM | 5.98 GB |
| 推論速度 | 23.8 tok/s (256 tok / 6.40s) |
| モデルロード時間 | 166.2s (初回DL込み) |

設計 v0.4 の事前予想 (6〜8GB) より軽量。SDXL Turbo 共存の余地が広がった。

### 追加された資産

- `requirements-vlm.txt` (新規; `requirements.txt` とは分離)
- `scripts/measure_vlm_vram.py` (新規)
- `scripts/test_sketch.jpg` (ダミー入力)
- `logs/vlm_vram_20260522_*.log` (計測ログ)

### Step C の進行ステータス

- ✅ VLM 単体 VRAM 計測
- 🔲 SDXL Turbo 単体 VRAM 計測
- 🔲 VLM + SDXL Turbo 同居計測

詳細は `docs/20260522_2250_vlm_vram_measurement.md` (プロジェクトナレッジ) を参照。
