# 🌳 draw_piper マイルストーン・ツリー

> **目的**: 詰まった時に「正常な地点」へ素早く戻るための地図。
> Git は履歴の倉庫、このファイルは *どこが正常か / どこで詰まったか* を一目で見る索引。
>
> 最終更新: 2026-05-23

---

## ★ 現在の正常地点（ここに戻れば安全）

| 項目 | 値 |
|------|-----|
| マイルストーン | **M8 — VLM ↔ ImageGenerator つなぎこみ(段階的スワップ実証)** |
| コミット | `41b8221` |
| 戻り方 | `git checkout 41b8221`(または最新 `main`) |
| 正常の確認 | `venv/bin/python scripts/test_vlm_to_image.py --steps 4 --cycles 3` が 3 サイクル完走、各 cycle 末で `allocated=0.01GB` |

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
05-23 16:15   ● M8  VLM ↔ ImageGenerator つなぎこみ(段階的スワップ実証) ★★ 現在地 ★★  [41b8221]
                     └ image_gen.py 本実装、test_vlm_to_image.py 統合テスト追加。
                       3 サイクル安定(定常 ~20s)、ピーク 12.93GB << 15.57GB 予算、
                       CPU offload / モデル縮小フォールバックは不要と確定
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

---

## ❌ ネガティブ・マイルストーン詳細

| ID | 日時 | 症状 | 原因 | 対処 | 詳細ドキュメント |
|----|------|------|------|------|------------------|
| N1 | 2026-05-21 | `GetArmJointMsgs` / `GetArmEndPoseMsgs` が全て 0 | V1.8 ファームが feedback を `0x2A*` → `0x3A*` にシフト、SDK 0.6.1 未対応 | `modules/piper_feedback.py` で `0x3A*` を別 socketcan 読み（workaround）。後に master mode 解除で `0x2A*` が復活し fallback 扱いに | `docs/20260521_2000_piper_feedback_issue_debug.md` |
| N2 | 2026-05-22 13:00–17:00 | JointCtrl 指令で実機が動かない（音はする） | ① master mode 残留で外部指令を拒否 ② Config Init 未送信 | `MasterSlaveConfig(0xFC,0,0,0)` + 電源完全リセット（AC+USB 抜いて 30 秒）+ Config Init（`ArmParamEnquiryAndConfig(0x01,0x02,0,0,0x02)`） | `docs/20260522_1700_piper_jointctrl_solved.md` |
| N3 | 再発性 | CAN TX がサイレント失敗、コマンドが届かない | USB-CAN 物理層の不調 | `ip -details -statistics link show can0` でエラーカウンタを確認 → USB-CAN アダプタを抜き差し | `docs/20260522_1700_piper_jointctrl_solved.md`（教訓 5） |

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
