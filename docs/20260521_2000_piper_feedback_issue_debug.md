# Piper フィードバック消失問題 — 調査ログ

> 日時: 2026-05-21 20:00頃 (JST)
> 関連既存ファイル: `piper_ros2_setup_progress.md`, `piper_ros2_setup_progress_append_2026-05-21_part2.md`
> ステータス: ✅ 解決 (2026-05-21 20:30、`modules/piper_feedback.py` で迂回)

## 実施したこと

朝のセッション (`piper_ros` + piper_sdk で実機動作確認できていた) と、夕方の Claude Code セッション (`~/draw_piper/`) で、共通して `piper_sdk.GetArmJointMsgs()` / `GetArmEndPoseMsgs()` が All 0 を返す症状が再現するか調査。

具体的には以下を実施:

- 現状の CAN 通信状態確認 (`candump can0`)
- `piper_sdk` のメソッドを使ったアーム状態取得 (`GetArmStatus`, `GetArmEnableStatus`, `GetArmCtrlCode151`)
- `MotionCtrl_2(installation_pos=0x01)` 送信テスト → 反映されないことを確認
- `EnablePiper()` (V2 SDK の高レベル enable) の呼び出し → True を返すが状態変わらず
- `piper_sdk` のバージョン確認 → 0.6.1 (PyPI最新)
- ファームウェアバージョン取得 → **`S-V1.8-2`** 判明

## 結果

### 確実な事実

| 項目 | 状態 |
|---|---|
| アーム電源 | ON、緑ランプ点滅中 |
| CAN 通信 | UP、1Mbps |
| 励磁状態 | 全6モーター True |
| Error Code | 0 (エラーなし) |
| ファームウェア | **S-V1.8-2** |
| piper_sdk バージョン | 0.6.1 (PyPI最新、これ以上アップグレードできない) |

### 観測する CAN ID

| ID 群 | 状態 | 内容 |
|---|---|---|
| `0x251-0x256` | ✅ 流れる | モーター高速診断 |
| `0x261-0x266` | ✅ 流れる | モーター低速診断 |
| `0x3A0-0x3A7` | ✅ 流れる | **正体不明** (SDKの `can_id.py` に未定義) |
| `0x2A1` (ARM_STATUS_FEEDBACK) | ❌ 出ない | 朝は出ていたはず |
| `0x2A5-0x2A7` (JOINT_FEEDBACK) | ❌ 出ない | 朝は出ていたはず |
| `0x2A2-0x2A3` (END_POSE_FEEDBACK) | ❌ 出ない | 朝は出ていたはず |
| `0x151` (CtrlCode feedback) | ❌ 出ない | `time stamp:0, Hz:0.0` |

### SDK の挙動

- `GetArmJointMsgs()` → 全関節 0 (実機が0°収納姿勢でも、製造誤差由来の微小値すら取れない)
- `GetArmEndPoseMsgs()` → X=Y=Z=0 (異常)
- `GetArmStatus()` → `time stamp:0, Hz:0.0` (該当フレームを一度も受信していない)
- `GetArmCtrlCode151()` → 同上
- `MotionCtrl_2(0x01, 0x01, 15, 0x00, 0x00, 0x01)` 送信 → `move_spd_rate_ctrl=50, installation_pos=0` のまま (SDKデフォルト値) → **送信内容が反映されていない**
- `EnablePiper()` → `True` を返すが、Control Mode は STANDBY のまま、状態変わらず

### 動作テスト

joint1 を +5度、joint2 を +30度 動かす指令を送ったが、**実機は意図通りに動かなかった**。
ただし「JointCtrl(0,0,0,0,0,0) 送信時に手で動かしていた姿勢から収納姿勢に戻った」現象は観測されたので、**何らかの指令は通っている**模様。フィードバックも制御フィードバックも来ない状態。

## つまずいた点

