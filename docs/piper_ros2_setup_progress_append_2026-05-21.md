## 2026-05-21 15:50 — ROS2 launch + RViz でPiper起動成功

### 実施したこと

- VS Code (snap版) のインストール
  ```bash
  sudo snap install code --classic
  ```
- 拡張機能インストール: Python, C/C++, YAML, XML Tools (Josh Johnson版)
  - ROS (Microsoft) は deprecated 表示でインストール不可 → 不要と判断
- ワークスペース作成と piper_ros クローン
  ```bash
  mkdir -p ~/piper_ws/src
  cd ~/piper_ws/src
  git clone -b humble https://github.com/agilexrobotics/piper_ros.git
  ```
- 依存関係インストール
  ```bash
  sudo apt install -y \
    ros-humble-ros2-control ros-humble-ros2-controllers \
    ros-humble-controller-manager ros-humble-moveit \
    ros-humble-xacro ros-humble-joint-state-publisher \
    ros-humble-joint-state-publisher-gui
  ```
- `rosdep install` 実行（`warehouse-ros-mongo` は不在で失敗するが描画用途では不要なので無視）
- `colcon build --symlink-install` 成功（8パッケージ）
  - `piper`, `piper_description`, `piper_msgs`, `piper_humble`
  - `piper_gazebo`, `piper_mujoco`
  - `piper_no_gripper_moveit`, `piper_with_gripper_moveit`
- 環境変数の永続化
  ```bash
  echo "source ~/piper_ws/install/setup.bash" >> ~/.bashrc
  ```
- launchファイル中身の精査（`start_single_piper.launch.py`, `start_single_piper_rviz.launch.py`, `display_xacro.launch.py`）
- 安全戦略を立ててから launch を起動
  - 先にPiperを 0° 姿勢に置き、joint_state_publisher_gui のスライダー初期値と一致させる
  - まず RViz 無し版で観測のみ起動 → トピック確認 → 停止 → RViz 有り版で起動

### 結果

- ✅ **`ros2 launch piper start_single_piper.launch.py`** RViz無し版で起動成功
  - `/piper_ctrl_single_node` 起動、`/joint_states_feedback` に 200Hz でデータ流入
  - Piper は意図せず動かないことを実機で確認
- ✅ **`ros2 launch piper start_single_piper_rviz.launch.py`** RViz有り版で起動成功
  - joint_state_publisher_gui ウィンドウ表示
  - RViz2 で Piper の3Dモデル表示
  - OpenGL 4.6 (GLSL 4.6) で正常レンダリング
  - Piperはスライダーが0°でPiper実機も0°のため動かず、安全
- ✅ piper_ros が立ち上げる ROS2 トピック構造を確認
  - `/joint_states_feedback`: フィードバック観測用
  - `/joint_states`: remap で指令入力扱い（後述）
  - `/end_pose`, `/end_pose_stamped`, `/arm_status`, `/joint_ctrl`, `/pos_cmd` などが存在

### つまずいた点

#### 1. 同一LAN上に別マシンの piper ノードが見えていた

- 起動した記憶がないのに `/piper_ctrl_single_node`, `/piper_control_manager`, `/piper_gui_server` が `ros2 node list` に出現
- `ps aux | grep piper` ではプロセスが見つからず混乱
- 原因: 同じROS2ネットワークに別PCがあって、そこで AgileXのGUIアプリ（MoveIt経由）が動いていた
- 対処: その別PCの電源を切る → 一時的にDDSキャッシュに残るがやがて消える
- **将来の対策**: `export ROS_DOMAIN_ID=42` などで自プロジェクト用に名前空間を分離する

#### 2. NVIDIAドライバ不整合で RViz2 起動不可

- `rviz2` が `Failed to create an OpenGL context. BadValue (integer parameter out of range)` で起動不能
- 原因: 過去の `apt upgrade` で 550系ドライバが「不要」扱いになりつつ、580系へ中途半端に移行していた
- `nvidia-smi` が `Driver/library version mismatch` を出していた
- 対処: PC再起動 → 新ドライバ（**Driver 595.71.05 / CUDA 13.2 / RTX 2000 Ada / 16GB VRAM**）が正常ロード
- 再起動後 `rviz2` 単体起動で OpenGL 4.6 を確認

#### 3. PC再起動後、CAN通信が回復しなかった

- USB-CANアダプタは `lsusb` で認識（`1d50:606f Geschwister Schneider CAN adapter`）
- `can0` は state UP, ERROR-ACTIVE だが RX/TX バイト数が 0
- candump で何も流れず、`read: Network is down` エラー
- 対処: **Piper本体の電源を切り USB-CAN も抜き差し** → 通信回復
  - 物理層の小さな接触不良 or 起動順序の問題と推定

#### 4. RViz2 単体起動時の TF 警告

- `No transform from [world] to [base_link]` が出るがエラーではない
- 原因: RViz2 単体だと `robot_state_publisher` が動かず TF が流れないため
- launch経由で起動すれば自動解消

### 学んだこと

#### 1. piper_ros の remap 設計は両刃の剣

- launchファイル内で `joint_ctrl_single` → `/joint_states` への remap が定義されている
- 結果として `joint_state_publisher_gui` が `/joint_states` に publish した値が **そのままPiper指令として実機に流れる**設計
- つまり「**RVizのスライダーを動かすと実機Piperが即座に動く**」
- 安全対策: launch起動前に Piperを 0° 姿勢に置き、GUIスライダー初期値と一致させる

#### 2. ROS2 のディスカバリ範囲は LAN 全体

- 同一LAN上の全ノードがデフォルトで自動発見される
- 別プロジェクトのノードが混ざって見えるとデバッグが混乱する
- `ROS_DOMAIN_ID` または `ROS_LOCALHOST_ONLY=1` で分離するのが定石

#### 3. DDS のディスカバリキャッシュ

- ノードが終了しても、しばらくは `ros2 node list` に残骸として表示されることがある
- 「`topic info` で Unknown topic と出るが `topic list` には残っている」場合は典型的な残骸

#### 4. NVIDIA ドライバの整合性は明示的に確認すべき

- `nvidia-smi` でバージョン不整合があると 3D描画系（RViz, Gazebo）が全滅する
- `apt upgrade` 後に `nvidia-smi` を実行して `Driver/library version mismatch` がないか確認する習慣を

#### 5. Piper 電源投入後の通信不能はまず電源とUSBの抜き差し

- 物理層のリセットで治ることが多い
- 30秒待つ（コンデンサ放電）と確実

### 次にやること

- 🔲 Step 7: MoveIt2 でモーションプランニング
  - `piper_no_gripper_moveit` を使う想定（描画はペンホルダー予定でグリッパー不要）
- 🔲 `ROS_DOMAIN_ID` を分離して Kachaka と共存しやすくする
- 🔲 VS Code 内で Claude Code への移行（Step 7 後半 or Step 9 描画システム実装のあたり）
- 🔲 Claude Code への引き継ぎ用 Markdown を別途用意（移行タイミングで作成）

ロードマップ Step 5, Step 6 を 🔲 → ✅ に更新
