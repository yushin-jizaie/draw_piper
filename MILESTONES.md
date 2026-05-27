# 🌳 draw_piper マイルストーン・ツリー

> **目的**: 詰まった時に「正常な地点」へ素早く戻るための地図。
> Git は履歴の倉庫、このファイルは *どこが正常か / どこで詰まったか* を一目で見る索引。
>
> 最終更新: 2026-05-25 15:17

---

## ★ 現在の正常地点（ここに戻れば安全）

| 項目 | 値 |
|------|-----|
| マイルストーン | **M12 — canvas_calibration yaml schema v3 (5-phase 設計) IO + GUI 配線完了・dry-run 検証 PASS・実機未検証** |
| コミット | `c24438c` |
| 戻り方 | `git checkout c24438c`(または最新 `main`) |
| 正常の確認 | `~/draw_piper/venv/bin/python ~/piper_test/test_canvas_calibration_io.py` で 50 checks PASS、`~/draw_piper/venv/bin/python ~/piper_test/test_step2_v3_save.py` で 33 checks PASS。GUI 起動 → `_load_calib_defaults` が disk 上 v1 yaml (M10) を読んで center_y/z + contact_x を反映。M11 同等の操作フロー(B1 4 corners + B2 plane extras + Save)で **v3 形式 yaml を書き出す**(traces.surface に legacy extras を入れる Step 2 transitional 仕様)。実機 drag-teach は M11 同様未検証(Step 4 で B2 自動サンプリングが入った後にまとめて検証予定) |

> 注: M9 は並走中の **VLM/画像生成スレッド**の正常地点。壁面描画スレッドは M10 → M11 と進行。両スレッドは独立で戻り先はどちらも `main` で OK。

---

## ツリー（時系列 ↓）

