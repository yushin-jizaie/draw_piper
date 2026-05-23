# Canny experiment summary

Date: 2026-05-23 14:49:48

各画像 × 各手法での「線ピクセル率」と「推定ストローク数」

- **line_pixel_ratio**: 全ピクセル中の線部分の割合
  - 1% 前後 = 線画らしい
  - 10% 超 = ベタ塗りに近い (ダメ)
  - 0.1% 未満 = スカスカ (情報不足)
- **estimated_strokes**: 連結成分数 (線のかたまり数)
  - 50〜300: ロボットで2分以内に描ける範囲
  - 1000+: 多すぎ

## sdxl_output_20260523_140432_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 10.348% | 3392 |
| 02_canny_mid | 7.768% | 1995 |
| 03_canny_high | 6.561% | 1487 |
| 04_canny_blur_high | 4.449% | 530 |
| 05_canny_strong_blur | 3.293% | 324 |
| 06_adaptive_thresh | 16.272% | 13598 |
| 07_otsu | 22.731% | 294 |
| 08_canny_dilate | 17.112% | 110 |

## sdxl_output_20260523_140435_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 13.631% | 3456 |
| 02_canny_mid | 10.050% | 2205 |
| 03_canny_high | 6.828% | 1835 |
| 04_canny_blur_high | 1.995% | 239 |
| 05_canny_strong_blur | 1.054% | 75 |
| 06_adaptive_thresh | 16.591% | 24118 |
| 07_otsu | 20.302% | 1135 |
| 08_canny_dilate | 22.407% | 138 |

## sdxl_output_20260523_140438_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 11.613% | 2569 |
| 02_canny_mid | 8.507% | 1596 |
| 03_canny_high | 6.044% | 1320 |
| 04_canny_blur_high | 2.662% | 361 |
| 05_canny_strong_blur | 1.183% | 83 |
| 06_adaptive_thresh | 13.214% | 8172 |
| 07_otsu | 19.335% | 680 |
| 08_canny_dilate | 18.618% | 292 |

## sdxl_output_20260523_141456_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 11.106% | 3287 |
| 02_canny_mid | 9.667% | 2107 |
| 03_canny_high | 8.061% | 1726 |
| 04_canny_blur_high | 4.992% | 538 |
| 05_canny_strong_blur | 3.353% | 232 |
| 06_adaptive_thresh | 16.380% | 10009 |
| 07_otsu | 31.451% | 163 |
| 08_canny_dilate | 21.165% | 77 |

## sdxl_output_20260523_141458_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 12.474% | 3619 |
| 02_canny_mid | 11.280% | 2824 |
| 03_canny_high | 10.592% | 2418 |
| 04_canny_blur_high | 7.861% | 878 |
| 05_canny_strong_blur | 3.920% | 446 |
| 06_adaptive_thresh | 20.071% | 18689 |
| 07_otsu | 20.571% | 559 |
| 08_canny_dilate | 24.973% | 47 |

## sdxl_output_20260523_141501_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 14.560% | 3591 |
| 02_canny_mid | 13.754% | 2944 |
| 03_canny_high | 12.379% | 2758 |
| 04_canny_blur_high | 6.583% | 1019 |
| 05_canny_strong_blur | 2.747% | 304 |
| 06_adaptive_thresh | 19.926% | 9996 |
| 07_otsu | 18.359% | 1229 |
| 08_canny_dilate | 30.537% | 109 |

## sdxl_output_20260523_141512_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 11.416% | 3212 |
| 02_canny_mid | 8.416% | 1847 |
| 03_canny_high | 7.408% | 1524 |
| 04_canny_blur_high | 5.222% | 753 |
| 05_canny_strong_blur | 2.919% | 324 |
| 06_adaptive_thresh | 16.556% | 11605 |
| 07_otsu | 25.899% | 165 |
| 08_canny_dilate | 18.492% | 90 |

## sdxl_output_20260523_141514_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 7.435% | 839 |
| 02_canny_mid | 7.210% | 625 |
| 03_canny_high | 7.047% | 511 |
| 04_canny_blur_high | 6.630% | 354 |
| 05_canny_strong_blur | 5.770% | 432 |
| 06_adaptive_thresh | 16.424% | 9364 |
| 07_otsu | 17.227% | 90 |
| 08_canny_dilate | 15.435% | 48 |

## sdxl_output_20260523_141517_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 13.738% | 2927 |
| 02_canny_mid | 11.355% | 2027 |
| 03_canny_high | 8.632% | 1890 |
| 04_canny_blur_high | 3.779% | 564 |
| 05_canny_strong_blur | 1.458% | 147 |
| 06_adaptive_thresh | 16.301% | 10376 |
| 07_otsu | 15.710% | 993 |
| 08_canny_dilate | 24.884% | 242 |

## sdxl_output_20260523_141833_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 12.909% | 4046 |
| 02_canny_mid | 10.426% | 2694 |
| 03_canny_high | 8.811% | 2208 |
| 04_canny_blur_high | 5.888% | 831 |
| 05_canny_strong_blur | 3.670% | 409 |
| 06_adaptive_thresh | 17.088% | 10975 |
| 07_otsu | 31.885% | 388 |
| 08_canny_dilate | 23.119% | 93 |

