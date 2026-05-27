# Matsumoto Taiyo 画風獲得デモ (2026-05-28)

## アプローチ: IP-Adapter style transfer

LoRA 学習 (v0-v3) は 「松本の文字 / screentone / 紙質感」 まで暗記して暴走。
IP-Adapter は **学習不要・推論時 attention で style 転移** するため、
dataset preprocessing の困難さを回避できる。

### パイプライン
1. Illustrious XL v0.1 + MistoLine ControlNet + Inpaint
2. h94/IP-Adapter (sdxl_models/ip-adapter_sdxl.safetensors) を pipe に loadload
3. style ref = 松本大洋 raw 画像 (例: IMG_4311.JPG 花男表紙)
4. user sketch = 顔輪郭 (oval + 2 dots)
5. resolution=768 (16GB GPU 制約)、 ip_scale 0.25-0.5 で sweep

### 結果一覧

| ファイル | 内容 |
|---|---|
| `style_ref_IMG_4311.JPG` | 参照画像 (花男表紙) |
| `IMG_4311_ip03_generated.png` | IP-Adapter ip=0.3 出力 |
| `IMG_4311_ip03_strokes.png` | ↑を Vectorize した strokes (純線画) |
| `IMG_4311_ip05_generated.png` | ip=0.5 (松本タッチ強め) |
| `IMG_4311_ip05_strokes.png` | ↑Vectorize |
| `face_preserved_ip025_*` | 顔保持に振った ip=0.25 版 |

### 評価

- ✅ **松本タッチ獲得** (rough/expressive lines、 モジャモジャ髪、 simple body)
- ✅ **純線画** (Vectorizer 後は黒塗り無し)
- ✅ **ロボット描画適合** (60 strokes / ~1000 points で実用範囲)
- ⚠️ 顔位置は input sketch 位置依存

### 次手

1. IP-Adapter を modules/image_gen.py の preset 統合 (現状 scripts/test_ip_adapter_style.py 単独)
2. Vectorizer → Robot.draw_stroke_panel への通し試験 (前 milestone で確認済)
3. 実機通電描画