```
凡例   ● 正常地点（戻れる checkpoint）    ✗ 詰まり・失敗・行き止まり
       │ trunk（正常な歩み）            ├──► branch（詰まりへ分岐）

時系列↓   ✅ ポジティブ ＝ trunk（● 戻れる正常地点）   ❌ ネガティブ ＝ branch（✗ 詰まり）
─────────────────────────────────────────────────────────────────────────────────────

05-21 19:04   ● M0  プロジェクト初期化                          [066b02e]
              │
              ├──►  ✗ N1  V1.8 feedback ID シフト（0x2A* → 0x3A*）
              │           SDK 0.6.1 が joints / end-pose を読めず全て 0
              │           対処: piper_feedback.py で 0x3A* を別 socketcan 読み
              │           ┗━ 復旧先 ▶ M1（workaround を同梱して前進）
              │
05-21 20:29   ● M1  Step E パイプライン smoke test 通過（mock）  [a674294]
              │      └ V1.8 feedback workaround 同梱
              │
              ├──►  ✗ N2  JointCtrl が実機で効かない（5 時間難航）
              │           症状: 指令しても動かない（「カチッ」と音はする）
              │           原因① master mode 残留（0x3A*・外部指令を拒否）
              │           原因② Config Init 未送信
              │           対処: MasterSlaveConfig(0xFC,0,0,0) + 電源完全リセット
              │                 + Config Init
              │           ┗━ 復旧先 ▶ M2
              │
05-22 17:11   ● M2  JointCtrl 実機解決（Config Init が最後の鍵） [14afe6e]
              │
05-22 17:40   ● M3  connect() に Config Init 自動統合           [019dfd7]
              │      └ GUI フリー初期化が完成
              │
              ├──►  ✗ N3  CAN TX 物理断（再発性ハードウェア）
              │           TX がサイレント失敗・error-pass カウンタ上昇
              │           対処: USB-CAN アダプタ抜き差し
              │           ┗━ 復旧先 ▶ 直前の正常地点（コード変更は不要）
              │
05-22 17:55   ● M4  ready pose 実機テスト PASS                  [3c23e92]
              │      └ 直立ゼロ姿勢 → ready pose、最大関節誤差 0.054°
              │
05-22 18:44   ● M5  cartesian / EndPoseCtrl 実機検証 PASS         [3a4e4b8]
              │      └ 空中 30mm 正方形トレース、最大軸誤差 0.4mm
              │
05-22 18:50   ● M6  draw_stroke() 実機統合テスト PASS         [8b27f8b]
              │      └ 空中 30mm 正方形、7 ウェイポイント最大誤差 0.3mm、
              │        閉ループ復帰 0.2mm
              │
05-22 19:04   ● M7  robot.py パネル座標層を一般化（mock 検証） [07311e6]
              │      └ PanelFrame + goto_panel/draw_stroke_panel、
              │        垂直アクリル板対応。実ジオメトリは Step B 待ち
              │
05-23 16:15   ● M8  VLM ↔ ImageGenerator つなぎこみ(段階的スワップ実証)  [41b8221]
              │      └ image_gen.py 本実装、test_vlm_to_image.py 統合テスト追加。
              │        3 サイクル安定(定常 ~20s)、ピーク 12.93GB << 15.57GB 予算、
              │        CPU offload / モデル縮小フォールバックは不要と確定
              │
05-23 17:10   ● M9  フルパス統合 + prompt 整形  [f01a91a]
              │      └ prompt_builder.py のカンマ前スペース修正、
              │        test_vlm_to_image.py に STAGE 9 (Vectorizer) 追加。
              │        3 サイクル安定(定常 ~19.7s、+vectorize 0.15-0.17s)、
              │        strokes 数 187-207 (CV ~5%)、cycle_NN/strokes.json と
              │        vec_debug/06_strokes.png まで生成
              │
              ├──►  ✗ N4  master mode で SDK feedback が読めない
              │           ① test_master_mode.py: 0xFA で hand-movable は OK
              │              だが GetArmJointMsgs は frozen、0xFC では復帰不可
              │           ② diagnose_master_broadcast.py(candump): 0x155-7
              │              は 27Hz broadcast されている。ただし**アームを
              │              手で動かしている間だけ**送出される
              │           ③ debug_master_sdk.py: SDK の bus.recv() が
              │              master mode 中に 10s で 1 frame しか拾わない。
              │              同プロセス内の python-can Bus も同様に starve。
              │              candump(別プロセス)のみ正常受信
              │           対処: drag_teach_calibrate.py v4 で candump を
              │                 subprocess 起動 → stdout を正規表現 + struct
              │                 で parse → 関節 mdeg を取得。SDK の読みは
              │                 完全に迂回。1 回の電源リセット消費前提で運用
              │           ┗━ 復旧先 ▶ M10
              │
05-23 18:12   ● M10 drag-teach キャンバスキャリブ実機成功  [221f0fb]
              │      └ MasterSlaveConfig(0xFA) + candump subprocess + URDF FK で
              │        31 点記録、平面 RMS 3.82mm、Y[-97,+77] × Z[+157,+464]、
              │        centroid X=204.3 Y=-2.8 Z=299.7、normal ≈ -X(垂直壁)。
              │        保存先 calibration/canvas_calibration.yaml
              │
05-23 21:00   ● M11 壁面描画 GUI 統合                            [f43e8e4]
              │      └ ~/piper_test/wall_drawing_gui.py (Tkinter)。
              │        Status bar + 4 section: Connection / Drag-Teach (2-phase) /
              │        Tune Contact / Draw Square。
              │        Joint と Draw の speed 分離(default 5/2)、adaptive settle、
              │        pkexec で CAN up、subprocess respawn で Restart GUI、
              │        Center Y/Z + contact_x の起動時自動ロード。
              │        canvas_calibration.yaml schema v2(whiteboard_corners +
              │        plane_extras + computed)。
              │        1-phase drag-teach + 描画は実機検証済、2-phase drag-teach は
              │        コード完成・実機検証は次セッション。
              │
05-25 15:17   ● M12 canvas キャリブ v3 IO + GUI 配線  [c24438c]
                     └ ~/piper_test/canvas_calibration_io.py 新規(280 行)。
                       v1 (M10 raw) / v2 (M11 GUI) / v3 (5-phase 設計) yaml 全対応の
                       reader + v3 専用 writer。50 checks PASS。
                       wall_drawing_gui.py 3 箇所改修:
                         - _load_calib_defaults → read_calibration() 経由
                         - _capture_point に timestamp 追加
                         - _fit_and_save → write_v3() に置換 (旧 plane_extras_mm は
                           Step 2 transitional で traces.surface へ写像)
                       設計ドキュメントとの差分: corner schema を {y,z} から
                       full record (pen_yz + joints + end_pose + timestamp) に拡張
                       (plane fit の X 入力に必要なため)。
                       dry-run 33 checks PASS (Tk 非起動で _fit_and_save unbound 呼び)。
                       実機 drag-teach は Step 4 で B2 自動サンプリングが入った後に検証予定。
                       設計: docs/20260525_1447_canvas_calibration_v3_design.md
                       進捗: docs/20260525_{1457,1517}_canvas_calibration_v3_step{1,2}_*.md
              │
              ├──►  ✗ N5  デバッグ用 image push で orphan branch + git clean -fd し、
              │           training/ (raw 36 + LoRA + dataset)、 venv/ (torch 等)、
              │           calibration/*.yaml の local uncommitted 修正、 logs 出力、
              │           その他 untracked ファイル群を一括 wipe
              │           原因: orphan branch で .gitignore も untracked になり、 clean が
              │             全消し (gitignored 含む)
              │           対処: ① Trash 内 training.zip (708M, 16:54 時点) から
              │                    raw/ + v0 LoRA + dataset を rsync 復元
              │                 ② venv は pip install で再構築 (torch 2.5.1+cu121,
              │                    diffusers 0.38.0, transformers 5.9.0)
              │                 ③ git fsck --unreachable で dangling blobs 発見
              │                    panel_frame.yaml と canvas_calibration.yaml の
              │                    最新版 (uncommitted だった) を recovered_yaml/ に
              │                    退避 → 採用
              │           教訓: orphan branch + clean -fd 禁止、 git worktree で隔離
              │           ┗━ 復旧先 ▶ M13
              │
05-28 00:14   ● M13 Plan E (Illustrious + MistoLine + inpaint) で純線画達成  [ce0eefc]
              │      └ Animagine 3.1 + 自前 LoRA (v0/v1/v2/v3) は全失敗
              │        (黒テクスチャ + 文字暴走)。 ユーザ提案 「漫画モデルを使えばいい」
              │        を反映し、 base を Illustrious XL early-release-v0 (Danbooru
              │        訓練、 monochrome/lineart tag 対応) に乗換、 LoRA off。
              │        新 preset illustrious_v2_inpaint で 顔保持 + 体描き足し +
              │        ハッチング/塗り無しの 線画 達成。 ロボット描画適合な strokes 取得。
              │
05-28 01:50   ● M14 Vectorizer + Robot 結合 動作確認 (mock + real CAN)  [82cb13b]
              │      └ Phase 3 生成画像 → Vectorizer.vectorize_to_panel()
              │        → strokes_mm 107 strokes (panel 107.05 x 197.07 mm)
              │      Robot(mock=True).draw_stroke_panel 全 stroke で
              │        travel→descend→trace→pen-up が log で確認可。
              │      Robot(mock=False).connect/disconnect: 実機電源 OFF で
              │        CAN 送信 → ERROR-PASSIVE (ACK 無しのため、 正常)。
              │
05-28 08:38   ● M15 IP-Adapter two-stage で 松本大洋画風 + 顔保持 同時達成 ★★ 現在地 ★★  [c32c2c1]
                     └ 自前 LoRA v0-v3 すべて失敗 (dataset 黒/文字暴走) を IP-Adapter
                       (h94/IP-Adapter sdxl_models/ip-adapter_sdxl) で迂回。
                       1 段で IP-Adapter 使うと style ref の構図 (顔=頭) と 元 sketch
                       (中央 face oval) が競合 → 顔が胴体中央に。
                       Two-stage で解決:
                         Stage 1: illustrious_v2_inpaint @ 1024res で 構図確定
                         Stage 2: img2img + IP-Adapter (strength 0.45, ip_scale 0.6,
                                  768res) で style 転写、 構図維持
                       Vectorizer 後で 114 strokes / 2542 pts。 松本タッチ
                       (spiky 髪、 rough/expressive lines、 dynamic body) + 純線画 +
                       顔保持 を同時に達成。 ロボット描画 ready。
                       script: scripts/test_ip_adapter_two_stage.py
                       demo: github phase-e-results-20260528/matsumoto_v2_two_stage/
```

