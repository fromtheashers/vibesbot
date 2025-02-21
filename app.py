import os
import re
import json
import logging
import asyncio
from datetime import datetime

import aiohttp
from quart import Quart, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ConversationHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# Set up logging with DEBUG level for more detail
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Quart(__name__)

# Environment variables using os.environ.get
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RENDER_URL = os.environ.get("RENDER_URL", "http://localhost:5000")
SHEET_ID = os.environ.get("SHEET_ID")

# Validate required environment variables
if not TOKEN:
    logger.error("TELEGRAM_TOKEN is not set. Bot cannot start.")
    raise ValueError("TELEGRAM_TOKEN environment variable is required.")
if not SHEET_ID:
    logger.error("SHEET_ID is not set. Bot cannot start.")
    raise ValueError("SHEET_ID environment variable is required.")

# Google Sheets Setup (public sheet, so no auth is added)
BASE_URL = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values"

# Conversation States (removed unused ASK_FIELD_TO_EDIT)
(ASK_PASSWORD, ASK_NAME, ASK_DATE, ASK_FOOD, ASK_PLACE, ASK_SPACIOUSNESS, ASK_CONVO,
 ASK_VIBE, CONFIRM, ASK_NAME_FOR_EDIT, ASK_DATE_FOR_EDIT, SHOW_CURRENT_DATA,
 ASK_NEW_VALUE, CONFIRM_EDIT) = range(14)

# Inline Keyboards
SCORE_BUTTONS = [[InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]]
VIBE_BUTTONS = [
    [InlineKeyboardButton("Good", callback_data="good"),
     InlineKeyboardButton("Bad", callback_data="bad")]
]
MAIN_MENU = [
    [InlineKeyboardButton("Input Vibe Data", callback_data="input")],
    [InlineKeyboardButton("Edit Vibe Data", callback_data="edit")],
    [InlineKeyboardButton("View Current Rankings", callback_data="rankings")]
]

WELCOME_TEXT = (
    "Welcome to GoodVibesBot! 🎉\n"
    "This bot helps you track and analyze vibes from different places.\n\n"
    "Please enter the password to proceed."
)

# Helper function to convert column number to Excel column letter (supports >26)
def col_to_letter(n):
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result

# Asynchronous helper functions for Google Sheets API
async def append_row(values):
    url = f"{BASE_URL}/Sheet1!A1:G1:append?valueInputOption=RAW"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"values": [values]}) as response:
            if response.status != 200:
                text = await response.text()
                logger.error(f"Failed to append row: {text}")
                raise Exception(f"Failed to append row: {text}")

async def get_all_values():
    url = f"{BASE_URL}/Sheet1!A:G"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                text = await response.text()
                logger.error(f"Failed to get data: {text}")
                raise Exception(f"Failed to get data: {text}")
            data = await response.json()
            return data.get("values", [])

async def update_cell(row, col, value):
    url = f"{BASE_URL}/Sheet1!{col_to_letter(col)}{row}"
    async with aiohttp.ClientSession() as session:
        async with session.put(url, json={"values": [[value]]}, params={"valueInputOption": "RAW"}) as response:
            if response.status != 200:
                text = await response.text()
                logger.error(f"Failed to update cell: {text}")
                raise Exception(f"Failed to update cell: {text}")

# Build the Telegram Application
application = Application.builder().token(TOKEN).build()

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message else "unknown"
    logger.debug("Start handler triggered for user %s", user_id)
    if update.message:
        await update.message.reply_text(WELCOME_TEXT)
        logger.info("Sent welcome text to user %s", user_id)
    return ASK_PASSWORD

