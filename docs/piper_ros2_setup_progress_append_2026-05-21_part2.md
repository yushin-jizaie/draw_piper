## 2026-05-21 16:55 — pymoveit2 動作確認、GitHub プライベート化、Claude Code 移行準備

### 実施したこと

- 作業再開時の状態リカバリ
  - PC再起動後の手順を再確認: `can0` が DOWN になっていたので再UP
  ```bash
  sudo ip link set can0 type can bitrate 1000000
  sudo ip link set can0 up
  ```
  - Piper は 0° 収納姿勢のまま、CAN通信回復
- pymoveit2 のインストール
  ```bash
  sudo apt install -y ros-humble-pymoveit2
  ```
  - 注: `moveit_py` は Humble の apt には無く、`pymoveit2` が標準
- SRDFから設定値を抽出 (`piper_with_gripper_moveit/config/piper.srdf`)
  - `group_name`: `arm`
  - `base_link`: `base_link`
  - `tip_link`: `link6`
  - 関節: `joint1` 〜 `joint6`、グリッパー `joint7`
  - プリセット姿勢: `zero` (arm), `open` / `close` (gripper)
- pymoveit2 動作確認スクリプト
  - `~/piper_test/moveit_test_1.py`: 関節空間 `move_to_configuration` で
    `[0.0, 0.3, -0.3, 0.0, 0.0, 0.0]` (約 j2=17°, j3=-17°) に動かす
- ターミナル1で `ros2 launch piper_with_gripper_moveit demo.launch.py` 起動
  ターミナル2でスクリプト実行
- 実機を動かさない形での MoveIt 軌道計画・実行を確認
- 補助スクリプトの試作
  - `moveit_get_pose.py`: `MoveIt2.compute_fk()` で位置取得 → **失敗 (error code -21)**
  - `get_link6_tf.py`: tf2 で直接 `base_link → link6` を取得する代替 (未実行)
- GitHub プライベートリポジトリへのバックアップ
  - piper_ros の `.git` を削除して自前リポジトリに統合 (ライセンスは MIT で問題なし)
  ```bash
  rm -rf ~/piper_ws/src/piper_ros/.git
  cd ~/piper_ws && git init
  ```
  - `.gitignore` で `build/`, `install/`, `log/`, `__pycache__/` などを除外
  - `README.md` 作成
  - `gh` (GitHub CLI) で認証＆リポジトリ作成
  ```bash
  sudo apt install -y gh
  gh auth login
  gh repo create piper_ws --private --source=. --push
  ```
  - 既存のSSH形式 origin を一度削除し、HTTPS形式で再設定して push
  - 133 ファイル / 4.59MB が `yushin-jizaie/piper_ws` (Private) にpush成功
- Claude Code 引き継ぎ用 Markdown を作成 (`HANDOFF_TO_CLAUDE_CODE.md`)
  - 達成済みステップ、現在の状態、優先タスク、ハマりやすい点を網羅

### 結果

- ✅ `pymoveit2` で MoveIt2 を Python から呼び出して軌道計画・実行に成功
  ```
  [INFO] Planning to: [0.0, 0.3, -0.3, 0.0, 0.0, 0.0]
  [INFO] Joint states are available now
  [INFO] Result: SUCCESS
  ```
- ✅ GitHub プライベートリポジトリ `yushin-jizaie/piper_ws` を作成、初回 push 完了
- ✅ Claude Code への移行準備完了 (引き継ぎ Markdown 用意)
- ⚠️ demo.launch.py の RViz 表示で、オレンジ色 (current state) が0°のまま、軌道アニメーションだけ繰り返される現象あり (動作上の致命的問題ではない、要追加調査)
- ⚠️ `compute_fk()` が error code -21 で失敗 → tf2 直叩きで代替する方針

### つまずいた点

#### 1. `moveit_py` が Humble の apt に無い

