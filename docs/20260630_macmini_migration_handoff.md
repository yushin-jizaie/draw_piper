# Mac Mini 移行 申し送り (2026-06-30)

このPC (Linux, jizaiedev2026) を初期化し、Mac Mini に開発環境を移す際の引き継ぎ。
**次環境の Claude Code は、まずこのファイルを読むこと。**

> ## ⚠️ 最初にブランチを切り替えること
> **最新作業もこの申し送りも `main` には無い。** `git clone` 既定の main は大きく遅れている
> (draw_piper は main より 266 / piper_test は 23 コミット先行)。clone 後に必ず:
> ```bash
> cd draw_piper && git checkout claude/style-pool-rebalance-20260529
> cd ../piper_test && git checkout claude/wall-warp-grid-editor-20260606
> ```
> 作業ブランチは origin に push 済 = 安全。main へのマージは diverged (draw_piper の main に 1 件先行コミット有) ＆ 266 件と大きいので、
> **初期化前に急いでやらない**。落ち着いてから Mac 側で実施可。

---

## 0. ⚠️ 最優先: 初期化前に必ず退避するもの (git 管理外 = 消えたら復元不可)

`git push` 済みのコードは GitHub にあるので安全。**危険なのは git 管理外のファイル。**
過去に一度 `git clean -fd` で全消ししている (N5)。今回は物理初期化なので確実に退避すること。

| 対象 | サイズ | 場所 | 重要度 | 備考 |
|---|---|---|---|---|
| **学習済み FLUX LoRA** | ~1.1G | `draw_piper/models/flux_lora_winners/`, `flux_lora_matsumoto/` | ★★★ | gitignore。再学習は 16GB GPU で数時間。**最優先退避** |
| 学習データ・LoRA runs | ~1.1G | `draw_piper/training/` | ★★ | raw 画像・dataset・checkpoint |
| 生成物 | ~2.1G | `draw_piper/sketch_variations/` | ★ | 再生成可だが時間 |
| **auto-memory** | 22 file | `~/.claude/projects/-home-jizaiedev2026-draw-piper/memory/*.md` | ★★★ | **git 管理外**。プロジェクトの暗黙知。下記 §6 |
| piper_test 未コミット | - | `wall_drawing_gui_full_dev.py` (modified) + `frida_circle_test/` (untracked) | ★★★ | **commit & push してから初期化** |
| calibration yaml | 小 | `draw_piper/calibration/*.yaml` | ★★ | committed 済だが念のため確認 |

退避コマンド例 (外付け or 別マシンへ):
```bash
# 最低限これだけは死守 (LoRA + memory + 未コミット push)
rsync -av ~/draw_piper/models/ /mnt/backup/models/
rsync -av ~/.claude/projects/-home-jizaiedev2026-draw-piper/memory/ /mnt/backup/claude_memory/
cd ~/piper_test && git add -A && git commit -m "WIP: 移行前退避" && git push
# 余裕があれば training/ sketch_variations/ も
```

---

## 1. ⚠️ macOS で動かない 2 大サブシステム (移行戦略の根幹)

**Mac Mini はこのプロジェクトの中核 2 つをネイティブに動かせない。** 必ず認識すること。

1. **ロボット CAN 制御** — `socketcan` / `can0` / `candump` / `python-can` の socketcan backend + `piper_sdk` は **Linux 専用**。
   → Mac Mini で Piper アームは動かせない。**アーム作業には Linux 機 + USB-CAN アダプタが必須。**
2. **CUDA 画像生成 / LoRA 学習** — `torch` + `bitsandbytes`(nf4) + `diffusers` (FLUX / LoRA) は **NVIDIA CUDA 専用**。
   → Apple Silicon の MPS では bitsandbytes nf4 量子化が動かない。**生成・学習も Mac Mini 不可。**

### 推奨構成
- **Mac Mini** = 編集 / Claude Code / git / ドキュメント / **オフライン IK 数学** (`wall_facing_ik.py` は numpy のみ＝どこでも動く)。
- **Linux + NVIDIA 機を残す/用意** = アーム制御 (CAN) と 画像生成・学習 (CUDA)。
- 現 PC を初期化するなら、**アーム+GPU 用の Linux 機が別途必要**。Mac 単体で完結はできない。
  → ここは要ユーザー判断 (Mac Mini は前段の開発機、実行は別 Linux か?)。

