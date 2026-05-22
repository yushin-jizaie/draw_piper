# Piper JointCtrl 動作不能問題 — 完全解決ログ

> 日時: 2026-05-22 17:00頃 (JST)
> ステータス: ✅ **完全解決**
> 関連既存ファイル: `20260521_2000_piper_feedback_issue_debug.md`

---

## ⭐ 結論先出し: 解決手順

ファームウェア S-V1.8-2 のアームで JointCtrl が効かない問題は、以下の手順で完全解決した:

1. **Master モード残留を解除**: `MasterSlaveConfig(0xFC, 0x00, 0x00, 0x00)` 送信 + 電源完全リセット
2. **AgileX 純正 GUI (Piper_sdk_ui) の `Config Init` ボタンを押す**
3. これで JointCtrl が完全に効くようになる

**`Config Init` が最後のピース**だった。ホスト側 SDK 単体ではこの「Config Init 相当」のコマンドが送れない（または送り方が分からない）。GUI を経由するのが現状の唯一の道。

---

## 実施したこと（時系列）

### 13:00 ~ 15:00 - 状態調査と仮説検証
- ファームウェア確認: `S-V1.8-2`
- piper_sdk バージョン: 0.6.1 (PyPI 最新)
- `GetArmJointMsgs()` / `GetArmEndPoseMsgs()` が全部 0
- candump で `0x2A` 系不在、`0x3A` 系出現を確認

### 15:00 ~ 15:30 - 仮説1: ファームと SDK の不整合
- `EnablePiper()` を試したが効果なし
- `MotionCtrl_2(installation_pos=0x01)` を試したが反映されず
- piper_sdk のアップグレード余地なし（0.6.1 が最新）

### 15:30 ~ 16:00 - AgileX 純正 GUI セットアップ
- `git clone https://github.com/agilexrobotics/Piper_sdk_ui.git`
- venv セットアップ
- GUI 起動成功

### 16:00 ~ 16:10 - GUI で初期状態確認
- フィードバックは取れる (`Read End Pose` で X=56mm, Z=216mm が表示)
- マスター/スレーブは Slave 表示
- ただし**JointCtrl は GUI からも効かない**

### 16:10 ~ 16:30 - GUI で Master/Slave を試しに切り替えてしまう
- ドロップダウンで Master → Slave と切り替え
- これが原因で内部状態が壊れる
- `Can fps: 0` になり通信途絶

### 16:30 ~ 16:45 - 復旧作業
- 電源完全リセット (AC + USB 抜いて 30秒待つ)
- `MasterSlaveConfig(0xFC, 0x00, 0x00, 0x00)` を Python から送信
- フィードバック (`0x2A` 系) が復活

### 16:45 ~ 17:00 - 動作テストするも JointCtrl 効かず
- Slave モード復帰、フィードバック完璧
- `Control Mode: CAN_CTRL`、励磁OK、エラー0
- でも JointCtrl で実機が動かない
- piper_ros 経由でも同じ結果 → ホスト側の問題ではない確定

### 17:00 - **GUI の `Config Init` ボタンを発見・実行** ⭐
- ログに各モーターの角度制限と速度制限が表示される
- これにより**何かの内部状態がリセットされた様子**

### 17:00 - 動作テスト成功 🎉
- joint1 +30度の指令 → 実測 29.96度
- 戻し指令 → 実測 0.00度
- **JointCtrl が完全に効く状態に復旧**

---

## 確立した完全リカバリ手順

アームが「JointCtrl 効かない」状態に陥った時の手順:

### 前提
- CAN 通信は正常 (candump でフレームが流れる)
- Piper の電源 ON、緑ランプ点滅
- piper_sdk 0.6.1 インストール済み
- AgileX 純正 GUI (`Piper_sdk_ui`) インストール済み

### 手順

```bash
# 1. CAN を up
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up

# 2. Master モード残留を解除 (Slave モードに強制設定)
python3 << 'EOF'
from piper_sdk import C_PiperInterface_V2
import time
p = C_PiperInterface_V2('can0')
p.ConnectPort()
time.sleep(1.0)
p.MasterSlaveConfig(0xFC, 0x00, 0x00, 0x00)
time.sleep(1.0)
EOF

# 3. 電源完全リセット
#    - AC アダプタをコンセントから抜く
#    - USB-CAN を PC から抜く
#    - 30秒以上待つ (重要)
#    - USB-CAN 挿す
#    - AC アダプタ挿す
#    - 緑ランプ安定まで10秒待つ

# 4. CAN を再 up
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up

# 5. candump で 0x2A 系が流れることを確認
timeout 5 candump can0 | head -20
# 期待: 0x2A1 ~ 0x2A8 が流れる

# 6. AgileX 純正 GUI を起動
cd ~/Piper_sdk_ui
source venv/bin/activate
python3 ./piper_ui.py

# 7. GUI 上で:
#    a. Find CAN Port を押して can0 を選択
#    b. Activate CAN Port を押す
#    c. ★ Config Init ボタンを押す ★
#    d. 中央のログに各モーター情報が表示されることを確認

# 8. (別ターミナルで) SDK から動作テスト
python3 << 'EOF'
from piper_sdk import C_PiperInterface
import time
p = C_PiperInterface('can0')
p.ConnectPort()
time.sleep(1.0)
p.ModeCtrl(0x01, 0x01, 100, 0x00)
time.sleep(0.3)
p.EnableArm(7, 0x02)
time.sleep(1.5)
p.JointCtrl(0, 0, 0, 0, 0, 0)
time.sleep(2.0)
p.JointCtrl(5000, 0, 0, 0, 0, 0)  # joint1 +5度
time.sleep(3.0)
s = p.GetArmJointMsgs().joint_state
print(f"After move: j1={s.joint_1/1000:.2f}")
# 期待: j1=5.00 付近
p.JointCtrl(0, 0, 0, 0, 0, 0)
time.sleep(3.0)
EOF
```