async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message else "unknown"
    text = update.message.text if update.message else ""
    logger.debug("ask_password triggered for user %s with text: %s", user_id, text)
    if text == "vibes":
        if update.message:
            await update.message.reply_text(
                "Access granted! What would you like to do?",
                reply_markup=InlineKeyboardMarkup(MAIN_MENU)
            )
        logger.info("Main menu sent to user %s", user_id)
        return ConversationHandler.END
    else:
        if update.message:
            await update.message.reply_text("Incorrect password. Please try again.")
        logger.info("Incorrect password response sent to user %s", user_id)
        return ASK_PASSWORD

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        logger.error("button handler received update without callback_query")
        return ConversationHandler.END
    await query.answer()
    logger.debug("Button handler triggered for user %s with data: %s",
                 query.from_user.id, query.data)
    if query.data == "input":
        await query.edit_message_text("Please enter the name of the place:")
        return ASK_NAME
    elif query.data == "edit":
        await query.edit_message_text("Enter the name of the place to edit:")
        return ASK_NAME_FOR_EDIT
    elif query.data == "rankings":
        await show_rankings(update, context)
        return ConversationHandler.END
    else:
        await query.answer("Unknown option selected.", show_alert=True)
        return ConversationHandler.END

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        logger.error("ask_name expects a text message.")
        return ConversationHandler.END
    context.user_data["vibe_data"] = {}
    context.user_data["vibe_data"]["name"] = update.message.text
    await update.message.reply_text("Enter the date (DD/MM/YYYY):")
    return ASK_DATE

