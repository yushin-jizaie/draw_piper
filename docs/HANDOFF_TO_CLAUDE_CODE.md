# Claude Code 引き継ぎドキュメント

> Live Collaborative Drawing Robot System プロジェクトの作業を、
> Claude（チャット版）から Claude Code（VS Code拡張）に引き継ぐためのメモ
> 作成日: 2026-05-21

---

## このドキュメントの目的

Claude Code のセッションを開始したら、まずこのファイルを読んでください。
プロジェクトの背景、現状、次にやるべきこと、注意点が書かれています。

---

## プロジェクト概要

人間と一緒に透明アクリル板に絵を描く協調描画システム。

- ロボットアーム **AgileX Piper**（6DOF）を ROS2 で制御
- カメラで描画を観察し、VLM で美的解釈、生成モデルで局所構造を生成、MoveIt2 + OpenCV で実機を動かす
- 詳細設計は **プロジェクトナレッジ** の `Live Collaborative Drawing Robot System` を参照
- 進捗ログは **プロジェクトナレッジ** の `piper_ros2_setup_progress.md` を参照

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

## 達成済みのステップ（詳細はナレッジファイル参照）

```
[Step 1] ROS2 Humble インストール        ✅
[Step 2] CAN環境セットアップ              ✅
[Step 3] Piper電源・CAN接続               ✅
[Step 4] piper_sdk で実機動作テスト       ✅
[Step 5] piper_ros (humble) ビルド        ✅
[Step 6] ros2 launch + RViz 表示          ✅
[Step 7] MoveIt2 でモーションプランニング ⚠️ 進行中
   - pymoveit2 のインストール完了 (apt: ros-humble-pymoveit2)
   - demo.launch.py 起動して MoveIt + RViz 動作確認済み
   - Pythonから move_to_configuration で軌道計画→実行 SUCCESS
   - ただし Fake コントローラなので実機は動かない
[Step 8] OpenCV + ArUco キャリブレーション 🔲
[Step 9] 描画システム本体実装             🔲
```

---

## 次にやるべきこと（優先順）

### 1. (継続調査) demo.launch.py での RViz 表示の謎

ユーザの観察:
- `moveit_test_1.py` を実行すると Result: SUCCESS だが、
- RView上のオレンジ（current state）は ホームポジション（0°）のまま
- 「目標に向かってアニメーション」だけが繰り返し表示される

これは Planned Path のアニメーションが描画されているだけで、
本来の「Fake コントローラが実際にジョイント状態を更新する」が
起きていない可能性がある。

**確認手順**:
```bash
# demo.launch.py 起動中に、別ターミナルで
ros2 topic echo /joint_states --once
# moveit_test_1.py 実行後の値が反映されているか確認
ros2 topic info /joint_states --verbose
# Publisherが誰なのか確認（複数Publisherが競合している可能性）
```

ただし、これは描画システム本体には直接影響しない調査なので、深追いせず、
**Step 7-B（実機統合）に進む方が優先度が高い**。

### 2. (本命) MoveIt → 実機 Piper への経路を作る

現状の問題:
- `demo.launch.py` は Fake コントローラ → 実機が動かない
- `start_single_piper_rviz.launch.py` は piper_ctrl_node 起動 → 実機が動くが、MoveIt無し
- これら2つを統合した launch ファイルが必要

**選択肢A: 既存の `piper_moveit.launch.py` を修正**
- piper_with_gripper_moveit/launch/piper_moveit.launch.py を見ると
- `use_sim_time: True` がついているので実機向きじゃない
- これを実機向けに直す

**選択肢B: 自前の統合 launch ファイルを書く**
- piper_ctrl_node（実機通信）+ move_group（計画）+ rviz
- joint_states の publish 元と sub 先の競合を整理する必要あり

**選択肢C: pymoveit2 で計画結果（JointTrajectory）を受け取り、自前で piper_sdk に流す**
- move_group は demo.launch.py のままでも可
- Python 層で「計画 → JointTrajectory取得 → piper_sdk.JointCtrl で時間刻みに送信」
- これが描画システムでも一番使いやすい構造

ユーザの希望: Cで進める想定。
`pymoveit2.MoveIt2.compute_cartesian_path()` などで軌道を取得し、
piper_sdk経由で実機に流すワーカーを作る。

### 3. (並行可) Step 8 カメラキャリブレーション

USBカメラが用意できれば並行で進められる。
OpenCV + ArUco マーカーで「カメラ画像座標 → アームのbase_link座標」変換を確立する。

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

### MoveIt / pymoveit2 周り

- **pymoveit2 の単位はラジアン**（piper_sdk は 0.001度なので注意）
- **pymoveit2 の位置はメートル**（piper_sdk は 0.001mm）
- **姿勢はクォータニオン**（piper_sdk はオイラー角）
- 変換ヘルパーが必要

### piper_ros のトピック構造

