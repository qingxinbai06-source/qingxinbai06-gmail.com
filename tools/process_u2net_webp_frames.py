from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageFilter
from tqdm import tqdm


def preprocess(rgb: np.ndarray) -> np.ndarray:
    image = Image.fromarray(rgb).resize((320, 320), Image.Resampling.BILINEAR)
    arr = np.asarray(image).astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    arr = (arr - mean) / std
    return arr.transpose(2, 0, 1)[None, ...].astype(np.float32)


def predict_alpha(session: ort.InferenceSession, rgb: np.ndarray) -> Image.Image:
    input_name = session.get_inputs()[0].name
    output = session.run(None, {input_name: preprocess(rgb)})[0]
    mask = output[0, 0]
    mask = (mask - mask.min()) / max(mask.max() - mask.min(), 1e-6)

    alpha = Image.fromarray((mask * 255).astype(np.uint8), mode="L")
    alpha = alpha.resize((rgb.shape[1], rgb.shape[0]), Image.Resampling.BILINEAR)
    alpha = alpha.filter(ImageFilter.GaussianBlur(radius=0.9))

    arr = np.asarray(alpha).astype(np.float32) / 255.0
    arr = np.clip((arr - 0.14) / 0.78, 0, 1)
    arr = arr * arr * (3 - 2 * arr)
    return Image.fromarray((arr * 255).astype(np.uint8), mode="L")


def crop_frame(frame_bgr: np.ndarray, crop: str) -> np.ndarray:
    if crop == "none":
        return frame_bgr

    h, w = frame_bgr.shape[:2]
    if crop == "center-character":
        x = int(round(w * 0.18))
        y = int(round(h * 0.02))
        cw = int(round(w * 0.64))
        ch = int(round(h * 0.96))
        return frame_bgr[y : y + ch, x : x + cw]

    raise ValueError(f"Unknown crop mode: {crop}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cut out a video with U2Net and export transparent WebP frames.")
    parser.add_argument("--input", required=True, help="Source video path.")
    parser.add_argument("--model", required=True, help="Path to a valid u2net.onnx model.")
    parser.add_argument("--output-dir", required=True, help="Directory for transparent WebP frames.")
    parser.add_argument("--max-fps", type=float, default=24.0, help="Maximum output frame rate.")
    parser.add_argument("--width", type=int, default=720, help="Output frame width after crop/resize.")
    parser.add_argument("--height", type=int, default=720, help="Output frame height after crop/resize.")
    parser.add_argument("--crop", choices=["none", "center-character"], default="none")
    parser.add_argument("--quality", type=int, default=92, help="WebP quality, 1-100.")
    args = parser.parse_args()

    input_path = Path(args.input)
    model_path = Path(args.model)
    output_dir = Path(args.output_dir)

    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if not model_path.exists():
        raise FileNotFoundError(model_path)

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {input_path}")

    source_fps = cap.get(cv2.CAP_PROP_FPS) or 24
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(source_fps / args.max_fps)))
    written = 0

    for index in tqdm(range(total), desc="u2net webp frames"):
        ok, frame = cap.read()
        if not ok:
            break
        if index % step:
            continue

        frame = crop_frame(frame, args.crop)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb = np.asarray(Image.fromarray(rgb).resize((args.width, args.height), Image.Resampling.LANCZOS))
        alpha = predict_alpha(session, rgb)

        rgba = Image.fromarray(rgb, mode="RGB")
        rgba.putalpha(alpha)
        rgba.save(output_dir / f"frame_{written:05d}.webp", format="WEBP", quality=args.quality, method=6)
        written += 1

    cap.release()
    if written == 0:
        raise RuntimeError("No frames were written")

    print(f"Wrote {written} transparent WebP frames to {output_dir}")


if __name__ == "__main__":
    main()
