"""Camera median capture utility (skeleton)
"""
import time
import numpy as np


def capture_median(cap, n_frames=10, interval=0.2):
    frames = []
    for _ in range(n_frames):
        ret, frame = cap.read()
        if ret:
            frames.append(frame)
        time.sleep(interval)
    if len(frames) == 0:
        return None
    return np.median(np.stack(frames), axis=0).astype(np.uint8)