### 1. ファームウェア V1.8-2 と piper_sdk 0.6.1 の不整合疑い

PyPI 公開の最新 piper_sdk が 0.6.1 で、これより新しいバージョンが存在しない。
一方でアーム側のファームは V1.8-2 と比較的新しい。両者のプロトコル不整合の可能性が高い。

朝のセッション（同じ環境）で feedback が取れていた理由は不明。考えられる候補:
- 別マシン（Kachakaチーム）の純正AgileX GUI が動作していた時、何らかの「feedback有効化」コマンドを broadcast していて、その効果が残っていた
- 朝の作業中に何かのコマンドが「feedback有効化」を踏んだ → 夕方には効果が切れた
- ファーム自体が「過去N時間アイドルだったら feedback停止」のような省電力動作をしている

### 2. `MotionCtrl_2` の設定反映失敗

引数を渡しているのに、`GetArmCtrlCode151()` で確認すると SDK デフォルト値のまま。
これは「送信したCANフレームをアームが拒否している」または「アーム側で受け取ったがフィードバックを返していない」のどちらか。`0x151` のフレームが受信されていないので確認できない。

### 3. `EnablePiper()` の挙動が不明瞭

`True` を返すが、それが何を意味するか不明。少なくとも問題は解決しなかった。

## 学んだこと

### Piper の CAN フレーム構造 (実機観察ベース)

- **`0x2A` 系**: 古いプロトコルでの状態フィードバック (V1.8系では出ていない可能性)
- **`0x3A` 系**: 新しいプロトコルでの何か (V1.8で追加? SDKは未対応)
- **`0x251-256, 261-266`**: モーター単位の診断 (常時 broadcast されている)

### SDK のメソッド存在確認

```
EnableArm / DisableArm           # 関節モーター個別または全体の励磁制御
EnablePiper / DisablePiper       # 高レベルenable (詳細不明)
EnableFkCal / DisableFkCal       # 順運動学計算の有効/無効
EnableFilterAbnormalData / Disable...  # 異常データのフィルタ
GetPiperFirmwareVersion          # 「S-V1.8-2」が返ってくる
GetArmCtrlCode151                # 0x151 (制御コードフィードバック) の値
GetCurrentSDKVersion             # SDKバージョン取得
GetCurrentProtocolVersion        # プロトコルバージョン取得
GetCurrentInterfaceVersion       # インターフェースバージョン取得
```

### `MotionCtrl_2` の正しい引数 (SDK 0.6.1)

```python
MotionCtrl_2(
    ctrl_mode: 0|1|3|4|7 = 1,                   # 0x01=CAN制御
    move_mode: 0|1|2|3|4|5 = 1,                 # 0x01=MOVE J, 0x02=MOVE L
    move_spd_rate_ctrl: int = 50,               # 速度 % (1-100)
    is_mit_mode: 0|173|255 = 0,                 # 0x00=位置速度モード
    residence_time: int = 0,
    installation_pos: 0|1|2|3 = 0               # 0=未設定, 1=水平正装, 2=水平倒立, 3=垂直壁面
)
```

## 次にやること

### 優先度高: Claude Code 側で実施すべき

🔲 **piper_sdk の GitHub リポジトリで最新ブランチ調査**
- PyPI公開版 (0.6.1) より新しい開発版がないか
- ファームウェア V1.8系対応のブランチ・タグ・PRがないか

🔲 **piper_ros の `piper_ctrl_single_node.py` を精査**
- どんなコマンドを送って初期化しているか
- V1.8系で動かすための追加処理がないか
- 特に `ConnectPort` 後の前処理を確認:
  ```bash
  grep -n "MotionCtrl\|ModeCtrl\|EnableArm\|EnablePiper\|installation" \
    ~/piper_ws/src/piper_ros/src/piper/piper/piper_ctrl_single_node.py
  ```

