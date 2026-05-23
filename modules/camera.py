"""Camera capture module (Step F, F-1).

設計 v0.4 の median 合成キャプチャを実装する。

  USB カメラ (Logitech 想定) → n_frames 枚を interval_s 秒間隔で撮影
  → ピクセルごとの中央値合成 → 動体 (ロボットアームなど) を統計的に除去

median 合成の原理:
  各ピクセルで時間方向の median を取る。アームが各時刻でフレーム内の
  別位置にいれば、各ピクセルの「アームに覆われた時刻」は少数派になり、
  median によって自然に消える。

  static (止まっている) ロボット・ユーザの手はそのまま残るが、
  描画システムでは描画中のアーム = 動体なので想定通り動く。

設計 v0.4 で挙げられた破綻パターン:
  - 全フレームで動体が同位置 → 残る (まれ。ready pose 中の冒頭撮影で対処)
  - 撮影中にユーザが激しく動く → ユーザの手も消える (手は描画対象外で OK)
  - ぼやけた合成 → mode/min 切替の選択肢を burst 取得経由で残してある

API:
  Camera(device_id, width, height, fps, rotation_deg, ...)
  - capture_single() -> ndarray            # 回転適用済み
  - capture_median(n_frames, interval_s) -> ndarray  # 回転適用済み
  - capture_burst(n_frames, interval_s) -> list[ndarray]  # 回転適用済み生フレーム列
  - set_rotation(deg) / get_rotation()    # 動的変更 (キャリブ GUI 用)
  - open() / close() / __enter__ / __exit__

回転の規約:
  rotation_deg は {0, 90, 180, 270} のみ。read() で 1 枚読むたびに
  cv2.rotate() を適用する。これにより capture_single/burst/median すべて
  「回転後の画像」を返す。回転後は width と height が入れ替わる
  (90/270 の場合)。self.width/self.height は「カメラ生出力」の値を
  そのまま保持し、回転後サイズは get_rotated_size() で取得する。

See:
  docs/20260521_1757_drawing_system_v04_design.md (median 設計の出典)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np


log = logging.getLogger("camera")


# ----- デフォルト値 (Logitech UVC カメラ想定) ------------------------------
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 30
DEFAULT_DEVICE_ID = 0
DEFAULT_WARMUP_FRAMES = 5
DEFAULT_FOURCC = "MJPG"  # Logitech 系は MJPG 指定で 720p/1080p 30fps が安定
DEFAULT_ROTATION_DEG = 0

# median 合成のデフォルト
DEFAULT_N_FRAMES = 10
DEFAULT_INTERVAL_S = 0.2

# rotation_deg → cv2.rotate のコード
_ROTATION_CODE = {
    0: None,
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}
VALID_ROTATION_DEGS = tuple(_ROTATION_CODE.keys())


class CameraError(RuntimeError):
    """カメラ関係の例外を 1 つにまとめる。"""


def _normalize_rotation_deg(deg: int) -> int:
    """rotation_deg を [0, 360) に正規化し、{0, 90, 180, 270} のいずれかに揃える。"""
    if deg is None:
        return 0
    d = int(deg) % 360
    if d not in _ROTATION_CODE:
        raise ValueError(
            f"rotation_deg must be one of {VALID_ROTATION_DEGS}, got {deg}"
        )
    return d


class Camera:
    """USB カメラのラッパ。OpenCV VideoCapture を内部に持つ。

    使い方:

        cam = Camera(device_id=0, rotation_deg=90)
        cam.open()
        img = cam.capture_median()  # 既に回転適用済み
        cam.close()

    または:

        with Camera(device_id=0, rotation_deg=90) as cam:
            img = cam.capture_median()

    Note:
      open() で fourcc / width / height / fps を設定し、warmup フレームを
      読み捨てて自動露出を安定させる。設定値が機種で効かなくても続行する
      (warning を出すのみ)。
      rotation_deg はカメラ生出力に対する後処理として全 read() に適用される。
    """

    def __init__(
        self,
        device_id: int = DEFAULT_DEVICE_ID,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        fps: int = DEFAULT_FPS,
        fourcc: str = DEFAULT_FOURCC,
        n_warmup_frames: int = DEFAULT_WARMUP_FRAMES,
        rotation_deg: int = DEFAULT_ROTATION_DEG,
        verbose: bool = False,
    ):
        self.device_id = device_id
        self.width = width
        self.height = height
        self.fps = fps
        self.fourcc = fourcc
        self.n_warmup_frames = n_warmup_frames
        self.verbose = verbose

        # rotation はバリデートして保持
        self._rotation_deg = _normalize_rotation_deg(rotation_deg)

        self._cap: Optional[cv2.VideoCapture] = None

    # ----- 回転設定 -------------------------------------------------------

    def get_rotation(self) -> int:
        """現在の rotation_deg ({0, 90, 180, 270})。"""
        return self._rotation_deg

    def set_rotation(self, deg: int) -> None:
        """rotation_deg を動的に変更する (キャリブ GUI 用)。"""
        new_deg = _normalize_rotation_deg(deg)
        if new_deg != self._rotation_deg and self.verbose:
            log.info("[camera] rotation %d -> %d deg", self._rotation_deg, new_deg)
        self._rotation_deg = new_deg

    def get_rotated_size(self) -> tuple[int, int]:
        """回転後の (width, height)。0/180 は (self.width, self.height)、
        90/270 は (self.height, self.width)。
        """
        if self._rotation_deg in (90, 270):
            return (self.height, self.width)
        return (self.width, self.height)

    @staticmethod
    def _apply_rotation(frame: np.ndarray, rotation_deg: int) -> np.ndarray:
        """frame に rotation_deg の回転を適用して返す。0 ならそのまま。"""
        code = _ROTATION_CODE[rotation_deg]
        if code is None:
            return frame
        return cv2.rotate(frame, code)

    # ----- 文脈マネージャ -------------------------------------------------

    def __enter__(self) -> "Camera":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ----- 開く / 閉じる --------------------------------------------------

    def open(self) -> None:
        """カメラを開いて解像度/fps/fourcc を設定、warmup フレームを読み捨てる。"""
        if self._cap is not None:
            if self.verbose:
                log.info("[camera] already open, skipping")
            return

        if self.verbose:
            log.info(
                "[camera] opening device=%d, target=%dx%d@%dfps, "
                "fourcc=%s, rotation=%d deg",
                self.device_id, self.width, self.height, self.fps,
                self.fourcc, self._rotation_deg,
            )

        cap = cv2.VideoCapture(self.device_id)
        if not cap.isOpened():
            raise CameraError(
                f"failed to open camera device {self.device_id} "
                "(check `ls /dev/video*` and v4l2-ctl)"
            )

        # fourcc を先に設定する (機種によっては解像度設定の前に必要)
        try:
            fourcc_code = cv2.VideoWriter_fourcc(*self.fourcc)
            cap.set(cv2.CAP_PROP_FOURCC, fourcc_code)
        except Exception as e:
            if self.verbose:
                log.warning("[camera] fourcc setup failed: %s", e)

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)

        # 実際の設定値を取得 (機種によっては希望値が反映されないことがある)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        if self.verbose:
            log.info(
                "[camera] actual = %dx%d@%.1ffps",
                actual_w, actual_h, actual_fps,
            )

        # warmup: 自動露出が安定するまで読み捨て
        for i in range(self.n_warmup_frames):
            ret, _ = cap.read()
            if not ret:
                if self.verbose:
                    log.warning("[camera] warmup frame %d/%d failed",
                                i + 1, self.n_warmup_frames)
                # 1 枚失敗しても続行 (autofocus 初動で起きうる)
                time.sleep(0.05)

        self._cap = cap
        if self.verbose:
            log.info("[camera] open OK (warmup %d frames)", self.n_warmup_frames)

    def close(self) -> None:
        """カメラを release する。"""
        if self._cap is None:
            return
        if self.verbose:
            log.info("[camera] closing")
        self._cap.release()
        self._cap = None

    # ----- キャプチャ -----------------------------------------------------

    def _require_open(self) -> cv2.VideoCapture:
        if self._cap is None:
            raise CameraError("camera not open; call open() or use with-statement")
        return self._cap

    def _read_one(self, retries: int = 3) -> np.ndarray:
        """1 枚読む。失敗時は短い間隔でリトライ。

        rotation_deg が 0 でなければ cv2.rotate() を適用してから返す。
        """
        cap = self._require_open()
        last_err = None
        for attempt in range(retries):
            ret, frame = cap.read()
            if ret and frame is not None:
                # 回転を適用してから返す。
                # これにより capture_single / capture_burst / capture_median
                # 全部に rotation_deg が透過的に効く。
                return self._apply_rotation(frame, self._rotation_deg)
            last_err = f"attempt {attempt + 1}/{retries} ret={ret}"
            time.sleep(0.05)
        raise CameraError(f"frame read failed: {last_err}")

    def capture_single(self) -> np.ndarray:
        """1 枚だけ撮る (BGR uint8, shape=(H, W, 3))。デバッグ/比較用。

        返る画像は rotation_deg 適用済み。
        """
        t0 = time.time()
        frame = self._read_one()
        if self.verbose:
            log.info(
                "[camera] single shot: shape=%s (rot=%d), elapsed=%.3fs",
                frame.shape, self._rotation_deg, time.time() - t0,
            )
        return frame

    def capture_burst(
        self,
        n_frames: int = DEFAULT_N_FRAMES,
        interval_s: float = DEFAULT_INTERVAL_S,
    ) -> List[np.ndarray]:
        """n_frames 枚を interval_s 秒間隔で撮影し、フレーム列を返す。

        各フレームは rotation_deg 適用済み。median 以外の合成方法
        (mode, min, max) を後で試したいときに使う。
        """
        if n_frames < 1:
            raise ValueError(f"n_frames must be >= 1, got {n_frames}")
        if interval_s < 0:
            raise ValueError(f"interval_s must be >= 0, got {interval_s}")

        t0 = time.time()
        frames: List[np.ndarray] = []
        for i in range(n_frames):
            frame = self._read_one()
            frames.append(frame)
            if self.verbose:
                log.info("[camera]   burst %2d/%d shape=%s",
                         i + 1, n_frames, frame.shape)
            if i < n_frames - 1 and interval_s > 0:
                time.sleep(interval_s)
        if self.verbose:
            log.info(
                "[camera] burst done: n=%d, elapsed=%.2fs",
                n_frames, time.time() - t0,
            )
        return frames

    def capture_median(
        self,
        n_frames: int = DEFAULT_N_FRAMES,
        interval_s: float = DEFAULT_INTERVAL_S,
    ) -> np.ndarray:
        """median 合成キャプチャ。動体を統計的に除去する。

        各フレームは _read_one() の段階で既に rotation_deg 適用済みなので、
        median 結果も回転後の座標系で返る。

        Returns:
            BGR uint8 画像 (shape=(H, W, 3))。
        """
        t0 = time.time()
        frames = self.capture_burst(n_frames=n_frames, interval_s=interval_s)
        # np.median は float64 で返るので uint8 にキャスト
        stack = np.stack(frames, axis=0)  # (n, H, W, 3)
        median = np.median(stack, axis=0).astype(np.uint8)
        if self.verbose:
            log.info(
                "[camera] median composite: shape=%s, total elapsed=%.2fs",
                median.shape, time.time() - t0,
            )
        return median


# ----- ヘルパ ---------------------------------------------------------------


def save_bgr(image: np.ndarray, path: Path) -> None:
    """BGR ndarray を PNG として保存。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)