---

## 2. リポジトリ (Mac Mini で clone)

| repo | remote | 役割 | 現ブランチ |
|---|---|---|---|
| **draw_piper** | `github.com/yushin-jizaie/draw_piper` | 画像生成パイプライン本体・docs・calibration・`modules/robot.py` | `claude/style-pool-rebalance-20260529` |
| **piper_test** | `github.com/yushin-jizaie/piper_test` | 壁面描画 GUI・IK (`wall_facing_ik.py`)・ドラッグ示教 | `claude/wall-warp-grid-editor-20260606` |
| piper_ws | `github.com/yushin-jizaie/piper_ws` | ROS2 ワークスペース (現状は SDK 直叩きが主、ROS2 は補助) | - |
| Piper_sdk_ui | `github.com/agilexrobotics/Piper_sdk_ui` (upstream) | AgileX 公式。Config Init の参照元。再 clone 可 | - |

```bash
cd ~  # Mac Mini
git clone https://github.com/yushin-jizaie/draw_piper.git
git clone https://github.com/yushin-jizaie/piper_test.git
# ★clone 既定は main (遅れている)。必ず作業ブランチへ:
cd draw_piper && git checkout claude/style-pool-rebalance-20260529 && cd ..
cd piper_test && git checkout claude/wall-warp-grid-editor-20260606 && cd ..
# アームを使う Linux 機のみ:
git clone https://github.com/agilexrobotics/Piper_sdk_ui.git
```

---

## 3. セットアップ手順

### 3a. 共通 (編集/IK のみ・Mac Mini で可)
```bash
cd ~/draw_piper
python3 -m venv venv        # 現環境は Python 3.10.12 (README の 3.11 は誤記)
./venv/bin/pip install numpy opencv-python pillow pyyaml
# オフライン IK の確認 (アーム不要):
cd ~/piper_test && python3 wall_facing_ik.py    # CASE A/B が出れば OK
```

### 3b. アーム制御 (Linux 機のみ)
```bash
./venv/bin/pip install -r requirements.txt   # piper_sdk, python-can 含む
# USB-CAN を挿して:
sudo ip link set can0 up type can bitrate 1000000
# 壁面描画 GUI (venv は draw_piper 側を流用):
cd ~/piper_test && ~/draw_piper/scripts/wall_gui   # 1行ラッパー (memory: wall_gui_launch_command)
```
- Piper firmware **S-V1.8-2**。`Robot.connect()` が Config Init を自動送信 (memory: piper_firmware_v18_id_offset)。
- 状態が all-zero → master mode。**slave mode に戻す** (§5 参照)。

### 3c. 画像生成 / 学習 (Linux + NVIDIA 機のみ)
```bash
./venv/bin/pip install -r requirements-vlm.txt      # torch, transformers, bitsandbytes
./venv/bin/pip install -r requirements-imagegen.txt # diffusers
# 生成ルート確定版 (memory: winning_genart_recipe / flux_schnell_controlnet_setup):
./venv/bin/python scripts/gen_routed.py             # 占有率ルーティング + ControlNet
```

---

## 4. ドキュメントの地図

- **`draw_piper/MILESTONES.md`** — 「戻れる正常地点」と「詰まり (N1〜N6)」の時系列地図。**最初に読む。** M5/M18/M19/M20 と N1〜N6 が要点。
- `draw_piper/README.md` — 起動コマンドと構成。
- `draw_piper/CLAUDE.md` / `piper_test` 側の運用メモ — Claude Code 運用ルール (MILESTONES 更新提案・git 速度ルール)。
- `draw_piper/docs/` — 設計書と過去の handoff。特に:
  - `20260527_0200_handoff_to_local_arm_session.md` — アーム実機作業の引き継ぎ
  - `20260522_1700_piper_jointctrl_solved.md` — JointCtrl が効かない問題の解決 (N2)
  - `piper_ros2_setup_progress*.md` — CAN/ROS2 セットアップ経緯
  - `20260522_1940_physical_setup_calibration_geometry.md` — 物理設置とキャリブ幾何

---

## 5. 申し送り: 現在進行中の課題 (live thread, 2026-06-30 時点)