🔲 **piper_ros 経由で起動した時に feedback が来るかテスト**
- `ros2 launch piper start_single_piper.launch.py` 起動
- `/joint_states_feedback` トピックの中身を観察 (effort が non-zero か)
- もし piper_ros 経由なら feedback が来るなら、piper_sdk 単体での使い方に何か欠けている

### 並行で確認

🔲 **`GetCurrentProtocolVersion()` と `GetCurrentInterfaceVersion()` を呼んで、現在のプロトコル状態を確認**

🔲 **`0x3A` 系の値が何を表すか解読**
- 励磁解除して手でジョイントを動かし、`0x3A` 系の値が変化するか観察
- もし関節角度フィードバックなら、SDK の `can_id.py` を書き換えて取得できるようになる

🔲 **AgileX サポートに問い合わせ**
- ファームウェア V1.8-2 で `0x2A` 系の状態フィードバックを有効化する方法
- piper_sdk の対応版バージョン

### 仮の運用 (もし問題解決まで時間がかかる場合)

🔲 **fixed position / feedback無しでの描画タスク設計**
- 「現在位置を取得しないと描画できない」前提を見直す
- 起動時の ready_pose は決め打ち姿勢で十分
- 描画中も「指令通りに動いた前提」で進める
- 失敗時のリカバリはセンサ情報 (カメラ) で検出する設計に変更

これは Claude Code 側の `v0.4` 設計書がすでに「state を持たない」ターン制設計なので、相性は良い。

---

## 解決 (2026-05-21 20:30)

### 原因確定: V1.8 firmware が feedback ID を +0x100 シフト

CAN バス観察で発見していた未知 ID `0x3A0–0x3A7` を「`0x2A0–0x2A7 + 0x100`」と仮定して payload を SDK の `0x2A*` 構造でデコードしたところ、**物理的に妥当な end pose と関節角が取得できた**:

```
0x3A2 → END_POSE_1:  X=249.277mm  Y=35.177mm
0x3A3 → END_POSE_2:  Z=56.819mm   RX=37.454deg
0x3A4 → END_POSE_3:  RY=0.074deg  RZ=1.236deg
0x3A5 → JOINT_12:    j1=-0.578deg j2=6.405deg
0x3A6 → JOINT_34:    j3=-22.381deg j4=13.496deg
0x3A7 → JOINT_56:    j5=-8.750deg (j6 は別問題で要調査)
```

つまり V1.8-2 ファームウェアは feedback の **broadcast ID を `+0x100` シフトしたが、payload 構造は同一**のまま。`piper_sdk` 0.6.1 は 0x2A* で listen しているため state を一切受信できない。

### 確認した周辺事実

- GitHub `agilexrobotics/piper_sdk` master 最新の `can_id.py` にも `0x3A` 系の定義は **無い**
- CHANGELOG.MD でサポート明記は V1.7-4 まで（V1.8 系は未対応）
- 0.6.1 リリース (2025-10-30) 以降の commit (2026-04-10 まで 4件) も `can_id` 関連には触れていない
- 公式 `piper_ros` ドライバ (`piper_ctrl_single_node.py`) を起動しても同症状 (`/joint_states_feedback` が全 0) → 修正は SDK 側で必要
- piper_ros の初期化シーケンス自体は極小 (`ConnectPort → EnableArm → MotionCtrl_2(0x01,0x00,50)`)、特別な hand-shake は無い

### 採用した対処

公式対応を待たず、`modules/piper_feedback.py` を新規追加して `0x3A*` を独自に listen:

- `python-can` で can0 に第2 socket を開く（socketcan は複数 reader 可）
- バックグラウンドスレッドで 0x3A2–0x3A7 を parse → 内部キャッシュ更新
- `Robot.connect()` で同時起動、`get_end_pose()` / `get_joints()` / `_refresh_orientation_cache()` はまず本リスナを参照
- piper_sdk アップデートで公式対応されたら自前モジュールを削除するだけで戻せる

