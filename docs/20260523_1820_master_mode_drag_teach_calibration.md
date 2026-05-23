# master mode ハンドガイドによるキャンバスキャリブレーション成功

> 日時: 2026-05-23 18:20 (JST)
> 関連既存ファイル: `20260522_2246_wall_drawing_calibration_j5_fault.md`, `20260522_1700_piper_jointctrl_solved.md`, `20260522_1940_physical_setup_calibration_geometry.md`

## 実施したこと

垂直アクリル板のキャンバスキャリブレーションを「手でアームを動かして点を記録する」ドラッグティーチ方式で行うため、`~/piper_test/` に下記スクリプトを段階的に作成・実行した。

- `test_master_mode.py` — `MasterSlaveConfig(0xFA, 0, 0, 0)` の挙動を確認(Q1: ハンドガイド可、Q2: SDK reads 凍結、Q3: 0xFC 復帰不可)
- `drag_teach_calibrate.py` (v1〜v4 まで段階的に再設計)
  - v1: python-can listener thread で 0x155-0x157 を直接購読 — 0 frames
  - v2: SDK の `GetArmJointCtrl().Hz > 0` ゲートに変更 — Hz が常に 0
  - v3: in-process `python-can` Bus を別途開いて listener thread — これも 0 frames
  - v4: `candump -ta can0` を **サブプロセス**として起動し stdout を正規表現 → `struct.unpack(">ii", ...)` で関節 mdeg を抽出 — 成功
- `diagnose_master_broadcast.py` — `candump` を全 ID に対して走らせ、phase ごとのフレーム数を集計
- `debug_master_sdk.py` — SDK 内部 `__fps_counter.fps_data` を mangled-name で直接覗き、master mode 中の SDK 受信状態を観測
- `recover_to_ready.py` — 電源リセット後の ready pose v2 復旧(複数回実行)

## 結果

✅ **drag-teach 実機成功**(2026-05-23 18:12)。`MasterSlaveConfig(0xFA)` で master mode に入り、手でアームを 31 点誘導 → 平面フィット。

保存先: `~/draw_piper/calibration/canvas_calibration.yaml`

| 指標 | 値 |
|------|-----|
| 記録点数 | 31 |
| 平面 RMS 残差 | 3.82 mm |
| centroid (base_link 座標) | X=204.3, Y=-2.8, Z=299.7 mm |
| 法線 | [-0.998, +0.052, -0.036] |
| Y 範囲 | [-97, +77] mm (174mm 幅) |
| Z 範囲 | [+157, +464] mm (307mm 高さ) |

法線がほぼ `-X` 方向 → +X を向いた垂直壁、想定通り。RMS 3.82mm は板のたわみ + 手押し精度の合算で、描画許容範囲内。

## つまずいた点

### master mode の謎挙動 1: SDK の CAN 読みが完全停止

`MasterSlaveConfig(0xFA)` 送出後、SDK の `bus.recv()` ループが事実上止まる。`debug_master_sdk.py` の `__fps_counter` 直視で確認:

- pre-master: `ArmJoint_12` カウンタが 200 Hz で増加(`fps_data` の連続更新あり)
- in master: 10 秒で **1 frame** しか増えない(=0.1 Hz)

加えて、別途 in-process で開いた `python-can` Bus でも同じく 0 frames。つまり SDK 固有のバグではなく、**同一プロセス内のあらゆる socketcan ソケットが starve される**。

回避: `candump` は別プロセスなので影響を受けない。subprocess で起動 → stdout を parse する方式に切り替えて解決。

### master mode の謎挙動 2: 0x155-0x157 は静止時には broadcast されない

`diagnose_master_broadcast.py` の `in_master` フェーズで 0x155-0x157 が 27 Hz 観測できたのは「ユーザーが 15 秒間アームを手で動かした」ため。drag_teach v4 の 5 秒待ち中はアームを触らなかったので 0 frames だった。

→ 「**WIGGLE the arm by hand for ~3 s to trigger 0x155-7 broadcast**」というプロンプトに変更し、1 秒ごとにフレーム数を表示。動かし始めるとすぐ broadcast 開始。

### SDK の `GetArmJointCtrl().Hz` のバグ

`cal_average` が `all(args)` で 0 を弾く実装。100ms スライス境界で 12/34/56 のいずれかが 0 frames だと Hz が 0 になる。今回は他要因(starve)も重なって機能しなかった。

### master mode 抜けは必ず電源リセット

`MasterSlaveConfig(0xFC)` 送出だけでは復帰しない。コマンド受付不能になり `EnablePiper` が timeout する。`test_master_mode.py` の Q3 で確認済。本セッション中は drag-teach + diagnose + debug で計 4 回電源リセット。

## 学んだこと

- **master mode は SDK と相性が悪い**: 同一プロセス内のあらゆる python-can ソケットが受信不能になる(SDK の `bus.recv()` も含む)。並列読み出しは別プロセス(`candump` subprocess または別 Python プロセス)が必須。
- **0x155-0x157 は master arm が「動いた時」だけ broadcast する**(静止時は送出ゼロ)。listener は frame 待ちタイムアウトの前にユーザーへ「揺らして」と促す UI が必要。
- **drag-teach に SDK の関節読み出しは要らない**。candump stdout を `(\s*\([0-9.]+\)\s+\S+\s+([0-9A-Fa-f]+)\s+\[\d+\]\s+([0-9A-Fa-f ]+))` で parse、bytes 0-3 / 4-7 を `>i` で unpack するだけで mdeg 整数が取れる。`wall_facing_ik.fk` を使って FK で end-pose を出すフローは前回(M5/M6)からそのまま流用可能。
- **`MasterSlaveConfig(0xFC)` 後は必ず電源リセット**(`AC OFF → USB-CAN 抜く → 30s 待つ → 戻す → CAN up`)。drag-teach は 1 回の電源リセット消費を前提に設計する。
- 平面フィット結果(法線 ≈ -X、RMS 3.82mm)は妥当。Y/Z レンジは「アームが届く範囲」で制限される(高さ Z=464mm までは届くが Z=157mm 付近は j2/j3 が限界に近い)。

## 次にやること

🔲 `~/draw_piper/modules/robot.py` の `PanelFrame` 層 / `panel_frame.yaml` に今回の plane fit 結果を反映(`origin` = centroid、`normal` = -X 方向)
🔲 `draw_square_wall.py` の描画中心を `centroid_mm = (204.3, -2.8, 299.7)` へ更新 → 30mm 正方形を実機に再描画して中心が合うか確認
🔲 ペン長(現在 ~90mm)再測 + 接触 X 補正(`contact_x_mm = 204.3` は centroid なので、ペン先で軽接触する真の X はもう一度プローブで確認したい)
🔲 描画範囲を centroid 周辺の安全窓 Y[-90,+90] × Z[+270,+370] に絞る(31 点中 Z=464 や Z=157 はリーチ限界寄り)
🔲 drag_teach_calibrate.py を `~/draw_piper/scripts/` へ移すか、`piper_test` に残すか方針決め(現在は piper_test、本番運用に上げるなら robot.py から呼べる関数化が望ましい)
