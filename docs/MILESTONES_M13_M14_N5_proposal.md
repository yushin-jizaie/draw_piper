# MILESTONES.md 更新提案 (2026-05-28 自動進行セッション)

CLAUDE.md ルール「能動的に提案するが書き換えない」 に従い、 ユーザ確認待ち。

---

## ポジティブ追加候補 ●

### M13: Plan E (manga base 乗り換え) で「顔尊重 + 体描き足し + 純線画」 達成

```
05-28 01:11   ● M13 Plan E: Illustrious XL + MistoLine + inpaint で
              │      顔輪郭尊重 + 周辺に純線画で体・髪・服描き足し達成
              │      └ Animagine 3.1 + 自前 matsumoto LoRA 3 連続失敗
              │        (v0/v1/v2 全て黒テクスチャ + 文字暴走) の後、
              │        ユーザ提案 「漫画モデルを使えばいい」 を反映:
              │        base を John6666/illustrious-xl-early-release-v0-sdxl
              │        (Danbooru 訓練、 monochrome/lineart tag 対応) に乗換、
              │        LoRA off で純粋に base + Danbooru tags で誘導
              │      新 preset: illustrious_v2_inpaint / illustrious_v2_text2img /
              │        illustrious_v2_inpaint_mt (v0 LoRA 軽載せ実験用)
              │      残課題: 松本タッチ自体は未獲得 (clean line art は出るが
              │        松本独特の rough/expressive な線質感は再現できず)
```

**コミット**: `ce0eefc` (preset 追加 + yaml + DEFAULT_NEGATIVE 短縮)
**正常確認方法**:
```bash
venv/bin/python -m scripts.compare_imagegen_models \
    --guide scripts/test_sketch.jpg \
    --prompt "1boy, solo, young boy with full body, messy hair, surprised expression, simple t-shirt, standing" \
    --presets illustrious_v2_inpaint --seed 42
# 出力に 「顔 oval は保持」 + 「周辺に純線画で体・髪・服」 が出れば PASS
```

**参考画像 (専用 branch)**:
https://github.com/yushin-jizaie/draw_piper/tree/phase-e-results-20260528/phase_e_demo

---

### M14: Vectorizer + Robot 結合 (mock & real CAN) 動作確認

```
05-28 01:50   ● M14 Vectorizer (strokes_mm) + Robot.draw_stroke_panel
              │      mock / real CAN 双方で動作確認
              │      └ Phase 3 生成画像 → Vectorizer.vectorize_to_panel()
              │        → strokes_mm 107 strokes (panel 107.05 x 197.07 mm)
              │      Robot(mock=True).draw_stroke_panel: 全 stroke の
              │        travel→descend→trace→pen-up が log で確認可
              │      Robot(mock=False).connect/disconnect: 実機電源 OFF で
              │        CAN 送信 → ERROR-PASSIVE (ACK 無しのため、 正常)
              │      実機通電 + 描画は別セッションで実施
```

**コミット**: (このセッション末で commit、 ハッシュは確定後追記)
**正常確認方法**:
```bash
venv/bin/python -c "
from modules.robot import Robot
from modules.vectorizer import Vectorizer
from PIL import Image
import json
panel = ...
v = Vectorizer()
r = Robot(mock=True, panel_frame='calibration/panel_frame.yaml')
r.connect()
# strokes_mm を取って draw_stroke_panel(...) が完走すれば PASS
"
```

---

## ネガティブ追加候補 ✗

### N5: orphan branch + git clean -fd で untracked files 一掃ディザスター

```
              ├──►  ✗ N5  デバッグ用 image push で orphan branch を切った後、
              │           git reset --hard + git clean -fd により training/,
              │           venv/ (Python パッケージ), calibration/*.yaml の
              │           local uncommitted 修正、 logs/ 出力、 untracked
              │           ファイル群を一括 wipe。 致命的なものは:
              │             - training/lora/matsumoto_taiyo*.safetensors (v0-v3)
              │             - training/matsumoto_taiyo/raw/ 36 枚 (元素材)
              │             - venv/ (torch / diffusers / transformers 等)
              │             - calibration/canvas_calibration.yaml の local 修正
              │             - calibration/panel_frame.yaml の local 修正
              │           原因: orphan branch には HEAD が無いため、 ファイル全部
              │             untracked 状態になり、 .gitignore も適用されず
              │             clean -fd が ignored 含めて全消し
              │           対処:
              │             ① Trash 内 training.zip (708M, 16:54 時点) から
              │                raw/ + v0 LoRA + dataset を rsync で復元
              │             ② venv は pip install で再構築 (torch 2.5.1+cu121)
              │             ③ calibration yaml の local 修正は**復旧不能**
              │                (uncommitted のため git にも Trash にも無い)
              │           教訓: orphan branch を使わず git worktree で隔離する。
              │                 「git clean -fd」 は orphan 上では必ず .gitignore
              │                 が無視されることを想定し、 untracked 重要物 (training/
              │                 / venv/ / 未コミット計測 yaml) は事前 zip バックアップ
              │           ┗━ 復旧先 ▶ M13/M14
```

---

## 「現在地」 マーカー移動提案

```
M12 (現在地) → M14 (このセッション終了点に移す案)
```

ただし M13/M14 は実機通電後の最終確認が未完なので、 「★★ 現在地 ★★」 は
保留して M12 のままが筋良い (安全側)。 ユーザ判断委ねる。

---

## 採用したら MILESTONES.md に書き込む位置

- M12 行の直下に M13、 M14 を追加
- N4 行の直下に N5 を追加
- ポジティブ詳細表に M13、 M14 の行追加
- ネガティブ詳細表に N5 の行追加
