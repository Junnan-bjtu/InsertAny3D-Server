#!/usr/bin/env python3
"""Run HPSv2 and center-view CLIP-I for one InsertAny3D task.

The metric consumes existing Step 6 PNGs only. HPSv2 scores the canonical
six inserted views; CLIP-I compares the benchmark center render nearest to the
task camera pitch with the image-edit result used to create the task.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from utils.view_selection import select_six_views


ROOT = Path(__file__).resolve().parent
HPS_ROOT = ROOT / "HPSv2"
if str(HPS_ROOT) not in sys.path:
    sys.path.insert(0, str(HPS_ROOT))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="InsertAny3D 单 run/task HPSv2 + CLIP-I 评测")
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--prompt")
    parser.add_argument("--hps-version", default="v2.0", choices=("v2.0", "v2.1"))
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--clip-checkpoint", type=Path)
    parser.add_argument("--edited-image", type=Path,
                        help="image-edit center 图；默认 <run>/<task>/edited/center.png")
    parser.add_argument("--pitches", nargs=2, type=float, metavar=("LOW", "HIGH"))
    parser.add_argument("--views", type=int, help="每个俯视角的总 view 数，用于校验文件名")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def task_manifest(run_root: Path, task_id: str) -> dict:
    path = run_root / task_id / "task_manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"缺少任务 manifest: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"任务 manifest 不是 JSON 对象: {path}")
    return value


def _mean(values: Iterable[float]) -> float:
    values = [float(value) for value in values]
    if not values:
        raise ValueError("没有可聚合的 metric 分数")
    return float(sum(values) / len(values))


def aggregate_scores(records: list[dict]) -> dict:
    """Aggregate the six scalar HPSv2 scores by useful view dimensions."""
    by_pitch: dict[str, list[float]] = {}
    by_view: dict[str, list[float]] = {}
    for item in records:
        by_pitch.setdefault(str(item["pitch"]), []).append(float(item["score"]))
        by_view.setdefault(str(item["label"]), []).append(float(item["score"]))
    mean_by_pitch = {key: _mean(values) for key, values in by_pitch.items()}
    mean_by_view = {key: _mean(values) for key, values in by_view.items()}
    return {
        "mean": _mean(item["score"] for item in records),
        "meanByPitch": mean_by_pitch,
        "meanByView": mean_by_view,
        # HPSv2 has no native semantic dimensions. These are explicit view
        # groupings so the UI does not imply that a scalar score is multi-head.
        "dimensions": {
            "pitch": mean_by_pitch,
            "view": mean_by_view,
        },
    }


def _load_rgb(path: Path):
    from PIL import Image

    with Image.open(path) as image:
        return image.convert("RGB")


def _fit_image(image, width: int, height: int):
    from PIL import Image

    fitted = image.copy()
    resampling = getattr(Image, "Resampling", Image)
    fitted.thumbnail((width, height), resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), (24, 24, 24))
    left = (width - fitted.width) // 2
    top = (height - fitted.height) // 2
    canvas.paste(fitted, (left, top))
    return canvas


def _new_font():
    from PIL import ImageFont

    try:
        return ImageFont.truetype("DejaVuSans.ttf", 20)
    except (OSError, IOError):
        return ImageFont.load_default()


def write_hps_summary(records: list[dict], output: Path) -> None:
    """Write a horizontal six-view contact sheet with one score per tile."""
    from PIL import Image, ImageDraw

    tile_width, image_height, caption_height = 224, 224, 58
    font = _new_font()
    canvas = Image.new("RGB", (tile_width * len(records), image_height + caption_height), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)
    for index, item in enumerate(records):
        x = index * tile_width
        tile = _fit_image(_load_rgb(Path(str(item["path"]))), tile_width, image_height)
        canvas.paste(tile, (x, 0))
        label = f"{item['label']} / pitch {float(item['pitch']):g}"
        score = f"HPSv2 {float(item['score']):.4f}"
        draw.text((x + 8, image_height + 5), label, fill=(235, 235, 235), font=font)
        draw.text((x + 8, image_height + 31), score, fill=(120, 210, 255), font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def write_clip_summary(reference: Path, render: Path, score: float, output: Path) -> None:
    """Write the paired image-edit/final-render CLIP-I contact sheet."""
    from PIL import Image, ImageDraw

    tile_width, image_height, caption_height = 420, 420, 76
    font = _new_font()
    canvas = Image.new("RGB", (tile_width * 2, image_height + caption_height), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)
    for index, (path, label) in enumerate(((reference, "image edit result"),
                                           (render, "3D inserted render"))):
        x = index * tile_width
        tile = _fit_image(_load_rgb(path), tile_width, image_height)
        canvas.paste(tile, (x, 0))
        draw.text((x + 10, image_height + 8), label, fill=(235, 235, 235), font=font)
        caption = f"CLIP-I {score:.4f}" if index == 1 else "center view pair"
        draw.text((x + 10, image_height + 42), caption, fill=(120, 210, 255), font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def _relative_task_path(path: Path, run_root: Path, task_id: str) -> str:
    task_root = (run_root / task_id).resolve()
    try:
        return path.resolve().relative_to(task_root).as_posix()
    except ValueError:
        return path.name


def _choose_center(selected: list[dict], manifest: dict) -> dict:
    centers = [item for item in selected if item.get("label") == "center"]
    if not centers:
        raise ValueError("六视角选择结果缺少 center 视角")
    target_pitch = manifest.get("pitch")
    try:
        target_pitch = float(target_pitch)
    except (TypeError, ValueError):
        target_pitch = float(centers[0]["pitch"])
    return min(centers, key=lambda item: abs(float(item["pitch"]) - target_pitch))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_root = args.run_root.expanduser().resolve()
    manifest = task_manifest(run_root, args.task_id)
    task_root = run_root / args.task_id
    prompt = (args.prompt or manifest.get("taskDescription") or
              manifest.get("effectiveEditPrompt") or "").strip()
    if not prompt:
        raise ValueError("没有评测 prompt；请在 task_manifest.json 中提供 taskDescription 或传入 --prompt")

    selected = select_six_views(run_root, args.task_id, pitches=args.pitches, views=args.views)
    image_paths = [str(item["path"]) for item in selected]
    edited_image = (args.edited_image or (task_root / "edited" / "center.png")).expanduser().resolve()
    if not edited_image.is_file():
        raise FileNotFoundError(f"缺少 image-edit center 图: {edited_image}")
    center = _choose_center(selected, manifest)
    center_render = Path(str(center["path"])).expanduser().resolve()
    if not center_render.is_file():
        raise FileNotFoundError(f"缺少 center 3D 渲染图: {center_render}")

    if args.clip_checkpoint:
        os.environ["HPSV2_CLIP_CHECKPOINT"] = str(args.clip_checkpoint)
    if args.checkpoint:
        os.environ["HPSV2_CHECKPOINT"] = str(args.checkpoint)
    # Import after setting environment variables: the model is created lazily.
    from hpsv2.img_score import clip_image_similarity, score

    clip_i = clip_image_similarity([str(edited_image), str(center_render)])
    scores = score(image_paths, prompt, str(args.checkpoint) if args.checkpoint else None, args.hps_version)
    if len(scores) != len(selected):
        raise ValueError(f"HPSv2 返回 {len(scores)} 个分数，预期 {len(selected)} 个视角")
    records = [{**item, "score": float(value)} for item, value in zip(selected, scores)]
    aggregates = aggregate_scores(records)

    output = args.output or (task_root / "metrics" / "hpsv2.json")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    hps_summary = output.parent / "hpsv2_summary.png"
    clip_summary = output.parent / "clip_i_summary.png"
    write_hps_summary(records, hps_summary)
    write_clip_summary(edited_image, center_render, clip_i, clip_summary)
    result = {
        "schemaVersion": 2,
        "metric": "hpsv2",
        "evaluationType": "unsupervised",
        "runRoot": str(run_root),
        "taskId": args.task_id,
        "hpsVersion": args.hps_version,
        "prompt": prompt,
        "selection": "center view_000, left/right adjacent circular views at low/high pitch",
        "views": records,
        "mean": aggregates["mean"],
        "meanByPitch": aggregates["meanByPitch"],
        "meanByView": aggregates["meanByView"],
        "dimensions": aggregates["dimensions"],
        "clipI": {
            "score": clip_i,
            "reference": _relative_task_path(edited_image, run_root, args.task_id),
            "render": _relative_task_path(center_render, run_root, args.task_id),
            "renderPitch": float(center["pitch"]),
            "renderViewIndex": int(center["viewIndex"]),
            "selection": "benchmark center nearest to task manifest pitch",
        },
        "artifacts": {
            "hpsv2Summary": hps_summary.name,
            "clipISummary": clip_summary.name,
        },
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "ready",
        "output": str(output),
        "mean": result["mean"],
        "clipI": clip_i,
        "hpsv2Summary": str(hps_summary),
        "clipISummary": str(clip_summary),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(f"HPSV2_METRIC_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
