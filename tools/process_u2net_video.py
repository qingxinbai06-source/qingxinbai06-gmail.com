from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageFilter
from tqdm import tqdm


def preprocess(rgb: np.ndarray) -> np.ndarray:
    image = Image.fromarray(rgb).resize((320, 320), Image.Resampling.BILINEAR)
    arr = np.asarray(image).astype(np.float32) / 255.0
    arr = (arr - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
        [0.229, 0.224, 0.225], dtype=np.float32
    )
    return arr.transpose(2, 0, 1)[None, ...].astype(np.float32)


def predict_mask(session: ort.InferenceSession, rgb: np.ndarray) -> Image.Image:
    input_name = session.get_inputs()[0].name
    output = session.run(None, {input_name: preprocess(rgb)})[0]
    mask = output[0, 0]
    mask = (mask - mask.min()) / max(mask.max() - mask.min(), 1e-6)
    mask_img = Image.fromarray((mask * 255).astype(np.uint8), mode="L")
    mask_img = mask_img.resize((rgb.shape[1], rgb.shape[0]), Image.Resampling.BILINEAR)
    mask_img = mask_img.filter(ImageFilter.GaussianBlur(radius=1.2))

    arr = np.asarray(mask_img).astype(np.float32) / 255.0
    arr = np.clip((arr - 0.18) / 0.74, 0, 1)
    arr = arr * arr * (3 - 2 * arr)
    return Image.fromarray((arr * 255).astype(np.uint8), mode="L")


def crop_character(frame_bgr: np.ndarray) -> np.ndarray:
    h, w = frame_bgr.shape[:2]
    x = int(round(w * 0.26))
    y = int(round(h * 0.04))
    cw = int(round(w * 0.50))
    ch = int(round(h * 0.94))
    return frame_bgr[y : y + ch, x : x + cw]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--frames-dir", default="assets/bai-qingxin/u2net_frames")
    parser.add_argument("--max-fps", type=float, default=18)
    parser.add_argument("--width", type=int, default=540)
    parser.add_argument("--height", type=int, default=810)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    frames_dir = Path(args.frames_dir)
    if frames_dir.exists():
      shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {input_path}")

    source_fps = cap.get(cv2.CAP_PROP_FPS) or 24
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(source_fps / args.max_fps)))
    out_fps = source_fps / step

    session = ort.InferenceSession(str(args.model), providers=["CPUExecutionProvider"])

    written = 0
    for index in tqdm(range(total), desc="u2net video matte"):
        ok, frame = cap.read()
        if not ok:
            break
        if index % step:
            continue

        crop = crop_character(frame)
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        rgb = np.asarray(
            Image.fromarray(rgb).resize((args.width, args.height), Image.Resampling.LANCZOS)
        )
        alpha = predict_mask(session, rgb)
        rgba = Image.fromarray(rgb, mode="RGB")
        rgba.putalpha(alpha)
        rgba.save(frames_dir / f"{written:05d}.png")
        written += 1

    cap.release()
    if written == 0:
        raise RuntimeError("No frames were written")

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",
        "-framerate",
        f"{out_fps:.6f}",
        "-i",
        str(frames_dir / "%05d.png"),
        "-an",
        "-c:v",
        "libvpx-vp9",
        "-pix_fmt",
        "yuva420p",
        "-auto-alt-ref",
        "0",
        "-b:v",
        "0",
        "-crf",
        "28",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)
    print(f"Wrote {written} frames at {out_fps:.2f} fps -> {output_path}")


if __name__ == "__main__":
    main()