---

## 教訓・知見

### 1. ⭐ `Config Init` ボタンは描画システムの初期化手順に組み込む必要がある

このボタンが何をしているか正確には分からないが、以下が判明した:
- 押した直後にログに各モーターの角度制限・速度制限が表示される
- ファイル `Piper_sdk_ui/piper_ui.py` を読めば、内部で何のコマンドを送っているか分かるはず
- **これを Python から再現できれば**、GUI なしで完全自動化できる

### 2. GUI の Master/Slave ドロップダウンは触らない

試しに切り替えると壊れる。Python スクリプトで明示的に `MasterSlaveConfig(0xFC, 0x00, 0x00, 0x00)` を送るほうが安全で確実。

### 3. Master モードのアームの挙動を理解

- `0x2A*` (状態フィードバック) を broadcast しない
- 代わりに `0x155-0x157` (JointCtrl ID) で自分の角度を broadcast する（指令を出す側として）
- 外部からの JointCtrl は受け付けない

### 4. 「音はする」現象 = 何かは起きている

JointCtrl 指令を送って実機が動かない時、それでも「カチッ」と音がする場合がある。これは:
- モーターに電流が流れている
- でも何かの理由でほぼ動かないか、即座に止まる

完全に沈黙している状態より、希望はある（何かを解除すれば動く可能性）。

### 5. CAN 物理層の確認方法

```bash
# 送信エラーがないかチェック
ip -details -statistics link show can0 | grep -A2 "re-started\|bus-errors\|error-warn\|error-pass\|bus-off"
# すべて 0 なら物理層 OK
```

### 6. 電源リセットは AC + USB を両方抜いて30秒待つ

- AC だけだとアームの内部状態が完全にリセットされない
- USB だけだとアームが励磁状態のまま残る
- 30秒待つことでコンデンサ放電と内部状態の完全クリア

### 7. piper_sdk と piper_ros の関係

- 両者とも同じ piper_sdk を内部で使っている
- piper_ros 経由で動かない問題は、SDK 側ではなくアーム側の問題
- piper_ros の特別な初期化シーケンスはない（昨日の調査で確認）

### 8. ファームウェア S-V1.8-2 で確認した動作

- piper_sdk 0.6.1 で標準的に動作する
- ただし `Config Init` 相当のコマンドを最初に送る必要がある場合がある
- `MasterSlaveConfig` は EEPROM に保存され、電源 OFF/ON でも保持される

---

## 未解決の小問題

- [ ] **`Config Init` ボタンが内部で何のコマンドを送っているか調査**
  - `Piper_sdk_ui/piper_ui.py` のソースを読めば分かるはず
  - これを Python 化できれば GUI なしで完全自動化可能
- [ ] **GUI で「No information for the CAN port」警告が頻発する理由**
  - 動作には影響しないが、ノイジー
- [ ] **ROS_DOMAIN_ID の分離 (Kachaka と共存しやすくする)**
  - `/kachaka/joint_states` が見えているので、現状は共有状態

---

## 描画システム実装への影響

### v0.4 設計は完全に進行可能

- ✅ 関節角度・エンドポーズの取得 OK
- ✅ JointCtrl で実機制御 OK
- ✅ piper_sdk の `EndPoseCtrl` も同様に効くはず（要確認）
- ✅ Master 残留問題は文書化されたリカバリ手順がある

### 推奨される初期化フロー (描画システム起動時)

```python
def initialize_arm():
    # 1. CAN up (システム外、bash script で実行)

    # 2. piper_sdk 接続
    p = C_PiperInterface_V2('can0')
    p.ConnectPort()
    time.sleep(1.0)

    # 3. Slave モード確認 (念のため)
    p.MasterSlaveConfig(0xFC, 0x00, 0x00, 0x00)
    time.sleep(0.5)

    # 4. ★ Config Init 相当のコマンドを送る ★
    #    現状は GUI 経由でしか送れない
    #    将来: piper_ui.py を解析して Python 化する
    raise NotImplementedError("Run AgileX GUI's Config Init manually")

    # 5. モード設定 + 励磁
    p.ModeCtrl(0x01, 0x01, 30, 0x00)
    p.EnableArm(7, 0x02)
    time.sleep(1.5)

    return p
```

### 最低限の追加調査タスク (描画システム実装の前提)

`Piper_sdk_ui/piper_ui.py` を読んで、`Config Init` ボタンが何をしているか調査:

```bash
grep -A 30 "config_init\|configInit\|ConfigInit" ~/Piper_sdk_ui/piper_ui.py
grep -A 30 "Config Init" ~/Piper_sdk_ui/piper_ui.py
```

これでボタンのハンドラを特定し、内部で送られるコマンドを再現する。

---

## まとめ

**5時間にわたるデバッグの末、ファームウェア S-V1.8-2 アームを完全に動作させる手順を確立した。**

最大の成果:
- マスター/スレーブ問題の根本原因と対処法を解明
- `Config Init` が最後のピースであることを発見
- 描画システム実装に必要な実機制御の道筋が完全に通った

ロードマップ進捗:
```
[Step 1-6] 基盤環境構築                ✅
[Step A] piper_sdk ベース軌道再生       🔲 (実機準備完了、コード未着手)
[Step B] ArUco キャリブレーション       🔲
[Step C] 線画生成パイプライン           🔲
[Step D] ベクトル化パイプライン         🔲
[Step E] 単体テスト統合                 🔲
[Step F] median 合成 + Enter トリガー   🔲
[Step G] オーケストレータ統合           🔲
```

明日以降、Claude Code 側で `~/draw_piper/` のコード実装を進められる状態。
