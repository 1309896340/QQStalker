"""Render an HTML document into one or more fixed-DPI PNG images."""

from __future__ import annotations

import argparse
import math
import os
import struct
import tempfile
import zlib
from datetime import datetime
from html import escape
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Browser, Page, sync_playwright

CSS_PIXELS_PER_INCH = 96.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_DPI = 300
DEFAULT_WIDTH_MILLIMETERS = 210.0
DEFAULT_MAX_HEIGHT_PIXELS = 30_000
PNG_DPI_ENV = "PNG_DPI"
PNG_MAX_HEIGHT_PIXELS_ENV = "PNG_MAX_HEIGHT_PIXELS"


def positive_integer(value: str) -> int:
    """Parse a strictly positive command-line integer."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("必须是正整数") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def positive_float(value: str) -> float:
    """Parse a strictly positive command-line floating point number."""

    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("必须是正数") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正数")
    return parsed


def css_pixels_for_millimeters(width_millimeters: float) -> int:
    """Convert a physical width to browser CSS pixels."""

    return max(1, round(width_millimeters / 25.4 * CSS_PIXELS_PER_INCH))


def create_output_path(
    output_dir: Path,
    *,
    generated_at: datetime | None = None,
    chat_name: str | None = None,
) -> Path:
    """Create the output directory and return its timestamped PNG path."""

    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("输出路径必须是文件夹")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (generated_at or datetime.now()).strftime("%Y%m%d%H%M%S")
    return output_dir / f"{timestamp}_{chat_name or '群员画像'}.png"


def output_paths(output_path: Path, segment_count: int) -> list[Path]:
    """Create a single output path or numbered paths for a split render."""

    if segment_count == 1:
        return [output_path]
    return [
        output_path.with_name(f"{output_path.stem}_{index}{output_path.suffix}")
        for index in range(1, segment_count + 1)
    ]


def final_output_paths(
    output_path: Path,
    segment_count: int,
    *,
    stitch_horizontal: bool,
    stitch_count: int,
) -> list[Path]:
    """Return final paths, preserving a single unstitched segment as-is."""

    if stitch_horizontal and segment_count > 1:
        return output_paths(output_path, math.ceil(segment_count / stitch_count))
    return output_paths(output_path, segment_count)


def stitch_pngs_horizontally(
    browser: Browser,
    source_paths: list[Path],
    output_paths_to_write: list[Path],
    *,
    images_per_output: int,
    dpi: int,
) -> None:
    """Stitch consecutive PNG segments horizontally without resampling them."""

    stitch_page = browser.new_page(viewport={"width": 1, "height": 1})
    try:
        for output_index, output_path in enumerate(output_paths_to_write):
            start = output_index * images_per_output
            segment_paths = source_paths[start : start + images_per_output]
            stitch_html_path = segment_paths[0].parent / f".stitch_{output_index}.html"
            images = "".join(
                f'<img src="{escape(path.resolve().as_uri(), quote=True)}">'
                for path in segment_paths
            )
            stitch_html_path.write_text(
                "<!doctype html><style>"
                "html, body { margin: 0; padding: 0; background: white; }"
                ".segments { display: flex; align-items: flex-start; width: max-content; }"
                ".segments img { display: block; flex: none; }"
                f"</style><body><div class=\"segments\">{images}</div>",
                encoding="utf-8",
            )
            stitch_page.goto(stitch_html_path.resolve().as_uri(), wait_until="load")
            wait_for_document_resources(stitch_page)
            images_loaded = stitch_page.locator(".segments img").evaluate_all(
                "images => images.every(image => image.naturalWidth > 0 && image.naturalHeight > 0)"
            )
            if not images_loaded:
                raise RuntimeError("无法加载待横向拼接的 PNG 分片")
            width, height = document_dimensions(stitch_page)
            stitch_page.set_viewport_size({"width": width, "height": height})
            stitch_page.screenshot(path=str(output_path), scale="device")
            set_png_dpi(output_path, dpi)
    finally:
        stitch_page.close()


def make_png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Encode one PNG chunk including its CRC."""

    checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", checksum)


