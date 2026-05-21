# Piper ROS2 セットアップ進捗メモ

> Live Collaborative Drawing Robot System の第一段階
> 「Piper を ROS2 で動かすところまで」の作業ログ

---

## システム構成（確定済み）

| 項目 | 内容 |
|------|------|
| 作業用PC | Neousys Nuvo-10000 Series（産業用PC、x86_64） |
| OS | Ubuntu 22.04 LTS |
| ROS2 ディストリ | **Humble**（`piper_ros` の `humble` ブランチが公式対応のため選定） |
| ロボットアーム | AgileX Piper（6DOF、payload 1.5kg、作業半径 626.75mm） |
| 通信方式 | CAN 1Mbps |
| USB-CANアダプタ | bytewerk candleLight USB to CAN adapter (VID 1d50 / PID 606f) — `gs_usb` ドライバで動作 |
| 電源 | DC24V ACアダプタ（XT30コネクタ経由でアダプタ＆アームに分岐供給） |
| USB延長 | Alxum 5m USB 3.0 アクティブ延長ケーブル（内蔵Realtekハブ経由で問題なく認識） |

---

## 完了したステップ

### ✅ Step 1: ROS2 Humble インストール

```bash
# ロケール設定
sudo apt update && sudo apt install -y locales
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

# リポジトリ追加
sudo apt install -y software-properties-common
sudo add-apt-repository universe -y
sudo apt install -y curl
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
  | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

# 本体インストール
sudo apt update && sudo apt upgrade -y
sudo apt install -y ros-humble-desktop ros-dev-tools

# 環境変数を毎回読み込む
echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

**動作確認結果**: `talker` / `listener` のデモが正常動作。エラーなし。

---

### ✅ Step 2: CAN環境セットアップ

```bash
sudo apt install -y can-utils ethtool iproute2
```

USB-CANアダプタ単体での認識確認（直挿し）:

```
[ 6982.453382] CAN device driver interface
[ 6982.455779] gs_usb 1-4:1.0: Configuring for 1 interfaces
[ 6986.012243] usb 1-4: Product: candleLight USB to CAN adapter
[ 6986.012246] usb 1-4: Manufacturer: bytewerk
```

```
7: can0: <NOARP,ECHO> mtu 16 qdisc noop state DOWN mode DEFAULT group default qlen 10
```

→ ドライバ `gs_usb` ロード済み、`can0` インターフェース作成済み（state DOWN は正常）。

---

### ✅ Step 2.5: USBアクティブ延長ケーブル経由での認識確認

Alxum 5m USB 3.0 アクティブ延長ケーブル経由でも問題なし:

```
[ 7744.868534] usb 1-4: Product: USB2.1 Hub
[ 7744.868537] usb 1-4: Manufacturer: Generic
[ 7745.256112] usb 1-4.1: Product: candleLight USB to CAN adapter
[ 7745.261751] gs_usb 1-4.1:1.0: Configuring for 1 interfaces
```

延長ケーブル内蔵のRealtekハブ（VID 0bda）の先に CAN アダプタがぶら下がる構造。USB-CANは低帯域（USB 2.0 フルスピード）なので帯域は余裕。

---

### ✅ Step 3: Piper本体に電源・CAN接続、通信確認

#### 配線手順（実施済み）

1. ACアダプタはコンセントに挿さない状態で配線
2. USB-CANアダプタ緑ターミナルブロック → Piper側CANコネクタ
3. XT30電源分岐 → Piper本体の電源入力
4. ACアダプタ → USB-CANアダプタの電源入力
5. 最後にACアダプタをコンセントに挿入

→ Piperの緑ランプが点滅して起動完了。

#### CANインターフェース起動

```bash
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
```

→ `state UP, LOWER_UP` 確認。

#### 通信確認

```bash
candump can0
```

→ 大量のCANフレームが流れることを確認。**通信成功**。

#### ⚠️ 注意点（実際に遭遇したトラブル）

- Piper電源投入時にUSB再列挙が起きて`can0`が一旦消失。インターフェース番号が `5` → `10` に変わった
- USBが再認識された後、`can0` は `state DOWN` に戻っていたので、再度 `ip link set can0 up` でUP化必要

---

### ✅ Step 4: piper_sdk (Python) のインストールと動作確認

#### インストール

```bash
sudo apt install -y python3-pip
pip3 install python-can piper_sdk
```

#### 状態読み取りテスト（`read_state.py`）

```python
from piper_sdk import C_PiperInterface
import time

piper = C_PiperInterface("can0")
piper.ConnectPort()
time.sleep(0.5)

for i in range(20):
    state = piper.GetArmJointMsgs()
    print(f"[{i:02d}] Joint angles: {state.joint_state}")
    time.sleep(0.5)