### 未解決の残件

- `0x3A7` 後半 4 バイト (j6) の解読 — V1.8 で encoding が変わった可能性。XY 描画タスクには影響小なので一旦保留
- `0x3A0` (恒常的に broadcast されているが ID マッピング不明) の解析
- AgileX への正式 issue 提起 (firmware V1.8 で broadcast ID シフトの仕様文書化要求)

### 検証結果

```bash
$ venv/bin/python3 -c "
from modules.robot import Robot
r = Robot(mock=False); r.connect(enable_motors=False, settle_s=0.5)
print('joints:', r.get_joints())
print('end pose:', r.get_end_pose())
r.disconnect()"
[robot] V1.8 feedback listener ready
[robot] cached orientation (deg): RX=37.45, RY=0.07, RZ=1.24
joints: (-0.578, 6.405, -22.381, 13.496, -8.75, <j6_TBD>)
end pose: (249.277, 35.177, 56.819, 37.454, 0.074, 1.236)
```

### 副次的に発見・修正したバグ

`run_draw_test.py` の `--real` フラグが Robot に伝わっておらず、piper_sdk が venv に入っているだけで実機モードで動いていた（コマンドが送信されていた）。`Robot(mock=True/False)` の明示パラメータと、`run_draw_test.py` 側で `mock=(not args.real)` を渡すよう修正。

## メモ: コマンド集

### 環境準備
```bash
# CAN UP
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
```

### 状態取得スクリプト (テンプレ)
```bash
cat > /tmp/check_status.py << 'PYEOF'
from piper_sdk import C_PiperInterface
import time
p = C_PiperInterface('can0')
p.ConnectPort()
time.sleep(0.5)

print(f"Firmware: {p.GetPiperFirmwareVersion()}")
print(f"SDK ver:  {p.GetCurrentSDKVersion()}")
print(f"Proto:    {p.GetCurrentProtocolVersion()}")
print(f"Iface:    {p.GetCurrentInterfaceVersion()}")
print()
print(p.GetArmStatus())
print(f"Enable status: {p.GetArmEnableStatus()}")
PYEOF
python3 /tmp/check_status.py
```

### 励磁解除 (作業終了時)
```bash
python3 -c "
from piper_sdk import C_PiperInterface
import time
p = C_PiperInterface('can0')
p.ConnectPort()
time.sleep(0.5)
p.DisableArm(7, 0x01)
print('Disabled')
"
```

---

# 続報: 実機動作テスト (2026-05-22)

> ステータス: 🔶 一部解決・一部未解決
> CAN 物理層障害と feedback ID 不整合は解決。動作コマンド実行ブロックは未解決 → AgileX 純正 GUI / サポート案件。

## この日やったこと

feedback workaround 完成後、実機でアームを動かす段になり、JointCtrl を送ってもアームが動かない問題を追跡。

## 判明した原因と対処

### 1. ✅ マスター/スレーブ設定によるフィードバック ID シフト

`0x3A*` 系の正体が確定。`MasterSlaveConfig` (CAN ID 0x470) の `feedback_offset` によるもの。
このアームは**マスターアーム (示教入力臂, linkage_config=0xFA)** に設定されており、
feedback が `0x2A*` → `0x3A*` にシフトしていた。

対処: `MasterSlaveConfig(0xFC, 0x00, 0x00, 0x00)` 送信 + **電源再投入**で `0x2A*` に復帰。
- `linkage_config=0xFC` = 運動出力臂
- `feedback_offset=0x00` = フィードバック ID をデフォルトに戻す
- マスターモードから抜ける場合は電源再投入が必須 (`piper_set_slave.py` のコメントに明記)

これにより piper_sdk 標準の `GetArmJointMsgs()` / `GetArmEndPoseMsgs()` が正常値を返すように。
自前 workaround `modules/piper_feedback.py` は不要になる見込み (公式パスが使えるため)。

