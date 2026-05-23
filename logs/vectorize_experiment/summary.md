# Vectorize prelim experiment summary

Date: 2026-05-23 15:09:35

## 目標値

- **strokes**: 30〜150 (ペンアップ/ダウン回数の上限)
- **total_points**: 500〜2000 (軌道点の総数)
- **total_length**: 10000〜50000 px (実描画長)

## 各画像の処理推移

### 141838_4step_k51

- Initial components: 479
- After CLOSE: 214
- After skeletonize: 214

#### 連結成分フィルタ (min_pixels)

| min_pixels | components kept |
|---|---|
| 10 | 452 |
| 30 | 356 |
| 50 | 251 |
| 100 | 91 |
| 200 | 14 |

#### ポリライン化 (epsilon、min_length=2)

| epsilon | strokes | total_points | total_length |
|---|---|---|---|
| 1.0 | 223 | 4160 | 47546 |
| 2.0 | 223 | 2389 | 46976 |
| 3.0 | 223 | 1817 | 46557 |
| 5.0 | 223 | 1373 | 45868 |

#### 最終 (epsilon=2.0、min_length変化)

| min_length | strokes | total_points | total_length |
|---|---|---|---|
| 3 | 223 | 2389 | 46976 |
| 5 | 204 | 2329 | 44685 |
| 10 | 100 | 1609 | 31894 |
| 20 | 21 | 594 | 14438 |

### 141517_4step_k51

- Initial components: 147
- After CLOSE: 92
- After skeletonize: 92

#### 連結成分フィルタ (min_pixels)

| min_pixels | components kept |
|---|---|
| 10 | 142 |
| 30 | 119 |
| 50 | 97 |
| 100 | 49 |
| 200 | 15 |

#### ポリライン化 (epsilon、min_length=2)

| epsilon | strokes | total_points | total_length |
|---|---|---|---|
| 1.0 | 92 | 1827 | 24624 |
| 2.0 | 92 | 980 | 24415 |
| 3.0 | 92 | 744 | 24297 |
| 5.0 | 92 | 557 | 24023 |

#### 最終 (epsilon=2.0、min_length変化)

| min_length | strokes | total_points | total_length |
|---|---|---|---|
| 3 | 92 | 980 | 24415 |
| 5 | 85 | 958 | 23660 |
| 10 | 39 | 651 | 16345 |
| 20 | 7 | 213 | 6247 |

### 144723_4step_k51

- Initial components: 121
- After CLOSE: 68
- After skeletonize: 68

#### 連結成分フィルタ (min_pixels)

| min_pixels | components kept |
|---|---|
| 10 | 119 |
| 30 | 106 |
| 50 | 76 |
| 100 | 39 |
| 200 | 9 |

#### ポリライン化 (epsilon、min_length=2)

| epsilon | strokes | total_points | total_length |
|---|---|---|---|
| 1.0 | 68 | 1334 | 17392 |
| 2.0 | 68 | 731 | 17228 |
| 3.0 | 68 | 538 | 17120 |
| 5.0 | 68 | 430 | 17009 |

#### 最終 (epsilon=2.0、min_length変化)

| min_length | strokes | total_points | total_length |
|---|---|---|---|
| 3 | 68 | 731 | 17228 |
| 5 | 60 | 705 | 16103 |
| 10 | 26 | 478 | 10547 |
| 20 | 8 | 236 | 5674 |

### 141851_2step_k51

- Initial components: 38
- After CLOSE: 29
- After skeletonize: 29

#### 連結成分フィルタ (min_pixels)

| min_pixels | components kept |
|---|---|
| 10 | 38 |
| 30 | 36 |
| 50 | 32 |
| 100 | 23 |
| 200 | 10 |

#### ポリライン化 (epsilon、min_length=2)

| epsilon | strokes | total_points | total_length |
|---|---|---|---|
| 1.0 | 29 | 705 | 11326 |
| 2.0 | 29 | 398 | 11254 |
| 3.0 | 29 | 301 | 11210 |
| 5.0 | 29 | 224 | 11142 |

#### 最終 (epsilon=2.0、min_length変化)

| min_length | strokes | total_points | total_length |
|---|---|---|---|
| 3 | 29 | 398 | 11254 |
| 5 | 29 | 398 | 11254 |
| 10 | 17 | 314 | 8941 |
| 20 | 5 | 165 | 5465 |