---

## ✅ ポジティブ・マイルストーン詳細

戻り方はすべて共通: `git checkout <コミット>`

| ID | 日時 | 内容 | コミット | 正常の確認方法 |
|----|------|------|----------|----------------|
| M0 | 2026-05-21 19:04 | プロジェクト初期化 | `066b02e` | リポジトリが存在する |
| M1 | 2026-05-21 20:29 | Step E パイプライン smoke test（mock）+ V1.8 feedback workaround | `a674294` | `venv/bin/python run_draw_test.py` が line/square/circle を完走 |
| M2 | 2026-05-22 17:11 | JointCtrl 実機解決（Config Init が鍵） | `14afe6e` | Config Init 後、JointCtrl で実機が指令通り動く |
| M3 | 2026-05-22 17:40 | `connect()` に Config Init 自動統合（GUI フリー） | `019dfd7` | `Robot(mock=False).connect()` が `[robot] Config Init done` を出す |
| M4 | 2026-05-22 17:55 | ready pose 実機テスト PASS | `3c23e92` | `venv/bin/python test_ready_pose.py move` が `RESULT: PASS` |
| M5 | 2026-05-22 18:44 | cartesian / EndPoseCtrl 実機検証 PASS（空中 30mm 正方形） | `3a4e4b8` | `venv/bin/python test_cartesian.py move` が `RESULT: PASS` |
| M6 | 2026-05-22 18:50 | draw_stroke() 実機統合テスト PASS（travel→pen-down→描画→pen-up） | `8b27f8b` | `venv/bin/python test_draw_stroke.py move` が `RESULT: PASS` |
| M7 | 2026-05-22 19:04 | robot.py にパネル座標層を一般化（垂直パネル対応、mock 検証） | `07311e6` | `run_draw_test.py` mock 完走（非破壊）、`Robot(mock=True)` がパネル YAML をロード |
| M8 | 2026-05-23 16:15 | VLM ↔ ImageGenerator つなぎこみ(段階的スワップ実証) | `41b8221` | `venv/bin/python scripts/test_vlm_to_image.py --steps 4 --cycles 3` が 3 サイクル完走、各サイクル末で `allocated=0.01GB`(リーク無し)、ピーク 12.93GB |
| M9 | 2026-05-23 17:10 | フルパス統合 + prompt 整形 (VLM → prompt_builder → ImageGen → Vectorizer) | `f01a91a` | `venv/bin/python scripts/test_vlm_to_image.py --steps 4 --cycles 3` が 3 サイクル完走、各 `cycle_NN/strokes.json` で n_strokes が 180-220、`cycle_NN/vec_debug/06_strokes.png` がロボット線画として認識可能 |
| M10 | 2026-05-23 18:12 | drag-teach キャンバスキャリブ実機成功(壁面描画スレッド) | `221f0fb` | `calibration/canvas_calibration.yaml` が存在、`canvas.n_points=31`、`plane_fit.rms_residual_mm=3.82`、centroid (204.3, -2.8, 299.7), 法線 ≈ -X 方向 |
| M11 | 2026-05-23 21:00 | 壁面描画 GUI 統合(Tkinter wrapper、2-phase drag-teach 実装、speed 分離、Restart GUI、pkexec CAN up) | `f43e8e4` | `~/piper_test/wall_drawing_gui.py` 起動 → GUI 表示 + CAN status 反映、`Connect → Recover → Tune Contact → Draw Square` で四角描画(M10 キャリブのまま)。2-phase drag-teach は実装済・実機未検証(次セッション) |
| M12 | 2026-05-25 15:17 | canvas_calibration yaml schema v3 (5-phase 設計) IO モジュール + GUI 配線(read/write_v3、_load_calib_defaults、_capture_point、_fit_and_save) | `c24438c` | `~/draw_piper/venv/bin/python ~/piper_test/test_canvas_calibration_io.py` で 50 checks PASS、`~/draw_piper/venv/bin/python ~/piper_test/test_step2_v3_save.py` で 33 checks PASS。GUI 起動時 `_load_calib_defaults` が v1/v2/v3 を自動検出してロード。実機 drag-teach は M11 同様未検証で Step 4 (B2 自動サンプリング実装後) にまとめて検証予定 |
| M13 | 2026-05-28 00:14 | Plan E (Illustrious XL early-release-v0 + MistoLine + inpaint) で 純線画 + 顔保持 + 体描き足し 達成。 Animagine + 自前 LoRA 路線 (v0-v3 全失敗) を base 乗換で迂回 | `ce0eefc` | `venv/bin/python -m scripts.compare_imagegen_models --guide scripts/test_sketch.jpg --prompt "1boy, solo, young boy with full body, messy hair, surprised expression, simple t-shirt, standing" --presets illustrious_v2_inpaint --seed 42` で 顔保持 + 体描き足し の純線画。 demo: branch phase-e-results-20260528/phase_e_demo/ |
| M14 | 2026-05-28 01:50 | Vectorizer (strokes_mm 化) + Robot.draw_stroke_panel mock/real CAN 双方で動作確認 | `82cb13b` | `Vectorizer.vectorize_to_panel(panel=PanelFrame)` で 107 strokes_mm 取得 (panel 107.05 x 197.07 mm)。`Robot(mock=True).draw_stroke_panel(strokes_uv)` 完走 + `Robot(mock=False).connect/disconnect` 実 CAN (実機電源 OFF) で OK (ERROR-PASSIVE = ACK 無し正常) |
| M15 | 2026-05-28 08:38 | IP-Adapter two-stage で 松本大洋画風 + 顔保持 + ロボット適合 同時達成 | `c32c2c1` | `venv/bin/python -m scripts.test_ip_adapter_two_stage --user-sketch scripts/test_sketch.jpg --style-ref training/matsumoto_taiyo/raw/IMG_4311.JPG --output logs/ip_2stage_<ts> --stage1-resolution 1024 --resolution 768 --stage2-strength 0.45 --ip-scale 0.6 --seed 42` で `30_vectorized_strokes.png` に 114 strokes / 2542 pts の松本タッチ純線画。 demo: branch phase-e-results-20260528/matsumoto_v2_two_stage/ |

