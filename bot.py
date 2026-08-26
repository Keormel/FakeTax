from __future__ import annotations

import asyncio
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Iterable, Mapping, NamedTuple

from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)


BASE_SCREENSHOT_PATH = Path("base_screenshot.png")

# Pixel coordinates are tuned for a 591x1280 screenshot reference.
LAYERS: Mapping[str, dict[str, int]] = {
    "green_text_container": {"x": 478, "y": 793, "w": 78, "h": 38},
    "center_divider": {"x": 216, "y": 747, "w": 178, "h": 30},
    "response_body": {"x": 44, "y": 873, "w": 370, "h": 183},
}

SAMPLE_POINTS: Mapping[str, tuple[int, int]] = {
    "green_text_container": (555, 811),
    "center_divider": (205, 759),
    "response_body": (405, 1042),
}

COLORS = {
    "green_fallback": (52, 199, 89),
    "divider_text": (142, 142, 147),
    "body_text": (0, 0, 0),
    "ticket_link": (0, 122, 255),
}

RECEIPT_PREFIX = "Electronnyi bilet no. "
RECEIPT_STATIC_LINES = (
    "Deystvitelen 1 chas",
    "Tsena 7 MDL",
)

SHORT_ID, TICKET_ID, TIMESTAMP, DIVIDER = range(4)


class Box(NamedTuple):
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def xyxy(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.right - 1, self.bottom - 1)


def layer_box(name: str) -> Box:
    coords = LAYERS[name]
    return Box(coords["x"], coords["y"], coords["w"], coords["h"])


def load_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if token:
        return token

    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in {"TELEGRAM_BOT_TOKEN", "BOT_TOKEN"}:
                return value.strip().strip('"').strip("'")

    raise RuntimeError("Set TELEGRAM_BOT_TOKEN or BOT_TOKEN in the environment or .env file.")


def font_candidates() -> Iterable[str]:
    yield "arial.ttf"
    yield "Arial.ttf"
    yield r"C:\Windows\Fonts\arial.ttf"
    yield r"C:\Windows\Fonts\segoeui.ttf"
    yield "/System/Library/Fonts/Supplemental/Arial.ttf"
    yield "/System/Library/Fonts/SFNS.ttf"
    yield "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf"
    yield "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in font_candidates():
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def font_to_fit(
    draw: ImageDraw.ImageDraw,
    texts: Iterable[str],
    max_width: int,
    preferred_size: int,
    minimum_size: int,
) -> ImageFont.ImageFont:
    text_list = list(texts)
    for size in range(preferred_size, minimum_size - 1, -1):
        font = load_font(size)
        if all(text_size(draw, text, font)[0] <= max_width for text in text_list):
            return font
    return load_font(minimum_size)


def average_patch_color(image: Image.Image, point: tuple[int, int], radius: int = 3) -> tuple[int, int, int]:
    rgb = image.convert("RGB")
    x, y = point
    left = max(0, x - radius)
    top = max(0, y - radius)
    right = min(rgb.width, x + radius + 1)
    bottom = min(rgb.height, y + radius + 1)
    pixels = list(rgb.crop((left, top, right, bottom)).getdata())
    if not pixels:
        return (255, 255, 255)
    red = sum(pixel[0] for pixel in pixels) // len(pixels)
    green = sum(pixel[1] for pixel in pixels) // len(pixels)
    blue = sum(pixel[2] for pixel in pixels) // len(pixels)
    return (red, green, blue)


def clear_region(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    box: Box,
    sample_name: str,
    fallback: tuple[int, int, int] | None = None,
) -> tuple[int, int, int]:
    color = average_patch_color(image, SAMPLE_POINTS[sample_name]) if sample_name in SAMPLE_POINTS else fallback
    if color is None:
        color = (255, 255, 255)
    draw.rectangle(box.xyxy, fill=color)
    return color


def assert_layer_bounds(image: Image.Image) -> None:
    for name in LAYERS:
        box = layer_box(name)
        if box.right > image.width or box.bottom > image.height:
            raise ValueError(
                f"Layer '{name}' is outside the base screenshot bounds "
                f"({image.width}x{image.height})."
            )


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    box: Box,
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    text_width, text_height = text_size(draw, text, font)
    x = box.x + (box.w - text_width) / 2
    y = box.y + (box.h - text_height) / 2 - 1
    draw.text((x, y), text, font=font, fill=fill)


def draw_green_short_id(draw: ImageDraw.ImageDraw, image: Image.Image, short_id: str) -> None:
    box = layer_box("green_text_container")
    clear_region(draw, image, box, "green_text_container", COLORS["green_fallback"])
    font = font_to_fit(draw, [short_id], box.w - 18, preferred_size=27, minimum_size=18)
    draw_centered_text(draw, box, short_id, font, fill=(255, 255, 255))


