"""Frame extraction, metadata analysis and perceptual hashing (OpenCV + Pillow)."""
import os, subprocess, json, shutil
import cv2, numpy as np
from PIL import Image


def video_metadata(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    duration = round(frames / fps, 2) if fps else 0
    meta = {"duration_s": duration, "fps": round(fps, 1), "width": w, "height": h, "frames": int(frames),
            "size_mb": round(os.path.getsize(path) / 1048576, 2), "created_tag": None, "flags": []}
    if shutil.which("ffprobe"):  # container creation timestamp, if present
        try:
            out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
                                 capture_output=True, text=True, timeout=20).stdout
            meta["created_tag"] = (json.loads(out).get("format", {}).get("tags", {}) or {}).get("creation_time")
        except Exception:
            pass
    if not frames or not w:
        meta["flags"].append("Video could not be decoded")
    if duration and duration < 8:
        meta["flags"].append(f"Very short clip ({duration}s) — too brief to inspect a device")
    if w and min(w, h) < 480:
        meta["flags"].append(f"Low resolution ({w}×{h}) — identifiers may be unreadable")
    if not meta["created_tag"]:
        meta["flags"].append("No embedded creation timestamp")
    return meta


def extract_frames(path, out_dir, n=6, max_side=960):
    os.makedirs(out_dir, exist_ok=True)
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    saved = []
    if total <= 0:
        cap.release(); return saved
    for i, idx in enumerate(np.linspace(0, max(total - 2, 0), n).astype(int)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        s = max_side / max(h, w)
        if s < 1:
            frame = cv2.resize(frame, (int(w * s), int(h * s)))
        p = os.path.join(out_dir, f"f{i}.jpg")
        cv2.imwrite(p, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        saved.append(p)
    cap.release()
    return saved


def phash(path_or_img, size=8):
    """Difference hash -> 16 hex chars. Robust to resize/recompression; used for duplicate detection."""
    im = path_or_img if isinstance(path_or_img, Image.Image) else Image.open(path_or_img)
    g = np.asarray(im.convert("L").resize((size + 1, size), Image.LANCZOS), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return "".join(f"{int(''.join('1' if b else '0' for b in bits[i:i+4]), 2):x}" for i in range(0, len(bits), 4))


def hamming(a, b):
    if not a or not b or len(a) != len(b):
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def frame_similarity(paths_a, paths_b):
    """Mean best-match colour-histogram correlation + hash distance between two frame sets (0..1 similarity)."""
    def hist(p):
        im = cv2.imread(p)
        im = cv2.resize(im, (256, 256))
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        hh = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
        return cv2.normalize(hh, hh).flatten()
    if not paths_a or not paths_b:
        return 0.0, 64.0
    ha, hb = [hist(p) for p in paths_a], [hist(p) for p in paths_b]
    corr = [max(cv2.compareHist(x.astype("float32"), y.astype("float32"), cv2.HISTCMP_CORREL) for y in hb) for x in ha]
    pa, pb = [phash(p) for p in paths_a], [phash(p) for p in paths_b]
    dist = [min(hamming(x, y) for y in pb) for x in pa]
    return float(np.clip(np.mean(corr), 0, 1)), float(np.mean(dist))