| トピック | 用途 |
|---|---|
| `/joint_states_feedback` | Piper → ROS2、観測フィードバック (200Hz) |
| `/joint_states` | ROS2 → Piper、指令入力 (launch内のremap) |
| `/end_pose`, `/end_pose_stamped` | エンドエフェクタ位置 |
| `/arm_status` | アーム全体ステータス |
| `/joint_ctrl`, `/pos_cmd` | 他の指令経路 |

### ネットワーク

- 同一LANに Kachaka（別プロジェクト）が居て、ROS2 ノードが大量に見える
- 描画プロジェクト用に `export ROS_DOMAIN_ID=42` などで名前空間分離するのがおすすめ（未実施）

### NVIDIAドライバ

- 過去に `apt upgrade` で 550→580 系の中途半端な状態になり RViz が起動不能になった事例あり
- 現在は 595.71.05 で動作中
- `apt upgrade` 後は `nvidia-smi` で `Driver/library version mismatch` がないか確認

---

## ファイル構成

```
~/piper_ws/                                  # ROS2 ワークスペース
├── src/
│   └── piper_ros/                           # AgileX公式のROS2パッケージ群
│       ├── src/piper/                       # メインの通信ノード
│       ├── src/piper_description/           # URDF
│       ├── src/piper_with_gripper_moveit/   # MoveIt設定 (今使ってる)
│       └── src/piper_no_gripper_moveit/     # グリッパー無しMoveIt設定 (未使用)
└── install/                                 # colcon build 成果物

~/piper_test/                                # 自前のテストスクリプト置き場
├── read_state.py                            # Piper状態読み取り
├── move_joint1_small.py                     # 関節1のみ±5度
├── nod_motion.py                            # うなずき
├── goto_ready_pose.py                       # ready pose v1
├── goto_ready_pose_v2.py                    # ready pose v2 ← 推奨
├── draw_square.py                           # 5cm正方形
├── draw_square_v2.py                        # 3cm正方形(誤差表示) ← 推奨
├── moveit_test_1.py                         # pymoveit2 関節空間テスト ← 動作確認済み
├── moveit_get_pose.py                       # FK で位置取得 (compute_fk が失敗、要修正)
└── get_link6_tf.py                          # tf2 で位置取得 (未実行、これを使う方向)
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

ROS2 関連は `.bashrc` で source 済みなので、新しいシェルを開くだけで `ros2` コマンドが使えるはず。

---

## SRDF からの設定情報（pymoveit2用）

```python
# pymoveit2 初期化に使う情報
joint_names = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"]
base_link_name = "base_link"
end_effector_name = "link6"   # 注: グリッパー先端は link7 だが、アーム本体としては link6
group_name = "arm"

# プリセット姿勢
# group_state "zero" (arm): すべて 0
# group_state "open" / "close" (gripper)
```

ジョイントの可動範囲（piper_sdk で確認済み、ラジアンに換算済み）:
- joint1: ±150° (±2.618 rad)
- joint2: 0° ~ 180° (0 ~ 3.142 rad)  ※マイナス不可
- joint3: -170° ~ 0° (-2.967 ~ 0 rad)  ※プラス不可
- joint4: ±100° (±1.745 rad)
- joint5: ±70° (±1.222 rad)
- joint6: ±120° (±2.094 rad)

---

## Claude Code への依頼事項

1. **このファイルを読んだら**、まずユーザに「読み込みました、現状こうですね」と
   現状を1段落で要約して見せる。理解の齟齬がないか確認。

2. **すぐに作業に取り掛からない**。次にやるべきことを上の優先順から
   ユーザと相談し、合意を取ってから着手する。

3. **実機を動かすコードを実行する前**は必ずユーザに確認を取る。
   特にready pose以外の姿勢に居る時は、いきなりMoveItで動かさない。

4. **ファイル編集の規約**:
   - テストスクリプトは `~/piper_test/` 配下に
   - 描画システム本体は別ディレクトリ（例: `~/draw_piper/`）に分ける想定
   - ROS2パッケージとして組むなら `~/piper_ws/src/draw_piper/` に作る

5. **進捗ログの更新**: 区切りごとに、
   プロジェクトナレッジの `piper_ros2_setup_progress.md` に追記する形のMarkdownを生成する。
   ルールは別途プロジェクトの `project_instructions.md` を参照。

---

## 既知の未解決事項

- [ ] demo.launch.py 実行時、 Fake コントローラがオレンジ更新しているか不明
- [ ] ROS_DOMAIN_ID の分離（Kachaka と共存しやすくする）
- [ ] 実機統合launchの作成 / または pymoveit2 → piper_sdk 軌道再生レイヤー
- [ ] カメラ機種選定（USBカメラ準備）
- [ ] ペンホルダーの設計（グリッパーの代替）
- [ ] 描画対象（透明アクリル板）の固定方法
- [ ] base_link と作業台/アクリル板の幾何学的関係（ArUco キャリブレーションで確立予定）

---

## 参考リンク

- piper_sdk: https://github.com/agilexrobotics/piper_sdk
- piper_ros (humble branch): https://github.com/agilexrobotics/piper_ros/tree/humble
- pymoveit2: aptパッケージ `ros-humble-pymoveit2`
- MoveIt2 公式: https://moveit.picknik.ai/