def draw_divider(draw: ImageDraw.ImageDraw, image: Image.Image, divider: str) -> None:
    box = layer_box("center_divider")
    clear_region(draw, image, box, "center_divider")
    font = font_to_fit(draw, [divider], box.w, preferred_size=17, minimum_size=12)
    draw_centered_text(draw, box, divider, font, fill=COLORS["divider_text"])


def draw_underlined_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    x, y = xy
    draw.text((x, y), text, font=font, fill=fill)
    left, top, right, bottom = draw.textbbox((x, y), text, font=font)
    underline_y = bottom - 2
    draw.line((left, underline_y, right, underline_y), fill=fill, width=1)


def draw_response_body(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    short_id: str,
    ticket_id: str,
    timestamp: str,
) -> None:
    box = layer_box("response_body")
    clear_region(draw, image, box, "response_body")

    receipt_lines = [
        f"{RECEIPT_PREFIX}{ticket_id}",
        f"Cislo {timestamp}",
        *RECEIPT_STATIC_LINES,
        f"Bortovoi nomer {short_id}",
    ]
    font = font_to_fit(draw, receipt_lines, box.w - 8, preferred_size=26, minimum_size=18)
    line_height = max(text_size(draw, "Ag", font)[1] + 9, 29)

    x = box.x + 4
    y = box.y + 1

    draw.text((x, y), RECEIPT_PREFIX, font=font, fill=COLORS["body_text"])
    prefix_width, _ = text_size(draw, RECEIPT_PREFIX, font)
    draw_underlined_text(
        draw,
        (x + prefix_width, y),
        ticket_id,
        font=font,
        fill=COLORS["ticket_link"],
    )

    for line in receipt_lines[1:]:
        y += line_height
        draw.text((x, y), line, font=font, fill=COLORS["body_text"])


def render_receipt_image(short_id: str, ticket_id: str, timestamp: str, divider: str) -> BytesIO:
    if not BASE_SCREENSHOT_PATH.exists():
        raise FileNotFoundError(f"Missing required layout reference: {BASE_SCREENSHOT_PATH}")

    with Image.open(BASE_SCREENSHOT_PATH) as source:
        image = source.convert("RGB")

    assert_layer_bounds(image)
    draw = ImageDraw.Draw(image)

    draw_green_short_id(draw, image, short_id)
    draw_divider(draw, image, divider)
    draw_response_body(draw, image, short_id, ticket_id, timestamp)

    output = BytesIO()
    output.name = "receipt_layout.png"
    image.save(output, format="PNG")
    output.seek(0)
    return output


def clean_input(text: str | None) -> str:
    return (text or "").strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Enter str_short_id:")
    return SHORT_ID


async def collect_short_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = clean_input(update.message.text if update.message else None)
    if not value:
        await update.message.reply_text("Enter str_short_id:")
        return SHORT_ID
    context.user_data["str_short_id"] = value
    await update.message.reply_text("Enter str_ticket_id:")
    return TICKET_ID


async def collect_ticket_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = clean_input(update.message.text if update.message else None)
    if not value:
        await update.message.reply_text("Enter str_ticket_id:")
        return TICKET_ID
    context.user_data["str_ticket_id"] = value
    await update.message.reply_text("Enter str_timestamp:")
    return TIMESTAMP


async def collect_timestamp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = clean_input(update.message.text if update.message else None)
    if not value:
        await update.message.reply_text("Enter str_timestamp:")
        return TIMESTAMP
    context.user_data["str_timestamp"] = value
    await update.message.reply_text("Enter str_divider:")
    return DIVIDER


async def collect_divider_and_render(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = clean_input(update.message.text if update.message else None)
    if not value:
        await update.message.reply_text("Enter str_divider:")
        return DIVIDER

    context.user_data["str_divider"] = value

    try:
        photo_stream = render_receipt_image(
            short_id=context.user_data["str_short_id"],
            ticket_id=context.user_data["str_ticket_id"],
            timestamp=context.user_data["str_timestamp"],
            divider=context.user_data["str_divider"],
        )
    except Exception as exc:
        logging.exception("Failed to render receipt image")
        await update.message.reply_text(f"Image generation failed: {exc}")
        return ConversationHandler.END

    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo_stream)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def build_application(token: str) -> Application:
    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("render", start),
        ],
        states={
            SHORT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_short_id)],
            TICKET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_ticket_id)],
            TIMESTAMP: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_timestamp)],
            DIVIDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_divider_and_render)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application = ApplicationBuilder().token(token).build()
    application.add_handler(conversation)
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    application = build_application(load_token())
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
