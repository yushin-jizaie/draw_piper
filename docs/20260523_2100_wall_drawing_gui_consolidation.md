# 壁面描画 GUI 構築 — 1-プロセス wrapper、2-phase drag-teach、速度分離

> 日時: 2026-05-23 21:00 (JST)
> 関連既存ファイル: `20260523_1820_master_mode_drag_teach_calibration.md`, `20260522_2246_wall_drawing_calibration_j5_fault.md`

## 実施したこと

M10 の drag-teach キャリブと draw_square_wall.py を、ターミナル操作なしで通して使える Tkinter GUI に統合した。`~/piper_test/wall_drawing_gui.py` を新規作成し、段階的に 7 回 commit して機能を積んだ。

最終構成(commit `d3f497f`):

- **Status bar**: CAN 状態(`ip link show can0` を 2 秒ごと poll)、Connected/Master 表示、現在関節、Restart GUI / Quit GUI
- **1. Connection**: `CAN up (sudo)`(pkexec で graphical password)、Joint speed (default 5)、Draw speed (default 2)、Connect/Recover/Storage/Disconnect
- **2. Drag-Teach (2-phase)**: B1 4 隅を順序付き (TL→TR→BR→BL) + B2 平面用追加点(≥4)
- **3. Tune Contact**: X offset Spinbox、`Go to Canvas Center (pen-down)`、X -1.0 / -0.5 / +0.5 / +1.0 nudge、`Lift Pen`
- **4. Draw Square**: Side Spinbox + 4 隅 ΔYΔZ Spinbox 8 個 + `Reset all corner ΔΥΖ`、`Draw Square`
- **canvas_calibration.yaml schema v2**: `whiteboard_corners_mm: {tl, tr, br, bl}`、`whiteboard_computed: {center_mm, width_mm, height_mm}`、`plane_extras_mm: [...]`、`plane_fit: {normal, centroid_mm, rms_residual_mm}` を新設

draw_square_wall.py 単体スクリプトにも `CONTACT_X_OFFSET_MM` 一括シフトを追加(GUI と独立)。

chat 側 (Phase A) との yaml 規約合意:

- `panel_frame.yaml` は panel の単一正本、namespaced blocks で並走
  - `panel:` ブロック = robot side (canvas_calibration から converter で生成、私の TODO)
  - `phase_a_calibration:` ブロック = camera_px ↔ panel_mm の対応点 (chat 側 `calibrate_panel.py` が書く)
- `canvas_calibration.yaml` は drag-teach の raw 証跡として温存
- panel UV 規約: **ホワイトボード座標**(0..230, 0..300)、左下原点、+u = base +Y、+v = base +Z

## 結果

- M10 の 31 点キャリブで描画した正方形 1 個成功(GUI 経由)。実測誤差 <0.5mm
- 2-phase drag-teach の **コード実装完了、実機未検証**(M11 として記録、次セッションで検証)
- 速度・接触深さ・キャンバス中心・各隅補正のすべてが GUI 内 Spinbox + ボタンで完結
- ターミナル必要な物理操作: アーム電源 ON/OFF、USB-CAN 抜き差し、(オプション) `pkexec` で sudo パスワード入力。**Restart GUI は in-app**(subprocess.Popen で fresh プロセス起動 + 旧 quit)で完結

## つまずいた点

### 速度設定を 1 つにしたら裏目に出た

- 単一 `Motion speed` Spinbox で全モーション共通化したが、描画時(MOVE L)に速くしすぎてアームがバウンドし、speed を 2 に落とすと ready pose 移動(MOVE J)が遅くてぎこちなくなる、というジレンマ発生
- 対処: Joint speed と Draw speed を別 Spinbox に分離(default 5 / 2)、それぞれ独立に調整可能に

### speed=2 で MOVE J が "MOVE J failed err 25.49°" 誤検出

- settle 検査の最大反復が固定 8s だったため、speed=2 で wall-facing への 52° 移動が時間内に終わらず、まだ動いている途中に verify を叩いてしまい RuntimeError を投げていた
- そのまま `gui_restart_required=True` を立てて「post-master state です」と誤った診断
- 対処: `settle_seconds = max(base, 30/speed)`(speed=5→6s、speed=2→15s、speed=1→30s)で適応的にスケール。さらに `_move_joints` は「settle してから err 超え」と「settle 前タイムアウト」を区別:
  - settle 前 → 「still in motion」と log してさらに 15s 待つ
  - 待ってもまだ未到達 → ようやく gui_restart_required を立てる

### master mode 後の in-process SDK 再接続が壊れる

- `MasterSlaveConfig(0xFC)` 後、物理電源リセット → CAN up → 同プロセス内で Connect しても、SDK が stale なフィードバック値を返し続け、MOVE J が silently 失敗する(joint err が動かない)
- in-process python-can Bus も同じ starvation を持つ(`debug_master_sdk.py` で確認済の既知問題)
- 対処: **GUI を fresh プロセスとして再起動**するボタンを追加。`subprocess.Popen([sys.executable, __file__], start_new_session=True)` で新プロセスを spawn してから `self.root.quit()` で旧プロセス終了。これで socketcan ソケットが完全に新しくなる
- master mode 終了モーダルも「ターミナルから再起動」表記を撤廃して「Restart GUI ボタンを押せ」に統一

### sudo を GUI から呼ぶ手段

- CAN up に `sudo ip link set can0 ...` が要るが、これだけのためにターミナルに戻るのは UX 悪い
- 対処: **pkexec** で graphical password prompt(Ubuntu Desktop の polkit エージェント使用)。`pkexec sh -c "ip link set can0 type can bitrate 1000000 || true; ip link set can0 up || true; exit 0"` で 1 回のパスワード入力で完結。`|| true` でビットレート既設定の「busy」エラーを吸収

## 学んだこと

- **Tkinter で十分**: 専用 UI ライブラリ(PyQt 等)無しに、ステータス bar / 4 セクション / Spinbox / 動的ボタンラベル / モーダルダイアログまで普通に組める。`tk.Spinbox` の `command=` callback は arrow click のみ発火、直接タイプは検知不可
- **subprocess respawn pattern** が in-process リソース問題(SDK starvation、socket 残留状態等)への一般解。`start_new_session=True` で親 process 終了に巻き込まれない
- **pkexec の戻り値**: 0=OK、126=user cancel、127=auth fail。GUI から呼ぶ時はこの 3 つを区別してログに残す
- **drag-teach の 2-phase 化** は Phase A 連携を考えると必然: 順序付き 4 隅(panel origin + size 用)と順序不問の追加点(平面フィット精度向上用)は目的が違うので state machine 分離が素直
- 接触深さ調整は描画ワークフローから分離して独立 section にする方が UX 良い(描画パラメータと混ざると分かりにくい)

## 次にやること

🔲 物理電源リセット + CAN up + Restart GUI → 2-phase drag-teach を実機で実行(4 隅 + 4+ 追加点)
🔲 保存後、Restart GUI → Center Y/Z / contact_x_mm が自動反映されるか確認
🔲 Tune Contact で接触深さ調整 → Draw Square 実機検証
🔲 `scripts/canvas_to_panel_frame.py` converter 実装(canvas_calibration → panel_frame.yaml の `panel:` block)。`--default-panel-size 230 300` 対応で Phase A 未着地でも先行可能に
🔲 `PanelFrame.in_bounds()` に reach bounds check 追加(`canvas.bounds_yz_mm` の bounding box でクリップ、v1 はシンプル方式)
🔲 chat 側 Phase A 着地(`phase_a_calibration:` block 書き込み完了)待ち → 統合 mock chain テスト