- `sudo apt install ros-humble-moveit-py` → `Unable to locate package`
- 検索すると iron, rolling にはあるが Humble には無い
- 代替: `ros-humble-pymoveit2` (サードパーティだが公式 apt にあり)

#### 2. `MoveIt2.compute_fk()` が失敗

- `error code -21` (汎用 FAILURE) で FK 計算できず
- 加えて executor スレッドで `RCLError: wait set index ... out of bounds` がクラッシュ
- 終了時のクリーンアップタイミングの問題と推測
- 対策: tf2 で `base_link → link6` を直接取得する方が堅実

#### 3. GitHub への push 経路でちょっと混乱

- 最初に SSH 形式で `git remote add origin git@github.com:...` してしまった
- SSH 鍵未登録だったので `Permission denied (publickey)`
- 後で SSH 鍵を生成したが、その後 HTTPS 形式を試そうとして `remote origin already exists`
- `gh repo create` でリポジトリは作成できたが remote 追加が失敗
- 対処: `git remote remove origin` で削除 → HTTPS で `git remote add origin` し直し → push 成功
- 教訓: gh CLI を最初から使えば、リポジトリ作成・認証・remote 設定が一気にできた

#### 4. user.name / user.email を例文のままコピペして設定してしまった

- 一旦 `your-github-username` / `your-email@example.com` で設定してしまった
- 実コミット前に気づいて修正 (`yushin-jizaie` / `yushin.suzuki@jizaie.co.jp`)
- 教訓: コマンド例にプレースホルダを入れた時は意識して書き換える

### 学んだこと

#### 1. pymoveit2 の単位系 (重要)

| 概念 | piper_sdk | pymoveit2 |
|---|---|---|
| 関節角度 | 0.001度 (整数) | ラジアン (float) |
| 位置 | 0.001mm (整数) | メートル (float) |
| 姿勢 | オイラー角 (0.001度) | クォータニオン (xyzw) |

描画システムでは両方を行き来する必要がある → ヘルパー関数を用意するべき。

#### 2. demo.launch.py は Fake コントローラ

- `MoveItConfigsBuilder().to_moveit_configs()` + `generate_demo_launch()` のシンプル構造
- 内部で fake_components を使う、実機には繋がらない
- 描画用途では別の launch (実機統合) が必要

#### 3. piper_moveit.launch.py は実機向きじゃない

- `use_sim_time: True` がついている
- move_group と RViz のみで、コントローラー接続なし
- → これを使うなら追加設定が必要

#### 4. 実機統合の現実的な選択肢

- 公式の `piper_moveit.launch.py` を直す
- 自前 launch (piper_ctrl_node + move_group) を書く
- **pymoveit2 で計画 → JointTrajectory を取得 → piper_sdk に流す軌道再生レイヤーを自前で書く** ← これが一番シンプル

#### 5. GitHub 連携は `gh` CLI が最速

- `sudo apt install gh` → `gh auth login` (ブラウザ認証) → `gh repo create --private --source=. --push`
- 認証・リポジトリ作成・remote 設定・初回 push が一発
- SSH 鍵を手動で GitHub に登録する手間も無くなる (gh が自動でやってくれるオプションあり)

### 次にやること

- 🔲 Step 7-B (実機統合): pymoveit2 → piper_sdk 軌道再生レイヤーを書く
  - 計画結果の JointTrajectory を取得して、各 waypoint を piper_sdk.JointCtrl で順次送信
  - 時間軸の補間と速度プロファイル維持を考える
- 🔲 demo.launch.py のオレンジ色更新の謎を解明 (joint_state_publisher と Fake controller の競合?)
- 🔲 Claude Code に作業を移し、コーディング中心の作業に切り替え
- 🔲 piper_test/ をGitHub対象に含めるか別管理にするか決める
- 🔲 ROS_DOMAIN_ID 分離 (Kachaka と共存しやすくする)

ロードマップ Step 7 は ⚠️ 進行中のまま (実機統合が未完)