### 2. ✅ CAN バス物理層障害 (TX 不能)

実機テスト中、`SendCanMessage(SEND_MESSAGE_FAILED (100017))` が頻発。
`ip -details -statistics link show can0` で `can state ERROR-PASSIVE`、`error-pass` カウンタが
数秒で数万まで急増することを確認。

- **受信は完璧** (380万パケット、feedback 正常)
- **送信が壊滅** (ACK されず error-passive 即発)

原因: CAN 物理層の問題。セッション中の USB / コネクタ抜き差しで配線か終端が劣化したと推定。

対処: **USB-CAN アダプタの USB を抜き差し** + CAN 配線の再接続。
その後 `sudo ip link set can0 up type can bitrate 1000000` で再 up。
→ 連続 290 フレーム送信しても `error-pass 0` を維持、TX 完全回復。

教訓:
- `candump` に自分の送信フレームが見えても、それは socketcan のローカル TX エコーであり
  **実際にバス上で ACK され相手に届いた保証にはならない**。
- 送信の健全性は `ip -details -statistics link show can0` の `error-pass` / `bus-off` で確認する。

## ❌ 未解決: 動作コマンド実行ブロック

CAN TX 回復・feedback 正常化の後も、**JointCtrl を送ってもアームが関節を動かさない**。

確認済みの状態 (すべて正常):
- CAN バス healthy (`error-pass 0`)
- `EnablePiper()` OK、全6モータ励磁 (`EnableStatus [True]*6`)、保持トルクあり (手で確認)
- `Control Mode: CAN_CTRL`、`Mode Feed: MOVE_J`、`Arm Status: NORMAL`、`Error Code: 0`
- `MotionCtrl_2` / `ModeCtrl` / `EnableArm` / `MasterSlaveConfig` は効く (設定コマンドは通る)
- JointCtrl フレーム (0x155-0x157) は正しいデータでバス送出されている

排除した原因:
- CAN 物理層 (健全、error 0)
- 制御 ID オフセット (`0x155-157` / `0x165-167` / `0x175-177` の3系統に同時送信 → 全て無反応)
- `C_PiperInterface` (V1) と `C_PiperInterface_V2` の差 (両方とも不可)
- 連続 `MotionCtrl_2` + `JointCtrl` ループ (公式デモ `piper_ctrl_joint.py` 方式) → 不可
- 速度設定 (10% / 100% 両方)
- teaching モード残留・急停ラッチ (`MotionCtrl_1` で recover + exit drag-teach 送信済み)
- `MasterSlaveConfig` の `linkage_config` = `0xFC` / `0x00` (両方を健全バス + 電源再投入で試行)

→ piper_sdk から打てる手は出し尽くした。アーム本体の設定をホスト外から触る必要がある。

## 次にやること

🔲 **AgileX 純正 GUI でアーム設定を完全リセット**
- このアームを master モードにしたのは (おそらく Kachaka チームの) 純正 GUI
- 同じ GUI なら master/slave を含むアーム設定を確実に初期化できるはず

🔲 **AgileX サポートに問い合わせ**
- ファームウェア S-V1.8-2 でマスター/スレーブを完全解除し、直接 CAN 制御に戻す正規手順
- 「設定コマンドは通るが JointCtrl だけ実行されない」状態の原因

## メモ: 重要コマンド (続報分)

```bash
# CAN バスの送信健全性チェック (error-pass が増えなければ TX 健全)
ip -details -statistics link show can0 | grep -A1 re-started

# マスター/スレーブ設定 (CAN 0x470) -- 送信後は電源再投入
python3 -c "
from piper_sdk import C_PiperInterface_V2
import time
p = C_PiperInterface_V2('can0'); p.ConnectPort(); time.sleep(0.5)
p.MasterSlaveConfig(0xFC, 0x00, 0x00, 0x00)  # 0xFC=運動出力臂, offset全0
"
```
