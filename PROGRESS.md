
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

---

## 2026-05-23 18:12 — 壁面描画 drag-teach キャンバスキャリブ実機成功 (M10)

手でアームを動かして 31 点記録 → 平面フィット。`calibration/canvas_calibration.yaml` が確定。

### 結果サマリー

| 項目 | 値 |
|---|---|
| 記録点数 | 31 |
| 平面 RMS 残差 | 3.82 mm |
| centroid (base_link 座標) | X=204.3, Y=-2.8, Z=299.7 mm |
| 法線 | [-0.998, +0.052, -0.036] (≈ -X 方向、+X を向いた垂直壁) |
| Y 範囲 | [-97, +77] mm |
| Z 範囲 | [+157, +464] mm |

### 道中(N4)

master mode 周りに 3 つの罠を発見:
1. `MasterSlaveConfig(0xFA)` 中、SDK の `bus.recv()` が事実上停止(10s で 1 frame)
2. **同一プロセスの** python-can Bus 単独でも同様に starve
3. 0x155-0x157 はアームが動いている間だけ broadcast される(静止時 0)

`candump` 別プロセス + stdout parse で迂回。最終的に `~/piper_test/drag_teach_calibrate.py` v4 で運用成功。

### 追加された資産

- `~/piper_test/drag_teach_calibrate.py` (v1→v4 段階的に書き直し、最終版)
- `~/piper_test/diagnose_master_broadcast.py` (candump 全 ID frame count)
- `~/piper_test/debug_master_sdk.py` (SDK 内部 `__fps_counter` 直視)
- `~/piper_test/test_master_mode.py` (master mode 挙動の三項テスト)
- `~/piper_test/recover_to_ready.py` (電源リセット後の復旧)
- `~/draw_piper/calibration/canvas_calibration.yaml` (31 点 + plane fit)

### 次のステップ

- `modules/robot.py` の `PanelFrame` / `panel_frame.yaml` を centroid + 法線で更新
- `draw_square_wall.py` の中心を centroid に合わせ実機で再描画 → 中心一致を確認
- ペン長/接触 X の再プローブ

詳細は `docs/20260523_1820_master_mode_drag_teach_calibration.md` を参照。