```

→ 20回のジョイント状態取得に成功。SDKレベルで通信できることを確認。

---

### ✅ Step 4.5: piper_sdk で実機動作テスト

#### piper_sdk の重要API仕様（実機確認済み）

##### モード切替: `ModeCtrl(ctrl_mode, move_mode, move_spd_rate_ctrl, is_mit_mode)`

- `ctrl_mode`: 0x00=待機 / **0x01=CAN指令制御**
- `move_mode`: **0x00=MOVE P / 0x01=MOVE J（関節補間） / 0x02=MOVE L（直線補間）** / 0x03=MOVE C
- `move_spd_rate_ctrl`: 0〜100 (速度%)
- `is_mit_mode`: 0x00=位置速度モード

##### モーター有効化: `EnableArm(motor_num, enable_flag)`

- `motor_num`: 1〜6 = 個別、**7=全モーター**
- `enable_flag`: **0x02=使能**

##### ジョイント制御: `JointCtrl(j1, j2, j3, j4, j5, j6)`

- 単位: **0.001度** (例: 5度 = 5000)
- 可動範囲:
  - joint1: -150° 〜 +150°
  - joint2: **0° 〜 180°**（マイナス不可）
  - joint3: -170° 〜 **0°**（プラス不可）
  - joint4: -100° 〜 +100°
  - joint5: -70° 〜 +70°
  - joint6: -120° 〜 +120°

##### エンドエフェクタ制御: `EndPoseCtrl(X, Y, Z, RX, RY, RZ)`

- 位置 (X, Y, Z): 単位 **0.001mm** (例: 1cm = 10000)
- 回転 (RX, RY, RZ): 単位 **0.001度**、オイラー角表現
- 座標系: X=前方、Y=左方、Z=上方（ベース原点）

##### 状態取得

- `GetArmJointMsgs().joint_state.joint_{1〜6}` → 現在の各ジョイント角
- `GetArmEndPoseMsgs().end_pose.{X,Y,Z,RX,RY,RZ}_axis` → 現在のエンド姿勢

#### テスト履歴

##### ✅ Test 1: joint_1 のみ ±5度動作（`move_joint1_small.py`）

第1関節（ベース回転）のみを±5度動かす単一ジョイントテスト → **成功**

ただし **初回実行時は動かず、2回目で動いた**。これは「ModeCtrl送信後、Piper側が制御モードに切り替わる前にJointCtrlを送った」ことが原因と推測。

##### ✅ Test 2: 複数ジョイント同時動作 / うなずき動作（`nod_motion.py`）

joint_2 を +5度、joint_3 を -5度同時に動かす協調動作 → **成功**

##### ⚠️ Test 3: エンドエフェクタ制御 Z軸±1cm（`move_endpose_small.py`）

初期姿勢（ほぼゼロ姿勢）からは MOVE L が動作せず。
→ 原因: **特異点（singularity）**。アームが垂直に立った状態は逆運動学的に特異点に近い。

##### ✅ Test 4: ready pose v1（`goto_ready_pose.py`）

joint_2=60°, joint_3=-60° に移動して特異点回避 → **成功**

結果: エンドエフェクタ位置 X=231mm, Y=0mm, Z=438mm, RY=85°

その後 `move_endpose_small.py` を再実行 → **MOVE L 成功**

##### ⚠️ Test 5: 5cm四方の正方形描画（`draw_square.py`）

ready pose v1 から正方形を描こうとしたが、**+Y方向の動きで停止**。
→ 原因: **手首特異点（wrist singularity）**。joint_4=joint_5=joint_6=0° の状態は手首の3軸が同一線上に並ぶ典型的特異点。

##### ✅ Test 6: ready pose v2（`goto_ready_pose_v2.py`）

joint_1=-45° (腰を右に回す) + joint_5=30° (手首を傾ける) で **二重に特異点回避**。

結果: エンドエフェクタ位置 X=158mm, Y=-158mm, Z=392mm, RX=180°, RY=65°, RZ=135°

##### ✅ Test 7: 3cm四方の正方形描画（`draw_square_v2.py`）

ready pose v2 から正方形描画 → **成功**。
各waypointごとに到達誤差も確認しながら動作。

---

## 🎓 今日得た重要な知見

### 1. 初期化シーケンスの重要性

電源投入直後の初期姿勢は特異点に近いことが多い。**起動後は必ず ready pose を経由してから作業姿勢へ**移行する初期化フローが必要。

### 2. 特異点の種類

- **肩特異点**: アームが垂直に立った状態
- **手首特異点**: joint_4, joint_5, joint_6 が同一線上に並んだ状態
- **境界特異点**: アームが伸びきった/折り畳まれきった状態

ready pose は **両方の特異点から離れた姿勢**にする必要がある。joint_1 と joint_5 を 0° から離すことで二重の安全策になる。

### 3. ModeCtrl と JointCtrl/EndPoseCtrl のタイミング

`ModeCtrl` 送信後、Piper側で制御モードが切り替わるまでにラグがある。
→ **`time.sleep(0.5)` 程度の待機が必要**。それでも初回失敗することがあるため、リトライ機構が望ましい。

### 4. MOVE J と MOVE L の使い分け

- **MOVE J (0x01)**: ジョイント空間で補間。特異点を比較的気にせず動かせる。初期化や大きな移動向け
- **MOVE L (0x02)**: 直線軌道で動く。逆運動学を解くので特異点に弱い。**描画タスクには必須だが、特異点を避けた姿勢で実行する必要あり**

### 5. 推奨される ready pose

```
joint_1 = -45° (or +45°)  # 腰を回して肩特異点回避
joint_2 = 60°              # 肩を前傾
joint_3 = -60°             # 肘を曲げる
joint_4 = 0°
joint_5 = 30°              # 手首を傾けて手首特異点回避
joint_6 = 0°
```

このとき先端は X=158mm, Y=-158mm, Z=392mm 付近にある。

---

## 次のステップ（未実施）

### 🔲 Step 5: piper_ros (humble ブランチ) のビルド

```bash
mkdir -p ~/piper_ws/src && cd ~/piper_ws/src
git clone -b humble https://github.com/agilexrobotics/piper_ros.git

