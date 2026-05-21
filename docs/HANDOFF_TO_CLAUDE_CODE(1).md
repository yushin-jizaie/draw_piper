# Claude Code 引き継ぎドキュメント (v0.4 対応版)

> Live Collaborative Drawing Robot System プロジェクトの作業を、
> Claude（チャット版）から Claude Code（VS Code拡張）に引き継ぐためのメモ
> 初版作成日: 2026-05-21
> 改訂: 2026-05-21 17:57 (JST) — v0.4 設計変更を反映

---

## このドキュメントの目的

Claude Code のセッションを開始したら、まずこのファイルを読んでください。
プロジェクトの背景、現状、次にやるべきこと、注意点が書かれています。

**重要**: 本ファイルは初版から大幅に方針が変わっています。
旧版で挙げられていた「MoveIt2 → 実機経路」「demo.launch.py のRViz表示の謎」は
**全て v0.4 で無効化**されています。本ファイルの新方針に従ってください。

---

## ⚠️ 設計が v0.3 → v0.4 に変わりました

スケジュール（5日のロス）の都合で、システム全体をシンプル化しました。

**必読**: プロジェクトナレッジの **`20260521_1757_drawing_system_v04_design.md`** が現在の正本です。
旧設計の `Live Collaborative Drawing Robot System` (v0.3) は **概念だけ参考**にする程度で。

### v0.4 で変わった主な点

| 観点 | v0.3 (旧) | v0.4 (新) |
|---|---|---|
| 動作モデル | 常時観察・反射的介入 | **ターン制（2分サイクル）** |
| VLM の役割 | 構図批評・芸術判断 | **ユーザ意図予測のみ** |
| Reflective Memory | あり（DB＋ベクトル検索） | **削除** |
| OpenCV 学習 | あり（pen offset補正等） | **削除**（固定パラメータ） |
| Reflection Layer | あり（描画後の自己批評） | **削除** |
| 軌道再生 | MoveIt2 | **piper_sdk 直叩き** |
| 開始トリガー | （未定義） | **キーボード Enter** |
| キャプチャ | 連続観察 | **median合成（10枚/2秒）** |
| 画像生成プロンプト | 美術的指示OK | **構造的指示のみ**（線画） |

---

## プロジェクト概要 (v0.4)

人間と一緒に透明アクリル板に絵を描く協調描画システム。

**ターン制で動作する**:

1. ユーザが開始ボタン（Enter）を押す
2. 30秒後にWEBカメラで静止画キャプチャ（median合成）
3. VLM がユーザの描画意図を予測
4. その予測から生成プロンプトを構築
5. SDXL Turbo + Lineart ControlNet で線画生成
6. OpenCV でベクトル化、ロボット軌道に変換
7. ロボットアームが実際に描く
8. 描画中、並行して次サイクルの ②〜⑥ を準備
9. 2分経過で現ストローク完了後に停止、次サイクルへ

各サイクル独立、状態を持たない。

注: ロボットアームの**開始地点が毎回違う場所**になるように空白領域を優先（難しければランダム）。

---

## 現在のセットアップ

| 項目 | 内容 |
|------|------|
| 作業PC | Neousys Nuvo-10000 Series (Intel i7-14700, NVIDIA RTX 2000 Ada 16GB) |
| OS | Ubuntu 22.04 LTS |
| ROS2 | Humble |
| NVIDIA Driver | 595.71.05 / CUDA 13.2 |
| ロボット | AgileX Piper 6DOF（CAN 1Mbps、USB-CANアダプタ接続）|
| 環境変数 | `source /opt/ros/humble/setup.bash` と `source ~/piper_ws/install/setup.bash` を `.bashrc` で設定済み |

---

## 達成済みのステップ

```
[Step 1] ROS2 Humble インストール        ✅
[Step 2] CAN環境セットアップ              ✅
[Step 3] Piper電源・CAN接続               ✅
[Step 4] piper_sdk で実機動作テスト       ✅
[Step 5] piper_ros (humble) ビルド        ✅
[Step 6] ros2 launch + RViz 表示          ✅
[Step 7] MoveIt2 でモーションプランニング 🚫 v0.4 で破棄
   - pymoveit2 は調査・インストール済みだが、本プロジェクトでは使わない
   - 平面描画には piper_sdk.EndPoseCtrl で十分（v0.4設計書参照）
```

---

## v0.4 で新しく着手するステップ

