import os
import sys
from pathlib import Path
from typing import Optional, Tuple, Union, List

from PIL import Image, ImageDraw, ImageFont, ImageColor


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def prompt_user_inputs() -> Tuple[Path, bool, int, str, str]:
    """Prompt user for required inputs via command line.

    Returns:
        path: 用户输入的路径（文件或目录）
        is_dir: 是否为目录
        font_size: 字体大小
        color: 颜色（中文/英文/十六进制）
        position_key_cn: 位置中文键
    """
    while True:
        input_path_str = input("请输入一个图片文件路径或一个图片文件夹路径: ").strip().strip('"')
        input_path = Path(input_path_str)
        if input_path.exists() and (input_path.is_file() or input_path.is_dir()):
            break
        print("路径无效，请重新输入存在的图片文件或目录路径。")

    # font size
    while True:
        font_size_str = input("请输入字体大小（例如 36）: ").strip()
        try:
            font_size_val = int(font_size_str)
            if font_size_val > 0:
                break
        except ValueError:
            pass
        print("无效的字体大小，请输入正整数。")

    # color
    while True:
        color_str = input("请输入颜色（支持中文/英文/十六进制，例如 白色、white、#FFFFFF）: ").strip()
        try:
            # Validate color
            # 先尝试中文/英文颜色名映射
            _ = ImageColor.getrgb(normalize_color_input(color_str))
            break
        except ValueError:
            print("无效的颜色，请输入常见颜色名（中文或英文）或十六进制色值，例如 #FFFFFF。")

    # position
    valid_positions_cn = "、".join(["左上角", "左下角", "右上角", "右下角", "中间", "上边缘", "下边缘"])
    while True:
        pos_str_cn = input(f"请输入位置（{valid_positions_cn}）: ").strip()
        if pos_str_cn in POSITION_CN_TO_KEY:
            break
        print(f"无效的位置，请从 {valid_positions_cn} 中选择。")

    return input_path, input_path.is_dir(), font_size_val, color_str, pos_str_cn


def ensure_output_dir(original_dir: Path) -> Path:
    """Create output directory named '<dirname>_watermark' under original_dir."""
    dir_name = original_dir.name
    out_dir = original_dir / f"{dir_name}_watermark"
    out_dir.mkdir(exist_ok=True)
    return out_dir


def list_images_in_dir(directory: Path):
    for entry in directory.iterdir():
        if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield entry


def try_load_truetype_font(font_size: int) -> Union[ImageFont.FreeTypeFont, ImageFont.ImageFont]:
    """Try to load a TTF font; fallback to default bitmap font if unavailable."""
    # Common Windows font
    candidate_paths = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
    ]
    for font_path in candidate_paths:
        try:
            if font_path.exists():
                return ImageFont.truetype(str(font_path), font_size)
        except Exception:
            continue
    try:
        return ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        return ImageFont.load_default()


def extract_exif_date(image: Image.Image) -> Optional[str]:
    """Extract date string (YYYY-MM-DD) from EXIF DateTimeOriginal or DateTime."""
    # Prefer Pillow >=7's getexif()
    exif = None
    try:
        exif = image.getexif()
    except Exception:
        pass

    date_value = None
    if exif:
        # 36867: DateTimeOriginal, 306: DateTime
        for tag in (36867, 306):
            value = exif.get(tag)
            if value:
                date_value = str(value)
                break

    if not date_value:
        # Fallback to legacy _getexif
        try:
            raw = image._getexif()  # type: ignore[attr-defined]
            if raw and isinstance(raw, dict):
                for tag in (36867, 306):
                    if tag in raw and raw[tag]:
                        date_value = str(raw[tag])
                        break
        except Exception:
            pass

    if not date_value:
        return None

    # Common EXIF datetime format: "YYYY:MM:DD HH:MM:SS"
    try:
        date_part = date_value.split(" ")[0]
        yyyy, mm, dd = date_part.split(":")
        return f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
    except Exception:
        # Try alternative format YYYY-MM-DD HH:MM:SS
        try:
            date_part = date_value.split(" ")[0]
            yyyy, mm, dd = date_part.split("-")
            return f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
        except Exception:
            return None


def compute_position(
    image_size: Tuple[int, int],
    text_size: Tuple[int, int],
    position_key: str,
    margin: int = 12,
) -> Tuple[int, int]:
    img_w, img_h = image_size
    text_w, text_h = text_size

    if position_key == "left_top":
        return margin, margin
    if position_key == "left_bottom":
        return margin, max(img_h - text_h - margin, 0)
    if position_key == "right_top":
        return max(img_w - text_w - margin, 0), margin
    if position_key == "right_bottom":
        return max(img_w - text_w - margin, 0), max(img_h - text_h - margin, 0)
    if position_key == "center":
        return (img_w - text_w) // 2, (img_h - text_h) // 2
    if position_key == "top_center":
        return (img_w - text_w) // 2, margin
    if position_key == "bottom_center":
        return (img_w - text_w) // 2, max(img_h - text_h - margin, 0)
    # default fallback
    return margin, margin