# 依存関係
sudo apt install -y \
  ros-humble-ros2-control \
  ros-humble-ros2-controllers \
  ros-humble-controller-manager \
  ros-humble-moveit

cd ~/piper_ws
colcon build
source install/setup.bash
```

---

### 🔲 Step 6: ros2 launch + RViz で動作確認

```bash
# CAN起動スクリプト
bash ~/piper_ws/src/piper_ros/can_activate.sh

# Piper起動
source ~/piper_ws/install/setup.bash
ros2 launch piper start_single_piper.launch.py can_port:=can0
```

RVizでアームのジョイント状態が表示されればOK。

---

## 全体ロードマップ（参考）

```
[Step 1] ROS2 Humble インストール        ✅ 完了
   ↓
[Step 2] CAN環境セットアップ              ✅ 完了
   ↓
[Step 2.5] USB延長ケーブル動作確認        ✅ 完了
   ↓
[Step 3] Piper本体に電源・CAN接続         ✅ 完了
   ↓
[Step 4] piper_sdk で状態読み取り         ✅ 完了
   ↓
[Step 4.5] piper_sdk で実機動作テスト     ✅ 完了 (joint/end-effector/square)
   ↓
[Step 5] piper_ros (humble) ビルド        🔲 次回ここから
   ↓
[Step 6] ros2 launch + RViz で動作確認
   ↓
[Step 7] MoveIt2 でモーションプランニング
   ↓
[Step 8] OpenCV + ArUco でカメラキャリブレーション
   ↓
[Step 9] 描画システム本体の実装（VLM、メモリ、生成、描画）
```

---

## 作成済みテストスクリプト一覧

`~/piper_test/` 配下:

| ファイル | 用途 |
|---------|------|
| `read_state.py` | ジョイント状態の読み取りのみ（動作なし） |
| `move_joint1_small.py` | joint_1 のみ ±5度動作 |
| `nod_motion.py` | joint_2/joint_3 協調うなずき動作 |
| `move_endpose_small.py` | エンドエフェクタ Z軸±1cm |
| `goto_ready_pose.py` | ready pose v1 (joint_1=0) |
| `goto_ready_pose_v2.py` | **ready pose v2 (joint_1=-45, joint_5=30) ← 推奨** |
| `draw_square.py` | 5cm四方の正方形描画 |
| `draw_square_v2.py` | **3cm四方の正方形描画（進捗表示付き）← 推奨** |

---

## メモ・注意事項

- **piper_ros の foxy ブランチは使わない**（Foxy は EOL 済み）
- **piper_ros の noetic ブランチは ROS1 用**（混同しないよう注意）
- **ベースプレート固定は早めに**（M5ボルト×4 で重量物 or 作業台に固定推奨）
- **CAN線とUSB線を長距離並行配線するとノイズの可能性あり**。気になる場合は `candump -e any` でエラーフレームを監視
- **再起動後は CAN インターフェースを毎回 up し直す必要あり**。`piper_ros` 同梱の `can_activate.sh` を使うのが楽
- **Piper電源投入時に USB が一瞬切れて can0 が DOWN に戻ることがある**。電源ON後はもう一度 `ip link show can0` で確認すべし
- **初回のModeCtrlは効かないことがある**。動かなかったらもう一度実行する習慣をつける
- **作業前は必ず ready pose v2 に移動してから本番動作を始める**
