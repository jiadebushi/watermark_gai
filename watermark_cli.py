import os
import sys
from pathlib import Path
from typing import Optional, Tuple, Union

from PIL import Image, ImageDraw, ImageFont, ImageColor


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def prompt_user_inputs() -> Tuple[Path, int, str, str]:
    """Prompt user for required inputs via command line."""
    while True:
        input_path_str = input("请输入一个图片文件路径: ").strip().strip('"')
        input_path = Path(input_path_str)
        if input_path.exists() and input_path.is_file():
            break
        print("路径无效，请重新输入一个存在的图片文件路径。")

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
        color_str = input("请输入颜色（例如 white、black、#FFFFFF）: ").strip()
        try:
            # Validate color
            ImageColor.getrgb(color_str)
            break
        except ValueError:
            print("无效的颜色，请输入常见颜色名或十六进制色值，例如 #FFFFFF。")

    # position
    positions = {"left_top", "center", "right_bottom"}
    while True:
        pos_str = input("请输入位置（left_top / center / right_bottom）: ").strip().lower()
        if pos_str in positions:
            break
        print("无效的位置，请从 left_top / center / right_bottom 中选择。")

    return input_path, font_size_val, color_str, pos_str


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
    if position_key == "center":
        return (img_w - text_w) // 2, (img_h - text_h) // 2
    if position_key == "right_bottom":
        return max(img_w - text_w - margin, 0), max(img_h - text_h - margin, 0)
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
    text_w, text_h = draw.textsize(text, font=font)
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


def process_directory(
    directory: Path,
    font_size: int,
    color: str,
    position_key: str,
) -> None:
    output_dir = ensure_output_dir(directory)
    font = try_load_truetype_font(font_size)

    images = list(list_images_in_dir(directory))
    if not images:
        print("该目录下未发现可处理的图片（jpg/jpeg/png）。")
        return

    processed = 0
    skipped = 0
    for img_path in images:
        try:
            with Image.open(img_path) as img:
                date_text = extract_exif_date(img)
                if not date_text:
                    print(f"跳过（无拍摄时间）：{img_path.name}")
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
            print(f"处理失败 {img_path.name}: {exc}")

    print(f"完成。处理成功 {processed} 张，跳过 {skipped} 张。输出目录：{output_dir}")


def main() -> None:
    input_path, font_size, color, position_key = prompt_user_inputs()
    directory = input_path.parent
    process_directory(directory, font_size, color, position_key)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(1)