def set_png_dpi(image_path: Path, dpi: int) -> None:
    """Insert or replace the PNG pHYs chunk without decoding raster data."""

    png_data = image_path.read_bytes()
    if not png_data.startswith(PNG_SIGNATURE):
        raise RuntimeError(f"Chromium 未生成有效 PNG：{image_path}")

    pixels_per_meter = round(dpi / 0.0254)
    physical_size = struct.pack(">IIB", pixels_per_meter, pixels_per_meter, 1)
    rewritten = bytearray(PNG_SIGNATURE)
    offset = len(PNG_SIGNATURE)
    inserted = False

    while offset < len(png_data):
        if offset + 12 > len(png_data):
            raise RuntimeError(f"PNG 文件结构不完整：{image_path}")
        chunk_size = struct.unpack(">I", png_data[offset : offset + 4])[0]
        chunk_end = offset + 12 + chunk_size
        if chunk_end > len(png_data):
            raise RuntimeError(f"PNG 文件结构不完整：{image_path}")
        chunk_type = png_data[offset + 4 : offset + 8]
        chunk = png_data[offset:chunk_end]
        offset = chunk_end

        if chunk_type == b"pHYs":
            continue
        rewritten.extend(chunk)
        if chunk_type == b"IHDR":
            rewritten.extend(make_png_chunk(b"pHYs", physical_size))
            inserted = True

    if not inserted:
        raise RuntimeError(f"PNG 文件缺少 IHDR 块：{image_path}")
    image_path.write_bytes(rewritten)


def wait_for_document_resources(page: Page) -> None:
    """Wait for fonts and local images so the screenshot is complete."""

    page.evaluate(
        """async () => {
            if (document.fonts) {
                await document.fonts.ready;
            }
            await Promise.all(
                Array.from(document.images).map((image) => {
                    if (image.complete) return Promise.resolve();
                    return new Promise((resolve) => {
                        image.addEventListener('load', resolve, { once: true });
                        image.addEventListener('error', resolve, { once: true });
                    });
                }),
            );
        }"""
    )


def document_dimensions(page: Page) -> tuple[int, int]:
    """Return the document's scrollable CSS width and height."""

    width, height = page.evaluate(
        """() => {
            const root = document.documentElement;
            const body = document.body;
            return [
                Math.max(root.scrollWidth, body ? body.scrollWidth : 0),
                Math.max(root.scrollHeight, body ? body.scrollHeight : 0),
            ];
        }"""
    )
    return int(width), int(height)


