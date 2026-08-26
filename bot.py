"""Telegram bot that renders clearly labeled fictional iPhone SMS mockups.

This is a demo/mockup generator. The rendered image is deliberately marked
"DEMO - NOT A TICKET" and must not be used as proof of payment or travel.

Install:
    pip install -r requirements.txt
Run:
    Add TELEGRAM_BOT_TOKEN=your-token to .env
    python bot.py
"""

from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

LOGGER = logging.getLogger(__name__)

SHORT_NUMBER, TICKET_NUMBER, DATE_TIME, DIVIDER_DATE = range(4)
START_CALLBACK = "start_demo"
CANCEL_CALLBACK = "cancel_demo"

WIDTH = 1170
HEIGHT = 2532
IOS_BACKGROUND = (246, 246, 248)
TEXT = (24, 24, 28)
SECONDARY_TEXT = (112, 112, 118)
INCOMING_BUBBLE = (229, 229, 234)
OUTGOING_BUBBLE = (52, 199, 89)
LINK_BLUE = (0, 95, 220)
WATERMARK = (210, 34, 34, 180)


@dataclass(frozen=True)
class TicketData:
    short_number: str
    ticket_number: str
    date_time: str
    divider_date: str


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Find a broadly available font, with a PIL fallback."""
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def rounded_bubble(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: tuple[int, int, int], radius: int = 34) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def draw_status_bar(draw: ImageDraw.ImageDraw) -> None:
    small = font(30, bold=True)
    draw.text((54, 30), "21:59", font=small, fill=TEXT)

    # Signal bars.
    for index, height in enumerate((12, 19, 26, 33)):
        x = 910 + index * 12
        draw.rounded_rectangle((x, 60 - height, x + 8, 60), radius=3, fill=TEXT)
    draw.text((970, 26), "5G", font=font(25, bold=True), fill=TEXT)

    # 73% battery indicator.
    draw.rounded_rectangle((1030, 32, 1111, 66), radius=8, outline=TEXT, width=3)
    draw.rounded_rectangle((1036, 38, 1094, 60), radius=4, fill=TEXT)
    draw.rectangle((1114, 43, 1119, 55), fill=TEXT)


def draw_avatar(draw: ImageDraw.ImageDraw, center: tuple[int, int]) -> None:
    x, y = center
    draw.ellipse((x - 39, y - 39, x + 39, y + 39), fill=(211, 211, 216))
    draw.ellipse((x - 14, y - 23, x + 14, y + 5), fill=(145, 145, 151))
    draw.ellipse((x - 27, y + 4, x + 27, y + 42), fill=(145, 145, 151))


def draw_header(draw: ImageDraw.ImageDraw) -> None:
    draw.line((0, 160, WIDTH, 160), fill=(218, 218, 222), width=2)
    draw.text((42, 91), "‹", font=font(70), fill=LINK_BLUE)
    draw.ellipse((80, 85, 126, 131), fill=(255, 59, 48))
    draw.text((91, 88), "39", font=font(22, bold=True), fill="white")
    draw_avatar(draw, (585, 91))
    draw.text((585, 133), "7000", anchor="ms", font=font(27, bold=True), fill=TEXT)
    draw.text((1080, 94), "i", anchor="mm", font=font(31, bold=True), fill=LINK_BLUE)


def centered(draw: ImageDraw.ImageDraw, text: str, y: int, fill: tuple[int, int, int], size: int = 27) -> None:
    draw.text((WIDTH // 2, y), text, anchor="ma", font=font(size), fill=fill)


def draw_outgoing(draw: ImageDraw.ImageDraw, text: str, y: int) -> int:
    bubble_font = font(34)
    left, right = 870, 1117
    top, bottom = y, y + 78
    rounded_bubble(draw, (left, top, right, bottom), OUTGOING_BUBBLE, 38)
    draw.text((right - 28, top + 39), text, anchor="rm", font=bubble_font, fill="white")
    return bottom


def draw_incoming(draw: ImageDraw.ImageDraw, lines: list[str], y: int, link_line: int | None = None) -> int:
    bubble_font = font(32)
    line_height = 46
    padding_x, padding_y = 28, 24
    height = padding_y * 2 + line_height * len(lines) - 8
    right = 1030
    bottom = y + height
    rounded_bubble(draw, (52, y, right, bottom), INCOMING_BUBBLE, 34)
    for index, line in enumerate(lines):
        line_y = y + padding_y + index * line_height
        fill = LINK_BLUE if index == link_line else TEXT
        draw.text((52 + padding_x, line_y), line, font=bubble_font, fill=fill)
        if index == link_line:
            width = draw.textlength(line, font=bubble_font)
            draw.line((52 + padding_x, line_y + 37, 52 + padding_x + width, line_y + 37), fill=LINK_BLUE, width=2)
    return bottom


def draw_footer(draw: ImageDraw.ImageDraw) -> None:
    top = HEIGHT - 154
    draw.line((0, top, WIDTH, top), fill=(218, 218, 222), width=2)
    draw.ellipse((32, top + 44, 88, top + 100), outline=SECONDARY_TEXT, width=4)
    draw.line((60, top + 56, 60, top + 88), fill=SECONDARY_TEXT, width=4)
    draw.line((44, top + 72, 76, top + 72), fill=SECONDARY_TEXT, width=4)
    rounded_bubble(draw, (112, top + 29, 1006, top + 116), (255, 255, 255), 44)
    draw.text((145, top + 72), "Текстовое сообщение", anchor="lm", font=font(29), fill=(145, 145, 150))
    draw.ellipse((941, top + 52, 960, top + 84), fill=SECONDARY_TEXT)
    draw.arc((927, top + 37, 974, top + 92), 290, 70, fill=SECONDARY_TEXT, width=4)
    draw.arc((1028, top + 40, 1080, top + 101), 210, 330, fill=SECONDARY_TEXT, width=4)
    draw.ellipse((1047, top + 65, 1061, top + 79), fill=SECONDARY_TEXT)


def render_ticket(data: TicketData) -> io.BytesIO:
    image = Image.new("RGB", (WIDTH, HEIGHT), IOS_BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw_status_bar(draw)
    draw_header(draw)

    y = 212
    centered(draw, data.divider_date, y, SECONDARY_TEXT)
    y += 52
    y = draw_outgoing(draw, data.short_number, y) + 24
    y = draw_incoming(draw, ["Vash zapros obrabatyvaetsea."], y) + 24
    y = draw_incoming(
        draw,
        [
            f"Electronnyi bilet no. {data.ticket_number}",
            f"Cislo {data.date_time}",
            "Deystvitelen 1 chas",
            "Tsena 7 MDL",
            f"Bortovoi nomer {data.short_number}",
        ],
        y,
        link_line=0,
    )

    # Persistent labeling prevents the mockup from being mistaken for evidence.
    watermark = Image.new("RGBA", image.size, (0, 0, 0, 0))
    watermark_draw = ImageDraw.Draw(watermark)
    label_font = font(62, bold=True)
    watermark_draw.text((WIDTH // 2, HEIGHT // 2), "DEMO - NOT A TICKET", anchor="mm", font=label_font, fill=WATERMARK, stroke_width=2, stroke_fill=(255, 255, 255, 150))
    image = Image.alpha_composite(image.convert("RGBA"), watermark).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw_footer(draw)

    output = io.BytesIO()
    output.name = "sms_demo.png"
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Create demo mockup", callback_data=START_CALLBACK)]]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancel", callback_data=CANCEL_CALLBACK)]]
    )


def valid(value: str, pattern: str) -> bool:
    return bool(re.fullmatch(pattern, value.strip()))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    message = update.effective_message
    await message.reply_text(
        "This bot creates a clearly labeled fictional SMS demo image. It is not a real ticket or proof of payment.\n\nPress the button to begin.",
        reply_markup=keyboard(),
    )
    return ConversationHandler.END


async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text(
        "Enter the short number (3-6 digits):", reply_markup=cancel_keyboard()
    )
    return SHORT_NUMBER


async def collect_short_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.effective_message.text.strip()
    if not valid(value, r"\d{3,6}"):
        await update.effective_message.reply_text("Use digits only, 3 to 6 characters.")
        return SHORT_NUMBER
    context.user_data["short_number"] = value
    await update.effective_message.reply_text("Enter the 8-digit ticket number:", reply_markup=cancel_keyboard())
    return TICKET_NUMBER


async def collect_ticket_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.effective_message.text.strip()
    if not valid(value, r"\d{8}"):
        await update.effective_message.reply_text("Use exactly 8 digits.")
        return TICKET_NUMBER
    context.user_data["ticket_number"] = value
    await update.effective_message.reply_text("Enter the date and time, for example: 21.08.2026 vremea 19:56", reply_markup=cancel_keyboard())
    return DATE_TIME


async def collect_date_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.effective_message.text.strip()
    if not 3 <= len(value) <= 80:
        await update.effective_message.reply_text("Enter a date and time between 3 and 80 characters.")
        return DATE_TIME
    context.user_data["date_time"] = value
    await update.effective_message.reply_text("Enter the divider date, for example: пятница 19:56", reply_markup=cancel_keyboard())
    return DIVIDER_DATE


async def collect_divider_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.effective_message.text.strip()
    if not 3 <= len(value) <= 50:
        await update.effective_message.reply_text("Enter a divider date between 3 and 50 characters.")
        return DIVIDER_DATE
    context.user_data["divider_date"] = value
    data = TicketData(**context.user_data)
    photo = render_ticket(data)
    await update.effective_message.reply_photo(
        photo=photo,
        caption="Fictional demo mockup. DEMO - NOT A TICKET.",
        parse_mode=ParseMode.HTML,
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Cancelled. Use /start to create another demo.")
    else:
        await update.effective_message.reply_text("Cancelled. Use /start to create another demo.")
    return ConversationHandler.END


def build_application(token: str) -> Application:
    conversation = ConversationHandler(
        entry_points=[CallbackQueryHandler(begin, pattern=f"^{START_CALLBACK}$")],
        states={
            SHORT_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_short_number)],
            TICKET_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_ticket_number)],
            DATE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_date_time)],
            DIVIDER_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_divider_date)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern=f"^{CANCEL_CALLBACK}$"),
        ],
        allow_reentry=True,
    )
    application = Application.builder().token(token).build()
    application.add_handlers([CommandHandler("start", start), conversation])
    return application


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set the TELEGRAM_BOT_TOKEN environment variable first.")
    application = build_application(token)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