詳細は `20260521_1757_drawing_system_v04_design.md` の「次にやること」を見てください。要約のみ:

```
[Step A] piper_sdk ベース軌道再生 (robot.py)
[Step B] ArUco キャリブレーション
[Step C] 線画生成パイプライン (SDXL Turbo + ControlNet)
[Step D] ベクトル化パイプライン (OpenCV)
[Step E] 単体テストを通したパイプライン結合
[Step F] median合成キャプチャ + Enter トリガー
[Step G] オーケストレータ統合
```

### 推奨着手順序

```
Step A (robot.py)
   + Step C (画像生成)   並行
   + Step D (ベクトル化) 並行
       ↓
Step E (パイプライン結合)
       ↓
Step B (キャリブ) ← カメラが届いたら
       ↓
Step F (median + Enter)
       ↓
Step G (オーケストレータ)
       ↓
[統合動作確認]
```

着手の入り口として一番リターンが大きいのは **Step A + Step C** の並行スタート:

- Step A (robot.py) は既存の `draw_square_v2.py` を一般化するだけなので早い
- Step C (画像生成) は VRAM 実測が必要で、ハマるとシステム全体の前提が崩れるので早めに不確実性を潰したい

---

## ハマりやすいポイント・注意事項

### Piper / CAN 周り

- **再起動後は必ず `can0` を up し直す**:
  ```bash
  sudo ip link set can0 type can bitrate 1000000
  sudo ip link set can0 up
  ```
  または `bash ~/piper_ws/src/piper_ros/can_activate.sh`
- **Piper電源投入時に USB再列挙が起きて can0 番号が変わる**ことがある
- **初回の ModeCtrl が効かないことがある** → リトライする習慣を
- **作業前は必ず ready pose v2 に移動してから本番動作を始める**
  - 推奨 ready pose: j1=-45°, j2=60°, j3=-60°, j4=0°, j5=30°, j6=0°
  - これで肩特異点と手首特異点の両方を回避

### VRAM 16GB の制約

VLM (Qwen2.5-VL-7B INT4) + 画像生成 (SDXL Turbo + Lineart ControlNet) を同時に乗せる。
合計 17〜20GB と理論上はオーバーするが、ControlNet を CPU offload するなどで吸収する想定。

OOM が出た場合の対処順序:
1. ControlNet を CPU offload
2. SDXL Turbo を SD 1.5 + ControlNet に変更（〜6GB）
3. VLM を Qwen2.5-VL-3B INT4 に変更（〜2GB）
4. それでも厳しければモデルスワップ方式へ移行

### piper_sdk 直叩き方針

v0.4 では MoveIt2 を使わず piper_sdk の `EndPoseCtrl` で軌道を再生する。

- 単位: 位置 0.001mm、回転 0.001度
- 平面描画なら Z固定、回転固定でOK（板に垂直なペン姿勢）
- 描画範囲は ready pose v2 周辺の **安全ゾーンに限定**することで特異点を事前回避
- ペンアップ/ダウンは Z軸を 5〜10mm 上下させて表現

### ネットワーク

- 同一LANに Kachaka（別プロジェクト）が居て、ROS2 ノードが大量に見える
- 描画プロジェクト用に `export ROS_DOMAIN_ID=42` などで名前空間分離するのがおすすめ（未実施）

### NVIDIAドライバ

- 過去に `apt upgrade` で 550→580 系の中途半端な状態になり RViz が起動不能になった事例あり
- 現在は 595.71.05 で動作中
- `apt upgrade` 後は `nvidia-smi` で `Driver/library version mismatch` がないか確認

---

## ファイル構成

### 既存（Piper関連）

```
~/piper_ws/                                  # ROS2 ワークスペース（v0.4ではあまり使わない）
├── src/
│   └── piper_ros/                           # AgileX公式のROS2パッケージ群
│       ├── src/piper/                       # メインの通信ノード
│       ├── src/piper_description/           # URDF
│       ├── src/piper_with_gripper_moveit/   # MoveIt設定（v0.4では使わない）
│       └── src/piper_no_gripper_moveit/     # 同上
└── install/                                 # colcon build 成果物

~/piper_test/                                # 自前のテストスクリプト置き場
├── read_state.py                            # Piper状態読み取り
├── move_joint1_small.py                     # 関節1のみ±5度
├── nod_motion.py                            # うなずき
├── goto_ready_pose.py                       # ready pose v1
├── goto_ready_pose_v2.py                    # ready pose v2 ← 推奨
├── draw_square.py                           # 5cm正方形
└── draw_square_v2.py                        # 3cm正方形(誤差表示) ← v0.4のrobot.pyの元
```

