# 壁面描画ブリングアップ — RPY規約確定・接触検出・j5過熱フォルト

> 日時: 2026-05-22 22:46 (JST)
> 関連既存ファイル: `20260522_1940_physical_setup_calibration_geometry.md`, `20260521_1757_drawing_system_v04_design.md`, `20260522_1700_piper_jointctrl_solved.md`

## 実施したこと

垂直キャンバスへの壁面描画（テスト 30mm 正方形）に向け、テストスクリプト群を `~/piper_test/` に作成・実行した。

- `read_state_full.py` — 関節＋エンドポーズ(RPY)の読み取り
- `fk_rpy_check.py` — URDF の順運動学でオフライン照合し EndPoseCtrl の RPY 規約を同定
- `wall_facing_ik.py` — オフライン数値 IK で板向き姿勢を算出
- `verify_wall_facing.py` — 板向き姿勢を実機検証
- `contact_detect_wall.py` — 接触検出（位置乖離方式 → effort 方式へ再設計）
- `contact_probe_interactive.py` — 対話式の手動接触プローブ
- `draw_square_wall.py` — 壁面 30mm 正方形描画
- `read_arm_status.py` — ステータス／エラー診断
- `calibrate_canvas_interactive.py` — 手動ペンタッチでキャンバス4隅＋中心を実測
- `recover_to_ready.py` — 中断後の ready pose v2 復旧
- `reach_window.py` — 届く範囲のオフライン計算

## 結果

- **EndPoseCtrl の RPY 規約を確定**: ZYX 固定軸 `R = Rz(RZ)·Ry(RY)·Rx(RX)`（単位 deg）。FK 照合の round-trip 誤差 0.0013。
- 板向き姿勢 `WALL_FACING_JOINTS = (0, 54.865, -27.267, 0.001, -22.598, 0)` を IK 算出 → 実機検証 PASS（位置誤差 0.25mm、ペン軸 0.00° off +X）。
- **壁面に 30mm 正方形を描画成功**（モーション誤差 最大 0.3mm）。ただし描画位置が低すぎた（後述）。
- 安全に描ける範囲 ≈ robot-Z [240, 390]mm / Y [±135]mm。推奨描画ウィンドウ Y[-90,+90] × Z[270,370]（実用描画高さ ~120mm）。

## つまずいた点

### 接触検出の方式 — 2回失敗

- **位置乖離方式 (v1)**: 指令 X と実到達 X の差で判定。板が剛固定でなく・ペンも圧縮性のため、ペンが板を約 1cm 押し・ペン先が約 1cm 縮んでも乖離が出ず未検出。アームが板想定面の 24mm 奥まで押し込んだ。位置ベースは剛体前提で不適。
- **effort 移動中監視 (v2)**: モーター effort 上昇で判定。移動中の動力学トルクを接触と誤検出。effort は整定後のみ読むべき。
- effort 信号はスティクションで ±300（0.001 N/m）程度のノイズがあり、軽接触の自動検出は不可。→ 対話式の目視プローブ `contact_probe_interactive.py` で接触点 `CONTACT_X ≈ 210` を取得した。

### 描画位置が低すぎた

30mm 正方形を Z=300 中心で描いたが、L字マーカー枠の中心ではなく下方だった。板の Z 基準（base_link 原点位置）の不確定が原因。

### j5 過熱フォルト（ハードウェアインシデント）

- `calibrate_canvas_interactive.py` で隅を探す際、ペンをワークスペース外（Y=-198, Z=430）へ jog し、アームが端で固着した。
- `EndPoseCtrl`(MOVE L) はワークスペース端では解けず、アームが出られないまま指令が継続 → j5（可動端 -70° 付近）が踏ん張り続けて過熱。
- `motor_overheating` / `collision_status` / `driver_error_status` が True、ドライバ自己無効化 → `EnablePiper` タイムアウト。アーム全体の `GetArmStatus().err_code` は 0 のままで、フォルトは各モーターの `foc_status` に出た。
- **電源リセットでフォルトクリア**し、`recover_to_ready.py`（ペン退避 → MOVE J で ready pose v2）で復旧した。

### reach_window.py 初版の誤結果

各点を中央姿勢から cold solve した IK が収束せず、Y=0 列以外が全 unreach の誤結果になった。flood-fill（中心から隣接点へ warm-start で伝播）方式に直して解決した。

## 学んだこと

- piper_sdk `EndPoseCtrl` の RPY は **ZYX 固定軸**。ペン⊥板（link6 +Z → +X）は **RY=+90**（`20260522_1940...` の pitch=-90 は符号誤り）。
- SDK エンドポーズ = URDF の `base_link → link6`。ツールオフセットは含まれず、ペン長は別計算。
- 接触検出は力（effort）ベースが必要だが、(1) effort は整定後のみ読む (2) スティクションノイズで軽接触の自動検出は困難。当面は対話目視プローブが現実的。
- **アームが動かないまま `EndPoseCtrl` を出し続けると関節が踏ん張り過熱フォルトする**。ツールは「届く範囲外へ jog させない／動かなければ即停止」が必須。
- ワークスペース端から脱出するには **MOVE J（関節空間）**。MOVE L は端で無力。
- モーターフォルトの確認は `GetArmLowSpdInfoMsgs()` の各モーター `foc_status`。回復は電源リセット。
- アームの安全描画範囲は限られる（robot-Z 約 240〜390mm）。キャンバス上部は届かない。
- キャリブレーションは ArUco/カメラでなく手動ペンタッチで行う方針（カメラ未購入、robot 座標で直接実測でき実装が速い）。

## 次にやること

🔲 キャンバスを安全描画ウィンドウ（Y[-90,+90] / Z[270,370] 目安）に収まる位置・サイズへ再配置
🔲 `calibrate_canvas_interactive.py` を「安全窓の外へ jog させない／動かなければ即停止」する作りに改修
🔲 再キャリブレーション → `~/draw_piper/calibration/canvas_calibration.yaml` 生成
🔲 `draw_square_wall.py` の描画中心を実測キャンバス中心へ更新 → 再描画
🔲 圧縮後のツール長（約 90mm）を精密再測、ペンマウントの再固定（テープ固定がずれた）
🔲 確定知見を `~/draw_piper/modules/robot.py` のパネル層 / `panel_frame.yaml` に反映