## sdxl_output_20260523_141835_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 12.136% | 2823 |
| 02_canny_mid | 9.717% | 1829 |
| 03_canny_high | 7.660% | 1487 |
| 04_canny_blur_high | 4.309% | 457 |
| 05_canny_strong_blur | 1.951% | 154 |
| 06_adaptive_thresh | 14.245% | 15226 |
| 07_otsu | 22.554% | 604 |
| 08_canny_dilate | 21.480% | 39 |

## sdxl_output_20260523_141838_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 13.967% | 3406 |
| 02_canny_mid | 12.454% | 2603 |
| 03_canny_high | 10.888% | 2324 |
| 04_canny_blur_high | 6.621% | 1007 |
| 05_canny_strong_blur | 3.401% | 482 |
| 06_adaptive_thresh | 20.126% | 11094 |
| 07_otsu | 17.208% | 879 |
| 08_canny_dilate | 27.343% | 124 |

## sdxl_output_20260523_141849_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 11.342% | 3444 |
| 02_canny_mid | 8.956% | 2012 |
| 03_canny_high | 8.016% | 1543 |
| 04_canny_blur_high | 5.898% | 659 |
| 05_canny_strong_blur | 4.434% | 402 |
| 06_adaptive_thresh | 17.807% | 10306 |
| 07_otsu | 24.807% | 127 |
| 08_canny_dilate | 19.538% | 110 |

## sdxl_output_20260523_141851_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 7.749% | 1634 |
| 02_canny_mid | 4.889% | 822 |
| 03_canny_high | 2.933% | 643 |
| 04_canny_blur_high | 1.359% | 84 |
| 05_canny_strong_blur | 0.764% | 40 |
| 06_adaptive_thresh | 9.298% | 15974 |
| 07_otsu | 23.349% | 403 |
| 08_canny_dilate | 10.818% | 42 |

## sdxl_output_20260523_141854_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 13.379% | 2993 |
| 02_canny_mid | 12.095% | 2245 |
| 03_canny_high | 10.536% | 2020 |
| 04_canny_blur_high | 6.359% | 943 |
| 05_canny_strong_blur | 3.037% | 370 |
| 06_adaptive_thresh | 17.834% | 6048 |
| 07_otsu | 17.750% | 891 |
| 08_canny_dilate | 26.509% | 208 |

## sdxl_output_20260523_144702_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 10.013% | 3192 |
| 02_canny_mid | 8.128% | 2059 |
| 03_canny_high | 6.944% | 1677 |
| 04_canny_blur_high | 4.539% | 633 |
| 05_canny_strong_blur | 2.994% | 307 |
| 06_adaptive_thresh | 14.865% | 10511 |
| 07_otsu | 25.622% | 189 |
| 08_canny_dilate | 17.977% | 85 |

## sdxl_output_20260523_144704_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 11.015% | 2604 |
| 02_canny_mid | 8.983% | 1687 |
| 03_canny_high | 8.069% | 1324 |
| 04_canny_blur_high | 6.117% | 683 |
| 05_canny_strong_blur | 3.506% | 327 |
| 06_adaptive_thresh | 18.388% | 20835 |
| 07_otsu | 17.684% | 469 |
| 08_canny_dilate | 19.569% | 98 |

## sdxl_output_20260523_144707_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 12.948% | 3196 |
| 02_canny_mid | 11.281% | 2426 |
| 03_canny_high | 9.740% | 2119 |
| 04_canny_blur_high | 5.329% | 770 |
| 05_canny_strong_blur | 2.218% | 258 |
| 06_adaptive_thresh | 17.169% | 10037 |
| 07_otsu | 16.709% | 1253 |
| 08_canny_dilate | 24.862% | 259 |

## sdxl_output_20260523_144718_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 12.420% | 3978 |
| 02_canny_mid | 9.219% | 2400 |
| 03_canny_high | 7.746% | 1861 |
| 04_canny_blur_high | 4.995% | 729 |
| 05_canny_strong_blur | 3.430% | 383 |
| 06_adaptive_thresh | 17.588% | 11028 |
| 07_otsu | 26.429% | 277 |
| 08_canny_dilate | 20.393% | 115 |

## sdxl_output_20260523_144720_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 15.375% | 3968 |
| 02_canny_mid | 11.968% | 2472 |
| 03_canny_high | 8.353% | 2095 |
| 04_canny_blur_high | 2.554% | 260 |
| 05_canny_strong_blur | 1.111% | 79 |
| 06_adaptive_thresh | 19.623% | 23836 |
| 07_otsu | 37.284% | 1112 |
| 08_canny_dilate | 26.859% | 35 |

## sdxl_output_20260523_144723_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| 01_canny_low | 14.947% | 3168 |
| 02_canny_mid | 11.211% | 2060 |
| 03_canny_high | 7.569% | 1736 |
| 04_canny_blur_high | 3.060% | 486 |
| 05_canny_strong_blur | 1.081% | 121 |
| 06_adaptive_thresh | 16.064% | 9602 |
| 07_otsu | 16.647% | 727 |
| 08_canny_dilate | 24.802% | 179 |