### v0.4 で新規作成するもの

```
~/draw_piper/                                # 新規プロジェクト
├── orchestrator.py                          # メインループ
├── modules/
│   ├── trigger.py                           # KeyboardTrigger / 将来 SerialTrigger
│   ├── camera.py                            # median合成キャプチャ
│   ├── vlm.py                               # Qwen2.5-VL
│   ├── prompt_builder.py                    # 意図→生成プロンプト
│   ├── image_gen.py                         # SDXL Turbo
│   ├── vectorizer.py                        # OpenCV
│   ├── start_point.py                       # 空白領域検出
│   ├── trajectory.py                        # ベクトル→軌道
│   └── robot.py                             # piper_sdk ラッパ
├── calibration/
│   ├── aruco_calibrate.py
│   └── camera_to_robot.yaml
└── logs/
    └── YYYYMMDD_HHMMSS/                     # サイクルごとの記録
```

---

## 環境立ち上げ手順（毎回必要）

```bash
# 1. CAN を up（PC再起動後は必須）
sudo ip link set can0 type can bitrate 1000000 2>/dev/null
sudo ip link set can0 up

# 2. CAN通信が来ているか確認
timeout 3 candump can0   # フレームが流れればOK

# 3. Piper の現状姿勢を確認
python3 ~/piper_test/read_state.py

# 4. (必要に応じて) ready pose に移動
python3 ~/piper_test/goto_ready_pose_v2.py
```

ROS2 関連は `.bashrc` で source 済み。ただし v0.4 では `ros2` はほとんど使わない。

---

## Claude Code への依頼事項

1. **このファイルを読んだら**、まずユーザに「読み込みました、現状こうですね」と
   現状を1段落で要約して見せる。理解の齟齬がないか確認。

2. **v0.4 設計書 (`20260521_1757_drawing_system_v04_design.md`) も読む**。
   そちらが正本。本ファイルは導入とハマりどころ集。

3. **すぐに作業に取り掛からない**。Step A から G のどれから始めるかを
   ユーザと相談し、合意を取ってから着手する。

4. **実機を動かすコードを実行する前**は必ずユーザに確認を取る。
   特にready pose以外の姿勢に居る時は、いきなり動かさない。

5. **ファイル編集の規約**:
   - テストスクリプトは `~/piper_test/` 配下に
   - **v0.4 の描画システム本体は `~/draw_piper/` 配下に新規作成**
   - ROS2 パッケージとしては組まなくてよい（v0.4 ではROS2を直接は使わない）

6. **進捗ログの更新**: 区切りごとに、
   プロジェクトナレッジ規約に従って `YYYYMMDD_HHMM_テーマ.md` を生成する。
   ルールは `project_instructions.md` を参照。
   既存ファイルを書き換えるのではなく、新ファイルとして追記する運用。

---

## 既知の未解決事項 (v0.4 時点)

### 確定済み（v0.4で決着）
- ~~demo.launch.py 実行時の Fake コントローラ挙動~~ → MoveIt2を使わないので不要
- ~~実機統合launchの作成~~ → piper_sdk直叩きで対処
- ~~pymoveit2 → piper_sdk 軌道再生レイヤー~~ → MoveIt2自体不使用

### 残っている課題
- [ ] ROS_DOMAIN_ID の分離（Kachaka と共存しやすくする）
- [ ] USBカメラ機種選定・購入
- [ ] ペンホルダーの設計（グリッパーの代替）
- [ ] 描画対象（透明アクリル板）の固定方法
- [ ] base_link と作業台/アクリル板の幾何学的関係（ArUco キャリブレーションで確立予定）
- [ ] SDXL Turbo + Qwen2.5-VL-7B INT4 + Lineart ControlNet の同時常駐可否（実測待ち）

---

## 参考リンク

- piper_sdk: https://github.com/agilexrobotics/piper_sdk
- piper_ros (humble branch): https://github.com/agilexrobotics/piper_ros/tree/humble  ← v0.4ではほぼ未使用
- Qwen2.5-VL: https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct
- SDXL Turbo: https://huggingface.co/stabilityai/sdxl-turbo
- ControlNet (Lineart): https://huggingface.co/lllyasviel/control_v11p_sd15_lineart