def draw_text_watermark(
    image: Image.Image,
    text: str,
    font: ImageFont.ImageFont,
    fill_color: str,
    position_key: str,
) -> Image.Image:
    # Convert to RGBA to support semi-transparent stroke if needed
    if image.mode != "RGBA":
        base = image.convert("RGBA")
    else:
        base = image.copy()

    draw = ImageDraw.Draw(base)

    def _measure_text() -> Tuple[int, int]:
        # Prefer modern APIs when available
        if hasattr(draw, "textbbox"):
            try:
                left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
                return right - left, bottom - top
            except Exception:
                pass
        # Pillow >=8 font.getbbox
        if hasattr(font, "getbbox"):
            try:
                left, top, right, bottom = font.getbbox(text)
                return right - left, bottom - top
            except Exception:
                pass
        # Legacy fallbacks
        if hasattr(font, "getsize"):
            try:
                return font.getsize(text)  # type: ignore[return-value]
            except Exception:
                pass
        # Last resort
        return (100, 30)

    text_w, text_h = _measure_text()
    x, y = compute_position(base.size, (text_w, text_h), position_key)

    # Optional stroke for readability
    try:
        draw.text((x, y), text, font=font, fill=fill_color, stroke_width=2, stroke_fill="black")
    except TypeError:
        # Older Pillow without stroke
        draw.text((x, y), text, font=font, fill=fill_color)

    # Return with original mode if necessary
    if image.mode != "RGBA":
        return base.convert(image.mode)
    return base


def process_targets(
    base_dir: Path,
    targets: List[Path],
    font_size: int,
    color: str,
    position_key: str,
) -> None:
    output_dir = ensure_output_dir(base_dir)
    font = try_load_truetype_font(font_size)

    if not targets:
        print("未发现可处理的图片（jpg/jpeg/png）。")
        return

    processed = 0
    skipped = 0
    skipped_reasons: dict[str, int] = {}
    for img_path in targets:
        try:
            with Image.open(img_path) as img:
                date_text = extract_exif_date(img)
                if not date_text:
                    reason = "无拍摄时间"
                    print(f"跳过（{reason}）：{img_path.name}")
                    skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
                    skipped += 1
                    continue

                watermarked = draw_text_watermark(img, date_text, font, color, position_key)

                out_path = output_dir / img_path.name
                # Preserve format
                save_kwargs = {}
                if img_path.suffix.lower() in {".jpg", ".jpeg"}:
                    save_kwargs["quality"] = 95

                watermarked.save(out_path, **save_kwargs)
                processed += 1
                print(f"已保存：{out_path}")
        except Exception as exc:
            print(f"处理失败 {img_path.name}（{exc.__class__.__name__}）：{exc}")

    if skipped_reasons:
        reasons_str = "；".join([f"{k} {v} 张" for k, v in skipped_reasons.items()])
        print(f"完成。处理成功 {processed} 张，跳过 {skipped} 张（原因：{reasons_str}）。输出目录：{output_dir}")
    else:
        print(f"完成。处理成功 {processed} 张，跳过 {skipped} 张。输出目录：{output_dir}")


def normalize_color_input(user_input: str) -> str:
    """Map Chinese color names to English/hex; pass through known values."""
    s = user_input.strip().lower()
    cn_to_en = {
        "白": "white", "白色": "white",
        "黑": "black", "黑色": "black",
        "红": "red", "红色": "red",
        "绿": "green", "绿色": "green",
        "蓝": "blue", "蓝色": "blue",
        "黄": "yellow", "黄色": "yellow",
        "青": "cyan", "青色": "cyan",
        "洋红": "magenta", "品红": "magenta",
        "灰": "gray", "灰色": "gray",
        "橙": "orange", "橙色": "orange",
        "紫": "purple", "紫色": "purple",
        "粉": "pink", "粉色": "pink",
        "棕": "brown", "棕色": "brown", "褐色": "brown",
    }
    if s in cn_to_en:
        return cn_to_en[s]
    # English color names or hex remain as-is
    return user_input


# Chinese position to internal key
POSITION_CN_TO_KEY = {
    "左上角": "left_top",
    "左下角": "left_bottom",
    "右上角": "right_top",
    "右下角": "right_bottom",
    "中间": "center",
    "上边缘": "top_center",
    "下边缘": "bottom_center",
}


def main() -> None:
    path, is_dir, font_size, color_input, position_cn = prompt_user_inputs()
    position_key = POSITION_CN_TO_KEY.get(position_cn, "center")
    color = normalize_color_input(color_input)

    if is_dir:
        base_dir = path
        targets = list(list_images_in_dir(base_dir))
    else:
        base_dir = path.parent
        targets = [path] if path.suffix.lower() in SUPPORTED_EXTENSIONS else []

    if not targets and not is_dir:
        print("输入的文件扩展名不受支持（仅支持 jpg/jpeg/png）。")
        return

    process_targets(base_dir, targets, font_size, color, position_key)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(1)