async def ask_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        logger.error("ask_date expects a text message.")
        return ConversationHandler.END
    date_text = update.message.text
    if not re.match(r"^\d{2}/\d{2}/\d{4}$", date_text) or not is_valid_date(date_text):
        await update.message.reply_text("Invalid format or date. Please use DD/MM/YYYY:")
        return ASK_DATE
    context.user_data["vibe_data"]["date"] = date_text
    await update.message.reply_text("Score for Food (1-5):", reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
    return ASK_FOOD

def is_valid_date(date_str):
    try:
        datetime.strptime(date_str, "%d/%m/%Y")
        return True
    except ValueError:
        return False

async def ask_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        logger.error("ask_food expects a callback query.")
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    try:
        context.user_data["vibe_data"]["food"] = int(query.data)
    except ValueError:
        await query.edit_message_text("Invalid input for food score. Please try again:")
        return ASK_FOOD
    await query.edit_message_text("Score for Place (1-5):", reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
    return ASK_PLACE

async def ask_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        logger.error("ask_place expects a callback query.")
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    try:
        context.user_data["vibe_data"]["place"] = int(query.data)
    except ValueError:
        await query.edit_message_text("Invalid input for place score. Please try again:")
        return ASK_PLACE
    await query.edit_message_text("Score for Spaciousness (1-5):", reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
    return ASK_SPACIOUSNESS

async def ask_spaciousness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        logger.error("ask_spaciousness expects a callback query.")
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    try:
        context.user_data["vibe_data"]["spaciousness"] = int(query.data)
    except ValueError:
        await query.edit_message_text("Invalid input for spaciousness score. Please try again:")
        return ASK_SPACIOUSNESS
    await query.edit_message_text("Score for Convo (1-5):", reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
    return ASK_CONVO

async def ask_convo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        logger.error("ask_convo expects a callback query.")
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    try:
        context.user_data["vibe_data"]["convo"] = int(query.data)
    except ValueError:
        await query.edit_message_text("Invalid input for convo score. Please try again:")
        return ASK_CONVO
    await query.edit_message_text("Is the vibe good or bad?", reply_markup=InlineKeyboardMarkup(VIBE_BUTTONS))
    return ASK_VIBE

async def ask_vibe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        logger.error("ask_vibe expects a callback query.")
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data["vibe_data"]["vibe"] = query.data
    data = context.user_data["vibe_data"]
    confirm_text = (
        f"Confirm your input:\n"
        f"Name: {data.get('name', '')}\n"
        f"Date: {data.get('date', '')}\n"
        f"Food: {data.get('food', '')}\n"
        f"Place: {data.get('place', '')}\n"
        f"Spaciousness: {data.get('spaciousness', '')}\n"
        f"Convo: {data.get('convo', '')}\n"
        f"Vibe: {data.get('vibe', '')}\n"
        "Reply 'yes' to save, 'no' to cancel:"
    )
    await query.edit_message_text(confirm_text)
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        logger.error("confirm expects a text message.")
        return ConversationHandler.END
    if update.message.text.lower() == "yes":
        data = context.user_data.get("vibe_data", {})
        await append_row([data.get("name"), data.get("date"), data.get("food"), data.get("place"),
                          data.get("spaciousness"), data.get("convo"), data.get("vibe")])
        await update.message.reply_text("Data saved!", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    else:
        await update.message.reply_text("Input canceled.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    context.user_data.clear()
    return ConversationHandler.END

async def ask_name_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        logger.error("ask_name_for_edit expects a text message.")
        return ConversationHandler.END
    context.user_data["edit_data"] = {"name": update.message.text}
    await update.message.reply_text("Enter the date (DD/MM/YYYY) to identify the entry:")
    return ASK_DATE_FOR_EDIT

async def ask_date_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        logger.error("ask_date_for_edit expects a text message.")
        return ConversationHandler.END
    date_text = update.message.text
    if not re.match(r"^\d{2}/\d{2}/\d{4}$", date_text) or not is_valid_date(date_text):
        await update.message.reply_text("Invalid format or date. Please use DD/MM/YYYY:")
        return ASK_DATE_FOR_EDIT
    context.user_data["edit_data"]["date"] = date_text
    row = await find_row(context.user_data["edit_data"]["name"], date_text)
    if row:
        context.user_data["edit_row"] = row
        formatted = format_row(row)
        await update.message.reply_text(
            f"Current data:\n{formatted}\nWhich field to edit? (Food, Place, Spaciousness, Convo, Vibe):"
        )
        return SHOW_CURRENT_DATA
    else:
        await update.message.reply_text("Entry not found.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
        return ConversationHandler.END

async def find_row(name, date):
    all_data = await get_all_values()
    # Skip header row and check that row data is complete
    for i, row in enumerate(all_data[1:], start=2):
        if len(row) >= 2 and row[0] == name and row[1] == date:
            return {"index": i, "data": row}
    return None

def format_row(row):
    try:
        data = row.get("data", [])
        if len(data) < 7:
            return "Data is incomplete."
        return (f"Name: {data[0]}\nDate: {data[1]}\nFood: {data[2]}\n"
                f"Place: {data[3]}\nSpaciousness: {data[4]}\nConvo: {data[5]}\n"
                f"Vibe: {data[6]}")
    except Exception as e:
        logger.error("Error formatting row: %s", e)
        return "Error formatting row."

async def show_current_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        logger.error("show_current_data expects a text message.")
        return ConversationHandler.END
    field = update.message.text.lower()
    context.user_data["field_to_edit"] = field
    if field in ["food", "place", "spaciousness", "convo"]:
        await update.message.reply_text(f"Enter new score for {field.capitalize()} (1-5):",
                                        reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
        return ASK_NEW_VALUE
    elif field == "vibe":
        await update.message.reply_text("Select new vibe:", reply_markup=InlineKeyboardMarkup(VIBE_BUTTONS))
        return ASK_NEW_VALUE
    else:
        await update.message.reply_text("Invalid field. Try again:")
        return SHOW_CURRENT_DATA

async def ask_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        logger.error("ask_new_value expects a callback query.")
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data["new_value"] = query.data
    field = context.user_data.get("field_to_edit", "")
    await query.edit_message_text(f"New {field.capitalize()}: {query.data}\nConfirm? (yes/no):")
    return CONFIRM_EDIT

async def confirm_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        logger.error("confirm_edit expects a text message.")
        return ConversationHandler.END
    if update.message.text.lower() == "yes":
        row = context.user_data.get("edit_row")
        field = context.user_data.get("field_to_edit")
        new_value = context.user_data.get("new_value")
        col_map = {"food": 3, "place": 4, "spaciousness": 5, "convo": 6, "vibe": 7}
        if field in col_map and row:
            await update_cell(row["index"], col_map[field], new_value)
            await update.message.reply_text("Data updated!", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
        else:
            await update.message.reply_text("Error: Invalid field or row data.")
    else:
        await update.message.reply_text("Edit canceled.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    context.user_data.clear()
    return ConversationHandler.END

async def show_rankings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        all_data = await get_all_values()
    except Exception as e:
        logger.error("Error retrieving data for rankings: %s", e)
        await update.callback_query.edit_message_text("Error retrieving data.")
        return
    # Skip header row
    all_data = all_data[1:]
    good_vibes = [row for row in all_data if len(row) >= 7 and row[6].lower() == "good"]
    bad_vibes = [row for row in all_data if len(row) >= 7 and row[6].lower() == "bad"]

    if not good_vibes or not bad_vibes:
        if update.callback_query:
            await update.callback_query.edit_message_text("Not enough data for rankings.")
        return

    attrs = {"food": 2, "place": 3, "spaciousness": 4, "convo": 5}
    try:
        good_avg = {attr: sum(int(row[col]) for row in good_vibes) / len(good_vibes) for attr, col in attrs.items()}
        bad_avg = {attr: sum(int(row[col]) for row in bad_vibes) / len(bad_vibes) for attr, col in attrs.items()}
    except Exception as e:
        logger.error("Error calculating averages: %s", e)
        if update.callback_query:
            await update.callback_query.edit_message_text("Error calculating averages.")
        return

    diffs = {attr: good_avg[attr] - bad_avg[attr] for attr in attrs}
    ranking = sorted(diffs.items(), key=lambda x: x[1], reverse=True)
    ranking_text = "Ranking of attributes for good vibes:\n" + "\n".join(
        f"{i+1}. {attr.capitalize()} (difference: {diff:.2f})" for i, (attr, diff) in enumerate(ranking)
    )
    good_avg_text = "Good vibes averages:\n" + "\n".join(f"- {k.capitalize()}: {v:.2f}" for k, v in good_avg.items())
    bad_avg_text = "Bad vibes averages:\n" + "\n".join(f"- {k.capitalize()}: {v:.2f}" for k, v in bad_avg.items())

    if update.callback_query:
        await update.callback_query.edit_message_text(f"{ranking_text}\n\n{good_avg_text}\n\n{bad_avg_text}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Operation canceled.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    context.user_data.clear()
    return ConversationHandler.END

# Add handlers before initialization
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start), CallbackQueryHandler(button)],
    states={
        ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password)],
        ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
        ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_date)],
        ASK_FOOD: [CallbackQueryHandler(ask_food)],
        ASK_PLACE: [CallbackQueryHandler(ask_place)],
        ASK_SPACIOUSNESS: [CallbackQueryHandler(ask_spaciousness)],
        ASK_CONVO: [CallbackQueryHandler(ask_convo)],
        ASK_VIBE: [CallbackQueryHandler(ask_vibe)],
        CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
        ASK_NAME_FOR_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name_for_edit)],
        ASK_DATE_FOR_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_date_for_edit)],
        SHOW_CURRENT_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, show_current_data)],
        ASK_NEW_VALUE: [CallbackQueryHandler(ask_new_value)],
        CONFIRM_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_edit)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    per_message=True
)
application.add_handler(conv_handler)

# Asynchronous startup function
async def startup():
    logger.info("Initializing application...")
    try:
        await application.initialize()
        logger.info("Application initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize application: {e}")
        raise

# Override Quart's run method to include startup
async def run_with_startup():
    await startup()
    logger.info("Starting the application")
    await app.run_task(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# Error handler for Quart
@app.errorhandler(Exception)
async def handle_exception(e):
    logger.error(f"Unhandled exception: {e}")
    return "Internal Server Error", 500

# Webhook endpoint
@app.route('/webhook', methods=['POST'])
async def webhook():
    logger.info("Webhook received a request")
    update_json = await request.get_json()
    logger.info(f"Raw update JSON: {json.dumps(update_json)}")
    update = Update.de_json(update_json, application.bot)
    logger.debug("Processing update: %s", update.update_id)
    await application.process_update(update)
    logger.debug("Update processed: %s", update.update_id)
    return '', 200

@app.route('/')
async def home():
    return "Bot is running"

if __name__ == "__main__":
    asyncio.run(run_with_startup())