# ----- smoke test -----------------------------------------------------------


def _smoke_test() -> int:
    """python3 -m modules.camera で実行する smoke test。

    1. Camera を開く
    2. single ショットを 1 枚撮って single.png 保存
    3. burst で 10 枚撮って burst_00〜09.png 保存
    4. burst から median を計算して median.png 保存
    5. 比較用に min/max 合成も保存 (median 以外の挙動を見るため)
    6. logs/camera_<timestamp>/ に全部出力
    """
    import argparse
    from datetime import datetime

    parser = argparse.ArgumentParser(description="Camera module smoke test")
    parser.add_argument("--device", type=int, default=DEFAULT_DEVICE_ID)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--n-frames", type=int, default=DEFAULT_N_FRAMES)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument(
        "--rotation-deg", type=int, default=DEFAULT_ROTATION_DEG,
        choices=list(VALID_ROTATION_DEGS),
        help="出力画像を回転 (0/90/180/270)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="保存先 (デフォルト: ~/draw_piper/logs/camera_<timestamp>/)",
    )
    parser.add_argument(
        "--countdown", type=int, default=3,
        help="撮影開始までの秒数 (この間に手をかざすなどテスト動作)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # 出力先決定
    if args.out_dir is None:
        # repo root を推定 (modules/camera.py から見て 1 つ上)
        repo_root = Path(__file__).resolve().parent.parent
        out_dir = repo_root / "logs" / f"camera_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    else:
        out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=== Camera smoke test ===")
    print(f"output: {out_dir}")

    cam = Camera(
        device_id=args.device,
        width=args.width,
        height=args.height,
        rotation_deg=args.rotation_deg,
        verbose=True,
    )
    cam.open()

    try:
        # ---- countdown ----
        if args.countdown > 0:
            print(f"\n撮影開始まで {args.countdown} 秒。")
            print("カメラ前に手をかざしたり動かしたりして、median で消えるか確認してください。")
            for s in range(args.countdown, 0, -1):
                print(f"  {s}...")
                time.sleep(1)

        # ---- 1) single ----
        print("\n[1/3] single shot")
        single = cam.capture_single()
        save_bgr(single, out_dir / "single.png")
        print(f"  saved: {out_dir / 'single.png'} shape={single.shape}")

        # ---- 2) burst ----
        print(f"\n[2/3] burst ({args.n_frames} frames @ {args.interval}s)")
        frames = cam.capture_burst(n_frames=args.n_frames, interval_s=args.interval)
        for i, f in enumerate(frames):
            save_bgr(f, out_dir / f"burst_{i:02d}.png")
        print(f"  saved: {len(frames)} burst frames")

        # ---- 3) median / min / max ----
        print("\n[3/3] composites")
        stack = np.stack(frames, axis=0)
        median = np.median(stack, axis=0).astype(np.uint8)
        save_bgr(median, out_dir / "median.png")
        # 比較用: 最小値合成 (黒い線が強調されるはず、ペンの跡を取りたいとき有用)
        # 最大値合成 (白背景に対して影が消える)
        minc = np.min(stack, axis=0)
        maxc = np.max(stack, axis=0)
        save_bgr(minc, out_dir / "min.png")
        save_bgr(maxc, out_dir / "max.png")
        print(f"  saved: median.png, min.png, max.png")

    finally:
        cam.close()

    # ---- レポート ----
    print("\n=== smoke test OK ===")
    print(f"全成果物: {out_dir}/")
    print("確認手順:")
    print(f"  1. single.png と burst_NN.png に動体 (手など) が映っているか確認")
    print(f"  2. median.png で動体が消えているか確認")
    print(f"  3. min.png / max.png は参考、median との挙動差を見比べる")
    return 0


if __name__ == "__main__":
    raise SystemExit(_smoke_test())