def render_html_to_pngs(
    input_html: Path,
    output_path: Path,
    *,
    dpi: int,
    width_millimeters: float,
    max_height_pixels: int,
    stitch_horizontal: bool = False,
    stitch_count: int = 4,
) -> list[Path]:
    """Render fixed-width HTML in vertical PNG segments at the requested DPI."""

    if not input_html.is_file():
        raise FileNotFoundError(f"HTML 输入文件不存在：{input_html}")

    css_width = css_pixels_for_millimeters(width_millimeters)
    device_scale_factor = dpi / CSS_PIXELS_PER_INCH
    max_segment_css_height = max(1, math.floor(max_height_pixels / device_scale_factor))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            raise RuntimeError(
                "无法启动 Chromium 渲染器；请执行 `uv run playwright install chromium` 安装浏览器"
            ) from error
        try:
            page = browser.new_page(
                viewport={"width": css_width, "height": min(1_080, max_segment_css_height)},
                device_scale_factor=device_scale_factor,
            )
            page.goto(input_html.resolve().as_uri(), wait_until="load")
            wait_for_document_resources(page)
            document_width, document_height = document_dimensions(page)
            if document_width > css_width:
                raise RuntimeError(
                    "HTML 内容宽度超过指定渲染宽度；"
                    f"内容为 {document_width} CSS px，指定为 {css_width} CSS px"
                )

            segment_count = math.ceil(document_height / max_segment_css_height)
            should_stitch = stitch_horizontal and segment_count > 1
            final_paths = final_output_paths(
                output_path,
                segment_count,
                stitch_horizontal=stitch_horizontal,
                stitch_count=stitch_count,
            )
            temporary_directory: tempfile.TemporaryDirectory[str] | None = None
            if should_stitch:
                temporary_directory = tempfile.TemporaryDirectory(
                    prefix=".render_html_png_",
                    dir=output_path.parent,
                )
                paths = [
                    Path(temporary_directory.name) / f"segment_{index}.png"
                    for index in range(1, segment_count + 1)
                ]
            else:
                paths = final_paths
            print(
                f"正在渲染 {document_height} CSS px 高的 HTML："
                f"{segment_count} 张图片，{dpi} DPI。",
                flush=True,
            )
            try:
                for index, (segment_top, path) in enumerate(
                    zip(range(0, document_height, max_segment_css_height), paths, strict=True),
                    1,
                ):
                    segment_height = min(max_segment_css_height, document_height - segment_top)
                    page.set_viewport_size({"width": css_width, "height": segment_height})
                    page.evaluate("segmentTop => window.scrollTo(0, segmentTop)", segment_top)
                    page.screenshot(
                        path=str(path),
                        scale="device",
                    )
                    set_png_dpi(path, dpi)
                    if should_stitch:
                        print(f"已生成临时分片 {index}/{segment_count}", flush=True)
                    else:
                        print(f"已写入图片 {index}/{segment_count}：{path.resolve()}", flush=True)

                if should_stitch:
                    stitch_pngs_horizontally(
                        browser,
                        paths,
                        final_paths,
                        images_per_output=stitch_count,
                        dpi=dpi,
                    )
                    for index, path in enumerate(final_paths, 1):
                        print(
                            f"已横向拼接图片 {index}/{len(final_paths)}：{path.resolve()}",
                            flush=True,
                        )
            finally:
                if temporary_directory is not None:
                    temporary_directory.cleanup()
        finally:
            browser.close()
    return final_paths


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_html", type=Path, help="待渲染的 HTML 文件")
    parser.add_argument("output_dir", type=Path, help="PNG 输出文件夹；不存在时自动创建")
    parser.add_argument(
        "--dpi",
        type=positive_integer,
        default=os.getenv(PNG_DPI_ENV, str(DEFAULT_DPI)),
        help=f"目标 PNG DPI（默认：环境变量 {PNG_DPI_ENV} 或 {DEFAULT_DPI}）",
    )
    parser.add_argument(
        "--width-mm",
        type=positive_float,
        default=DEFAULT_WIDTH_MILLIMETERS,
        help=f"渲染宽度，单位毫米（默认：{DEFAULT_WIDTH_MILLIMETERS:g}）",
    )
    parser.add_argument(
        "--max-height-px",
        type=positive_integer,
        default=os.getenv(PNG_MAX_HEIGHT_PIXELS_ENV, str(DEFAULT_MAX_HEIGHT_PIXELS)),
        help=(
            "单张图片最大物理像素高度；超出时自动输出 _1、_2 等分片"
            f"（默认：环境变量 {PNG_MAX_HEIGHT_PIXELS_ENV} 或 {DEFAULT_MAX_HEIGHT_PIXELS}）"
        ),
    )
    parser.add_argument(
        "--stitch-horizontal",
        action="store_true",
        help="将连续的垂直分片横向拼接为最终 PNG",
    )
    parser.add_argument(
        "--stitch-count",
        type=positive_integer,
        default=4,
        help="每张拼接图片包含的分片数量（仅 --stitch-horizontal 生效，默认：4）",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    try:
        output_path = create_output_path(args.output_dir)
        paths = render_html_to_pngs(
            args.input_html,
            output_path,
            dpi=args.dpi,
            width_millimeters=args.width_mm,
            max_height_pixels=args.max_height_px,
            stitch_horizontal=args.stitch_horizontal,
            stitch_count=args.stitch_count,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error

    if len(paths) == 1:
        print(f"PNG 已写入：{paths[0].resolve()}")
    else:
        print(f"PNG 已分片写入：共 {len(paths)} 张，基础名为 {output_path.stem}")


if __name__ == "__main__":
    main()
