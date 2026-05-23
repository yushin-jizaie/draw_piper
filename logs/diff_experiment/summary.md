# Diff detection experiment summary

Date: 2026-05-23 15:02:31

User image: `test_sketch.jpg`
  - line_pixel_ratio: 0.498%
  - estimated_strokes: 3

各生成画像 × dilate kernel size での差分結果

- kernel=5: ほぼピンポイント差分(リング残るかも)
- kernel=11: 標準
- kernel=21: ユーザ線周辺をしっかり除外
- kernel=31: 広めに除外(描き足し部分が遠方になる)
- kernel=51: 大きく除外(ユーザ絵周辺全部消える)

目標: ストロークが 50〜300、線が顔の意味のある部分に残ること

## sdxl_output_20260523_140432_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 3.293% | 324 |
| diff_k5 | 3.189% | 344 |
| diff_k11 | 3.096% | 348 |
| diff_k21 | 3.068% | 314 |
| diff_k31 | 3.068% | 314 |
| diff_k51 | 3.068% | 315 |

## sdxl_output_20260523_140435_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 1.054% | 75 |
| diff_k5 | 0.942% | 150 |
| diff_k11 | 0.896% | 73 |
| diff_k21 | 0.896% | 73 |
| diff_k31 | 0.896% | 73 |
| diff_k51 | 0.896% | 73 |

## sdxl_output_20260523_140438_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 1.183% | 83 |
| diff_k5 | 1.138% | 83 |
| diff_k11 | 1.134% | 86 |
| diff_k21 | 1.130% | 82 |
| diff_k31 | 1.130% | 82 |
| diff_k51 | 1.130% | 82 |

## sdxl_output_20260523_141456_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 3.353% | 232 |
| diff_k5 | 3.231% | 250 |
| diff_k11 | 3.183% | 256 |
| diff_k21 | 3.074% | 230 |
| diff_k31 | 3.051% | 228 |
| diff_k51 | 3.002% | 227 |

## sdxl_output_20260523_141458_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 3.920% | 446 |
| diff_k5 | 3.832% | 514 |
| diff_k11 | 3.743% | 450 |
| diff_k21 | 3.584% | 442 |
| diff_k31 | 3.554% | 442 |
| diff_k51 | 3.479% | 437 |

## sdxl_output_20260523_141501_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 2.747% | 304 |
| diff_k5 | 2.617% | 307 |
| diff_k11 | 2.592% | 342 |
| diff_k21 | 2.527% | 301 |
| diff_k31 | 2.510% | 298 |
| diff_k51 | 2.484% | 293 |

## sdxl_output_20260523_141512_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 2.919% | 324 |
| diff_k5 | 2.803% | 329 |
| diff_k11 | 2.732% | 358 |
| diff_k21 | 2.660% | 320 |
| diff_k31 | 2.647% | 318 |
| diff_k51 | 2.620% | 314 |

## sdxl_output_20260523_141514_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 5.770% | 432 |
| diff_k5 | 5.686% | 542 |
| diff_k11 | 5.602% | 434 |
| diff_k21 | 5.448% | 438 |
| diff_k31 | 5.412% | 439 |
| diff_k51 | 5.333% | 433 |

## sdxl_output_20260523_141517_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 1.458% | 147 |
| diff_k5 | 1.458% | 147 |
| diff_k11 | 1.457% | 147 |
| diff_k21 | 1.454% | 148 |
| diff_k31 | 1.452% | 147 |
| diff_k51 | 1.444% | 147 |

## sdxl_output_20260523_141833_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 3.670% | 409 |
| diff_k5 | 3.535% | 450 |
| diff_k11 | 3.475% | 428 |
| diff_k21 | 3.398% | 406 |
| diff_k31 | 3.371% | 400 |
| diff_k51 | 3.329% | 382 |

## sdxl_output_20260523_141835_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 1.951% | 154 |
| diff_k5 | 1.838% | 150 |
| diff_k11 | 1.726% | 201 |
| diff_k21 | 1.685% | 147 |
| diff_k31 | 1.684% | 145 |
| diff_k51 | 1.681% | 145 |

## sdxl_output_20260523_141838_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 3.401% | 482 |
| diff_k5 | 3.281% | 542 |
| diff_k11 | 3.241% | 481 |
| diff_k21 | 3.235% | 482 |
| diff_k31 | 3.230% | 481 |
| diff_k51 | 3.218% | 479 |

## sdxl_output_20260523_141849_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 4.434% | 402 |
| diff_k5 | 4.326% | 430 |
| diff_k11 | 4.241% | 422 |
| diff_k21 | 4.121% | 422 |
| diff_k31 | 4.070% | 395 |
| diff_k51 | 3.994% | 376 |

## sdxl_output_20260523_141851_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 0.764% | 40 |
| diff_k5 | 0.672% | 50 |
| diff_k11 | 0.627% | 69 |
| diff_k21 | 0.604% | 38 |
| diff_k31 | 0.604% | 38 |
| diff_k51 | 0.604% | 38 |

## sdxl_output_20260523_141854_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 3.037% | 370 |
| diff_k5 | 2.889% | 408 |
| diff_k11 | 2.872% | 368 |
| diff_k21 | 2.871% | 366 |
| diff_k31 | 2.869% | 366 |
| diff_k51 | 2.865% | 366 |

## sdxl_output_20260523_144702_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 2.994% | 307 |
| diff_k5 | 2.874% | 331 |
| diff_k11 | 2.791% | 332 |
| diff_k21 | 2.741% | 300 |
| diff_k31 | 2.720% | 299 |
| diff_k51 | 2.670% | 292 |

## sdxl_output_20260523_144704_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 3.506% | 327 |
| diff_k5 | 3.397% | 376 |
| diff_k11 | 3.315% | 341 |
| diff_k21 | 3.285% | 322 |
| diff_k31 | 3.278% | 323 |
| diff_k51 | 3.252% | 322 |

## sdxl_output_20260523_144707_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 2.218% | 258 |
| diff_k5 | 2.112% | 280 |
| diff_k11 | 2.058% | 268 |
| diff_k21 | 2.042% | 251 |
| diff_k31 | 2.042% | 251 |
| diff_k51 | 2.038% | 251 |

## sdxl_output_20260523_144718_1step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 3.430% | 383 |
| diff_k5 | 3.313% | 403 |
| diff_k11 | 3.249% | 411 |
| diff_k21 | 3.207% | 381 |
| diff_k31 | 3.182% | 381 |
| diff_k51 | 3.138% | 373 |

## sdxl_output_20260523_144720_2step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 1.111% | 79 |
| diff_k5 | 0.960% | 151 |
| diff_k11 | 0.928% | 95 |
| diff_k21 | 0.837% | 75 |
| diff_k31 | 0.832% | 75 |
| diff_k51 | 0.817% | 73 |

## sdxl_output_20260523_144723_4step.png

| 手法 | line_pixel_ratio | estimated_strokes |
|---|---|---|
| generated_canny | 1.081% | 121 |
| diff_k5 | 1.081% | 121 |
| diff_k11 | 1.081% | 121 |
| diff_k21 | 1.081% | 121 |
| diff_k31 | 1.080% | 121 |
| diff_k51 | 1.076% | 121 |