### ★A. 「IK で矩形が描けない」の診断結論 (本セッションで確定)
**IK の計算は無罪。** オフライン検証で:
- 向き拘束を外せば link6 は完璧な矩形を描ける (pos_err 0.00mm、辺ピッタリ)。**リーチは問題でない。**
- 小矩形 (10〜40mm) も向き拘束込みで綺麗に収束 (j5 飽和なし、関節連続)。
- FK モデルは SDK end pose と 0.3mm 一致と検証済 ([wall_facing_ik.py](../../piper_test/wall_facing_ik.py) 冒頭)。

→ **実機で見える「link6 が数十cmズレ・link4 から見当違いの向き」は IK より下流で注入されている。** 候補 3 つ:
- **(a) 板→ロボット座標の対応 (キャリブフレーム) が間違い** → IK は正しい関節角を「間違った目標」に解く。
- **(b) 実機が指令関節角に届いていない** → streaming JointCtrl (settle 待たず) で命令落ち/遅延。
- **(c) モデルと firmware の関節定義不一致 (特に joint4)** → 「link4 から見当違い」は joint4 のゼロ点/符号ズレの典型兆候。**筆頭容疑。**

**次の決定打 (要実機, Linux 機):** 1 点だけ `solve_ik` 経由で指令 → `get_joints()` / `get_end_pose()` を読み戻し、
「指令関節角 vs 実測関節角」「目標 pose vs 実測 pose」「solve_ik に渡った目標 XYZ」を並べて出す診断を 1 回回せば (a)(b)(c) が確定する。

