# Matsumoto v2: Two-stage IP-Adapter (顔位置 fix 版)

## 問題と解法

**v1 (matsumoto_v1_ip_adapter/) の問題**: IP-Adapter が style ref の構図 (顔=頭、 体=下) を
学習してしまい、 元 sketch の face oval が「胴体中央」 に置かれてしまった。

**v2 解法**: 2-stage 構成で 構図と style を分離。
- **Stage 1**: Plan E (illustrious_v2_inpaint, IP-Adapter なし、 1024res) で 顔保持 +
  体描き足し の構図確定版を生成
- **Stage 2**: Stage 1 出力を init として img2img + IP-Adapter (768res、 strength 0.45)
  で style だけ転写、 構図保持

## 結果

| ファイル | 役割 |
|---|---|
| `00_user_sketch_1024.png` | 入力 (顔 oval + 目 2 つ) |
| `10_stage1_plan_e_1024.png` | Stage 1: 構図確定版 (Plan E 1024res) |
| `11_stage1_resized_768.png` | Stage 2 用に 768 にリサイズ |
| `12_style_ref_matsumoto.png` | IP-Adapter の style ref (花男表紙) |
| `20_stage2_ip_adapter_final.png` | Stage 2 出力: 松本タッチ強め |
| `30_robot_ready_strokes.png` | Vectorize 後: ロボット描画用純線画 (114 strokes / 2542 pts) |

## 評価

- ✅ **松本タッチ獲得** (spiky 髪、 rough/dynamic body、 expressive lines)
- ✅ **顔保持** (元 sketch の oval が画像中央~上に位置、 hair が外側に展開)
- ✅ **純線画** (Vectorizer 後で黒塗り無し)
- ✅ **ロボット描画適合** (114 strokes、 ~1500 mm 総距離、 panel 107×197mm に妥当)

## 再現コマンド

```bash
./venv/bin/python -m scripts.test_ip_adapter_two_stage \
    --user-sketch scripts/test_sketch.jpg \
    --style-ref training/matsumoto_taiyo/raw/IMG_4311.JPG \
    --output logs/ip_2stage_$(date +%Y%m%d_%H%M%S) \
    --stage1-resolution 1024 \
    --resolution 768 \
    --stage2-strength 0.45 \
    --ip-scale 0.6 \
    --seed 42
```