---

## ❌ ネガティブ・マイルストーン詳細

| ID | 日時 | 症状 | 原因 | 対処 | 詳細ドキュメント |
|----|------|------|------|------|------------------|
| N1 | 2026-05-21 | `GetArmJointMsgs` / `GetArmEndPoseMsgs` が全て 0 | V1.8 ファームが feedback を `0x2A*` → `0x3A*` にシフト、SDK 0.6.1 未対応 | `modules/piper_feedback.py` で `0x3A*` を別 socketcan 読み（workaround）。後に master mode 解除で `0x2A*` が復活し fallback 扱いに | `docs/20260521_2000_piper_feedback_issue_debug.md` |
| N2 | 2026-05-22 13:00–17:00 | JointCtrl 指令で実機が動かない（音はする） | ① master mode 残留で外部指令を拒否 ② Config Init 未送信 | `MasterSlaveConfig(0xFC,0,0,0)` + 電源完全リセット（AC+USB 抜いて 30 秒）+ Config Init（`ArmParamEnquiryAndConfig(0x01,0x02,0,0,0x02)`） | `docs/20260522_1700_piper_jointctrl_solved.md` |
| N3 | 再発性 | CAN TX がサイレント失敗、コマンドが届かない | USB-CAN 物理層の不調 | `ip -details -statistics link show can0` でエラーカウンタを確認 → USB-CAN アダプタを抜き差し | `docs/20260522_1700_piper_jointctrl_solved.md`（教訓 5） |
| N4 | 2026-05-23 17:00 | master mode 中、SDK の `GetArmJointMsgs` / `GetArmJointCtrl` が 0/stale。in-process の `python-can` Bus も同様に starve | 同一プロセス内の socketcan ソケットが master mode 中に受信不能化(原因不明だが再現性あり)。加えて 0x155-0x157 はアームが動いている時だけ broadcast される | `candump -ta can0` を subprocess 起動 → stdout を parse して 0x155-7 を decode。`MasterSlaveConfig(0xFC)` 後は電源リセット必須 | `docs/20260523_1820_master_mode_drag_teach_calibration.md` |
| N5 | 2026-05-28 00:40 | orphan branch + git clean -fd で untracked file 一括 wipe (training/ raw 36+LoRA+dataset、 venv/ Python パッケージ、 calibration/*.yaml local mods) | orphan branch では .gitignore も untracked となり、 git clean -fd が gitignored 含めて全消去 | ① Trash の training.zip (708M, 16:54 時点) から raw/ + v0 LoRA + dataset を rsync 復元 ② venv は pip install で再構築 (torch 2.5.1+cu121 等) ③ `git fsck --unreachable` で dangling blobs から panel_frame.yaml / canvas_calibration.yaml の uncommitted 最新版を発見、 `recovered_yaml/` に保存後 採用 (M15 への復旧経路) | `docs/20260528_matsumoto_pursuit_plan.md`、 branch `recovered-yaml-20260528` |

---

## 使い方 / メンテナンス

このファイルは手で育てる。Git コミットのたびではなく、**節目ごと**に更新する。

- **正常な状態に到達したら** → trunk に `●` を 1 行追加（日時・ID・内容・コミット）。
  コミット後にハッシュを記入し、`★★ 現在地 ★★` を最新の `●` に付け替える（現在地は常に 1 つ）。
- **詰まったら** → 直近の `●` から `├──►` で `✗` ブランチを足し、症状をその場で書く。
- **解決したら** → ブランチに「対処」と「復旧先 ▶」を追記。必要なら新しい `●` を trunk に追加。
- **詰まって戻りたい時** → 「★ 現在の正常地点」か、ツリーで直近の `●` のコミットを `git checkout`。

更新タイミングは Claude Code が能動的に提案する（判断基準・条件は `CLAUDE.md` 参照）。

> ID 採番: ポジティブ＝`M0, M1, …` / ネガティブ＝`N1, N2, …`（時系列で連番）。