### ★B. feedback / 示教モードの WEB 裏取り (公式 issue で確定)
- all-zero feedback の公式原因は **master mode**。修正は **slave mode に戻す** (piper_sdk #76, #33)。
- **重力補償つき示教 + feedback 読取は「slave mode + 本体示教ボタン」で両立** (piper_ros #26)。
  → 現状の drag-teach が master mode (`MasterSlaveConfig 0xFA`) を使っているなら、slave + ボタンに置換すれば
  0x3A* シフトも workaround listener も不要になる可能性大。**要実機検証。** (memory: piper_firmware_v18_id_offset / piper_end_load_gravity_comp に追記済)

### C. 描画品質の効くレバー (確定済の方針)
- クリーンなキャリブ (示教中サグ防止 = 重力補償) + **9 点実測ワープ補正** ([modules/draw_warp_correction.py](../modules/draw_warp_correction.py))。
- 中心+サイズ→四隅方式は廃止済 (モデル駆動オープンループで台形化、memory: calibration_center_size_redesign)。
- **MoveIt は不採用** (CAN feedback が前提だが本機で最も脆弱。修羅場を払い直すだけ。本セッションで結論)。

---

## 6. auto-memory の引き継ぎ (★忘れやすい)

`~/.claude/projects/-home-jizaiedev2026-draw-piper/memory/` の 22 個の `.md` は **git 管理外**。
プロジェクトの暗黙知 (firmware・キャリブ・生成レシピ・失敗の教訓) が詰まっている。

**Mac Mini 側の対応:**
1. このディレクトリを退避し、Mac Mini の**新しいプロジェクトパスに対応する memory ディレクトリ**へコピー。
   - パスは `~/.claude/projects/<新パスのスラッグ>/memory/` になる (プロジェクトの絶対パスから生成)。
   - 最初に Claude Code を一度起動して新スラッグのディレクトリを作らせてから、`*.md` と `MEMORY.md` を流し込むのが確実。
2. `MEMORY.md` が索引。各行が 1 メモリへのポインタ。
3. 特に重要: `piper_firmware_v18_id_offset`, `piper_end_load_gravity_comp`, `drawing_distortion_diagnostic`,
   `calibration_center_size_redesign`, `winning_genart_recipe_lineart_cn05`, `flux_lora_training_16gb`。

---

## 7. ★Mac Mini 単体で動かすための設計変更 (申し送り)

§1 で「Mac 不可」とした 2 大ブロッカーは、**設計変更すれば Mac Mini 単体でも動かせる**。
調査の結果、思ったより現実的。ただし下記は**未検証の設計方針**であり、実機・実マシンで 1 つずつ潰すこと。

### 8a. ロボット CAN 制御 → Mac ネイティブ化 (実現性: 高)

**決め手: 使用中の USB-CAN アダプタは candleLight/gs_usb 系。**
- `lsusb` で `1d50:606f Geschwister Schneider CAN adapter` (docs/piper_ros2_setup_progress_append_2026-05-21.md)。
  これは **candleLight (gs_usb) ファーム**。socketcan を介さず **python-can の `gs_usb` backend で macOS から直接 USB 通信できる** (libusb/pyusb 経由、Linux 不要)。
- **piper_sdk は socketcan をハードコードしていない。** `can_encapsulation*.py` の `bustype` は引数で、
  デフォルトが `"socketcan"` なだけ。デモ `piper_sdk/demo/V2/piper_set_can.py` は `bustype="slcan"` を使い、
  「pcie/串口 can なら `judge_flag=False` で socketcan 検査を回避」とコメントあり。

**設計変更の要点:**
1. `pip install gs_usb pyusb` + macOS は `brew install libusb`。デバイスを掴むカーネルドライバが無いこと (gs_usb は user-space)。
2. **patch 箇所が特定済**: `piper_sdk/interface/piper_interface.py:418` が
   `C_STD_CAN(can_name, "socketcan", 1000000, judge_flag, ...)` と socketcan を直書き。
   ここ (および V2 相当) を **`bustype="gs_usb"` を渡せるよう改修** (引数化 or fork)。
   `modules/robot.py` の `Robot.connect()` から bustype/channel/bitrate を渡す設計にする。
3. macOS には `ip link set can0 up` が無い。**bitrate は `can.interface.Bus(..., bitrate=1000000)` 実体化時に指定** (gs_usb backend が設定)。README/GUI の `pkexec ip link` 手順は Mac では不要・削除。
4. **bring-up シーケンス (MasterSlaveConfig 0xFC / Config Init 0x477 / EnablePiper) は CAN フレーム層なので backend 非依存** → そのまま動くはず。
5. **要再検証 (backend が変わると挙動が変わりうる)**:
   - master mode の 0x2A*→0x3A* シフト と N4 の socket starvation が gs_usb でどうなるか (改善する可能性もある)。
   - **candump が macOS に無い** → 別プロセス candump 案 (§5B の旧案) は不可。代わりに **python-can gs_usb の in-process reader** で 0x2A*/0x3A* を読む `modules/piper_feedback.py` を gs_usb 対応に書き換える。
   - そもそも §5B の結論「示教は slave mode + 本体ボタンで feedback 生存」が効けば、0x3A* 問題自体が消えるので feedback 周りは大幅に単純化できる。

**フォールバック (gs_usb が不安定なら):** Raspberry Pi に USB-CAN を挿し socketcan + `socketcand` を立て、
Mac から python-can の `socketcand` backend で TCP 越しに繋ぐ。Mac で開発、Pi が CAN を担当。$50 で確実。

### 8b. 画像生成 / LoRA → Mac ネイティブ化 (実現性: 中, メモリ次第)

**唯一の壁は bitsandbytes nf4 量子化 (CUDA 専用)。** 現状 `scripts/gen_*.py` が
`BitsAndBytesConfig(load_in_4bit, nf4)` で FLUX を 15GB に収めている (memory: flux_schnell_controlnet_setup)。
Apple Silicon では bitsandbytes が動かないので **nf4 を捨てる設計**にする。3 案:

1. **mflux (MLX) ★推奨** — Apple Silicon ネイティブの FLUX 実装。FLUX schnell/dev + LoRA + MLX 量子化(4/8bit)対応。
   既存 LoRA (`models/flux_lora_winners` 等) を mflux 形式で読む (HF safetensors、変換要確認)。
   **要検証の最大ギャップ: ControlNet。** 現レシピは「FLUX schnell + ControlNet Union canny CN0.2」。
   mflux の ControlNet 対応は限定的なので、Union CN が載るか先に確認。載らなければ案2/3。
2. **diffusers + MPS + bf16** — nf4 を外し `pipe.to("mps")`。ControlNet はフル対応で**レシピをそのまま移植可**。
   ただし **量子化しないので大容量ユニファイドメモリが必須** (FLUX+CN を bf16 で。実質 ≥32GB、できれば 64GB)。MPS は CUDA より遅い (1 枚 数分)。
3. **ComfyUI (Metal)** — Mac で ControlNet 含め広く動く。GUI 運用なら現実的。

**LoRA 学習は Mac では非現実的** (bitsandbytes 8bit Adam 不可、MPS 学習は遅く不安定)。
→ 学習はクラウド GPU を時間借り (4090/A100) するか、**既存の学習済 LoRA で推論だけ Mac**(主用途はこれで足りるはず)。

**VLM (Qwen2.5-VL-7B 完成形ビジョン)**: `mlx-vlm` で Apple Silicon ネイティブ、または
**Anthropic API (Claude) に置換**して局所 GPU 負荷を消すのが簡明 (品質も期待できる)。

**★ハード選定の肝: Mac Mini は大容量ユニファイドメモリ (≥32GB、できれば 64GB) を選ぶこと。**
FLUX inference のメモリがそのまま生成可否を決める。16GB では現レシピは厳しい。

### 8c. コード側のリファクタ方針 (単体化を支える)

- **CAN backend を設定化**: `modules/robot.py` で socketcan 前提・`ip link` 前提を排し、
  `bustype` / `channel` / `bitrate` を設定値に (Linux=socketcan, Mac=gs_usb を 1 箇所で切替)。
- **生成 backend を抽象化**: `generate()` を CUDA/diffusers と MLX/mflux で差し替え可能な薄い IF に。
  scripts は backend 非依存にして、環境変数や config で選ぶ。
- **feedback を backend 非依存に**: candump 前提を捨て python-can reader (gs_usb/socketcan 共通) に統一。
- この 3 つを切れば、同一コードが「Linux+NVIDIA」でも「Mac Mini 単体」でも動く構成になる。

### 8d. 単体化の検証順序 (推奨)
1. Mac で `gs_usb` + patched piper_sdk で **CAN 接続 → Config Init → j1 を数度動かす** だけ先に通す (最小リスク)。
2. 通れば slave mode 示教 + feedback 読取 (§5B) を Mac で確認。
3. 並行して **生成は mflux で 1 枚** 出す → ControlNet Union が載るか判定 → 案 2/3 へ分岐。
4. 最後に壁面描画 GUI を Mac で起動 (Tk は Mac でも動く。絵文字 segfault 注意 = memory: tk_emoji_xft_segfault)。

---

## 8. ★AI パート全面作り変え: 構成予測アーキ (Mac 版設計)

現状の `VLM → テキスト → FLUX → ControlNet → DIS ワープ` は**入力から出力まで期待品質に届かず、まるごと作り変える**方針 (2026-06-30 ユーザー決定)。不満の核は **「線画スタイル」と「完成予測の質」**。**完全ローカル必須** (外部 API 不可)。

### 8a. 問題の再定義: 穴埋め → シーン構成予測 (本セッションの核心)
- ユーザーが**椅子**を描いたら、予測すべきは「椅子の完成形」ではなく **「その椅子が在る部屋全体 (テーブル等)」**。
- つまり**ピクセルの画像補完ではなく、「物体の組み合わせ＋配置」という構造的(シンボリック)予測**。
- これにより、難しい意味部分 (何と何が同じ空間に居るか・どこに) と、簡単な描画部分 (固定線画で描く) を**分離**でき、汎用画像生成の幻覚・整合崩れがほぼ消える。
- **重要な前提認識 (ユーザー)**: 画風はホワイトボード線画で**一度固定すれば CN は低くてよい**。ただし**入力物体の位置は認識する必要がある**。

### 8b. 4 段アーキテクチャ + Mac ローカル実装
```
入力線画
 ① 知覚:    何が・どこに        → 物体検出+位置  {椅子 @ bbox, scale}
 ② 構成予測: シーン全体を予測      → {椅子, テーブル, 窓, …} + 相対レイアウト
 ③ 描画:    各物体を固定線画で配置  → 完成線画            (CN 低/任意)
 ④ 差分:    出力 − 入力          → 加筆物体だけ → ロボットが描く
```
- **① 知覚 (何が・どこに)**: 線画の物体検出+位置。`Qwen2.5-VL` (mlx-vlm) の grounding で bbox、または GroundingDINO。MPS/MLX。**線画ドメイン適応が要** (写真より難)。
- **② 構成予測 (シーン全体)**: **v0 = 検索DB** (コーパスを「物体→共起物体+相対レイアウト」に前処理 → 入力物体の検出位置/スケールに合わせ検索+適応)。**CPU/numpy のみ = Mac 余裕、学習ほぼ不要、可制御**。 v1 = 小レイアウトLLM (mlx/llama.cpp で `(物体,位置,スケール)` 列を生成)。
- **③ 描画 (固定線画で配置)** ← 分岐点:
  - **(b) 推奨 = 線画アセット辞書**: 白板スタイルで描かれた物体を検索→配置 (画像合成のみ、**GPU 不要・幻覚ゼロ・完全可制御**)。漫画の物体クロップから辞書化。**ロボットが描く加筆＝そのまま辞書の線**になり整合も完璧。
  - (a) 代替 = 物体ごと線画生成 (mflux/SDXL-MPS + style LoRA、低 CN)。柔軟だが多少の幻覚。
  - スタイルは描画器(LoRA/辞書)が固定、配置は②が決めるので **CN は「スタイル」も「配置」も背負わず低くて済む**。
- **④ 差分**: 出力 − 入力 = 加筆物体。opencv。

### 8c. なぜ Mac ローカル向きか
大半が **CPU/軽量** (検索・画像合成・opencv)。重いのは ③(a) の任意生成のみで、それも mflux/MPS で可。
**旧 FLUX-CN パイプラインより遥かに Mac 単体向き** = §7 の単体化が現実的になる。

### 8d. データ設計 (源を分ける)
- **共起＋空間関係の学習** → 注釈の濃いシーンデータ (シーングラフ系: 何と何がどこに、が取れる) から。
- **線画スタイル/物体クロップ** → 漫画 + 自前ホワイトボードデータ。
- **注意**: 漫画の物体検出は難しい (スクリーントーン/吹き出し/効果線、Manga109 の注釈は frame/顔/体/セリフ止まりで任意物体 box 無し)。**「漫画から物体抽出」自体が重い工程**。共起は構造データ、線画らしさは漫画、と源を分けるのが現実解。

### 8e. ロボット統合 (最も相性が良い)
離散の物体を既知位置に配置 ＝ **そのまま「加筆分＝描く線の集合」**、位置も既知。
「出力から入力線を差し引く」([robot_draws_only_additions](../../draw_piper))、「入力線=唯一のアライン基準」([design_align_warp_compose]) も自明に成立。**今までで一番ロボット向きの設計。**

### 8f. 未決の分岐 (次セッションで決める)
1. **③描画**: アセット辞書(b) で始める / 生成(a) で始める
2. **②コーパス**: 漫画から自前抽出にこだわる / シーングラフ等の既存注釈で共起を学び、線画らしさだけ漫画で

### 8g. 最小プロトの順序
`①検出 → ②検索構成 → ③(b)配置 → ④差分` の**薄い縦串を 1 本**通してから各段を強化する。
関連 memory: `robot_draws_only_additions`, `design_align_warp_compose`, `winning_genart_recipe_lineart_cn05`, `ip_matsumoto_radial_ink_framing`。

---

## 9. 初期化前チェックリスト

**退避 (初期化で消える):**
- [ ] `piper_test` を commit & push (`wall_drawing_gui_full_dev.py` + `frida_circle_test/`)
- [ ] `draw_piper/models/` (学習済 LoRA) を退避 ★最優先
- [ ] `~/.claude/.../memory/` を退避 ★
- [ ] `training/` `sketch_variations/` を退避 (任意)
- [ ] `calibration/*.yaml` が push 済か確認
- [ ] このファイルを push (`draw_piper/docs/20260630_macmini_migration_handoff.md`)

**Mac Mini 立ち上げ:**
- [ ] 両 repo を clone し §3a で IK 動作確認 (numpy のみ、すぐ通る)
- [ ] §7a: `gs_usb` + piper_sdk patch で CAN 接続 → Config Init → j1 を動かす (単体化の第一関門)
- [ ] §7b: 生成は mflux で 1 枚 → ControlNet Union が載るか判定
- [ ] Mac Mini は **ユニファイドメモリ ≥32GB (推奨 64GB)** を確保したか

**単体化しない場合の代替:**
- [ ] アーム+GPU 用に Linux+NVIDIA 機を手当て (§7 をやらないなら必須)
