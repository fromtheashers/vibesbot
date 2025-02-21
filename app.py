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

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Quart(__name__)

# Environment variables
TOKEN = os.environ.get("TELEGRAM_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
SELF_PING_URL = os.environ.get("SELF_PING_URL")  # e.g. "https://your-app.onrender.com/"

if not TOKEN:
    logger.error("TELEGRAM_TOKEN is not set. Bot cannot start.")
    raise ValueError("TELEGRAM_TOKEN environment variable is required.")
if not SHEET_ID:
    logger.error("SHEET_ID is not set. Bot cannot start.")
    raise ValueError("SHEET_ID environment variable is required.")
if not GOOGLE_API_KEY:
    logger.error("GOOGLE_API_KEY is not set. Bot cannot start.")
    raise ValueError("GOOGLE_API_KEY environment variable is required.")

# Google Sheets API base URL
BASE_URL = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values"

# Define conversation states
(ASK_PASSWORD, ASK_NAME, ASK_DATE, ASK_FOOD, ASK_PLACE, ASK_SPACIOUSNESS,
 ASK_CONVO, ASK_VIBE, CONFIRM,
 SELECT_RECORD_EDIT, SHOW_CURRENT_DATA, ASK_NEW_VALUE, CONFIRM_EDIT,
 SELECT_RECORD_DELETE, CONFIRM_DELETE) = range(15)

# Inline Keyboards
SCORE_BUTTONS = [[InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]]
VIBE_BUTTONS = [[InlineKeyboardButton("Good", callback_data="good"),
                 InlineKeyboardButton("Bad", callback_data="bad")]]
MAIN_MENU = [
    [InlineKeyboardButton("Input Vibe Data", callback_data="input")],
    [InlineKeyboardButton("Edit Vibe Data", callback_data="edit")],
    [InlineKeyboardButton("View Current Rankings", callback_data="rankings")],
    [InlineKeyboardButton("Delete Vibe Data", callback_data="delete")]
]

WELCOME_TEXT = (
    "Welcome to GoodVibesBot! 🎉\n"
    "This bot helps you track and analyze vibes from different places.\n\n"
    "Please enter the password to proceed."
)

# Helper: Convert a column number to an Excel-style letter
def col_to_letter(n):
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result

# Helper: Validate a date string in DD/MM/YYYY format
def is_valid_date(date_str):
    try:
        datetime.strptime(date_str, "%d/%m/%Y")
        return True
    except ValueError:
        return False

# --- Google Sheets API Helper Functions ---
async def append_row(values):
    url = f"{BASE_URL}/Sheet1!A1:G1:append?valueInputOption=RAW&key={GOOGLE_API_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"values": [values]}) as response:
            if response.status != 200:
                text = await response.text()
                logger.error(f"Failed to append row: {text}")
                raise Exception(f"Failed to append row: {text}")

async def get_all_values():
    url = f"{BASE_URL}/Sheet1!A:G?key={GOOGLE_API_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                text = await response.text()
                logger.error(f"Failed to get data: {text}")
                raise Exception(f"Failed to get data: {text}")
            data = await response.json()
            return data.get("values", [])

async def update_cell(row, col, value):
    url = f"{BASE_URL}/Sheet1!{col_to_letter(col)}{row}?key={GOOGLE_API_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.put(url, json={"values": [[value]]}, params={"valueInputOption": "RAW"}) as response:
            if response.status != 200:
                text = await response.text()
                logger.error(f"Failed to update cell: {text}")
                raise Exception(f"Failed to update cell: {text}")

async def delete_row(row_index):
    range_str = f"Sheet1!A{row_index}:G{row_index}"
    url = f"{BASE_URL}/{range_str}:clear?key={GOOGLE_API_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url) as response:
            if response.status != 200:
                text = await response.text()
                logger.error(f"Failed to delete row {row_index}: {text}")
                raise Exception(f"Failed to delete row {row_index}: {text}")

# New helper: List records formatted with an auto-counter
async def list_records_formatted():
    data = await get_all_values()
    records = data[1:] if len(data) > 1 else []
    valid_records = []
    for sheet_index, row in enumerate(records, start=2):
        try:
            d = datetime.strptime(row[1], "%d/%m/%Y")
            valid_records.append((sheet_index, row, d))
        except Exception:
            continue
    valid_records.sort(key=lambda x: x[2], reverse=True)
    if not valid_records:
        return "No records found.", {}
    lines = []
    mapping = {}
    for counter, (sheet_index, row, d) in enumerate(valid_records, start=1):
        mapping[counter] = {"index": sheet_index, "data": row}
        line = (f"{counter}. {row[0]} | {row[1]} | {row[3]} | "
                f"Food: {row[2]}, Spaciousness: {row[4]}, Convo: {row[5]}, Vibe: {row[6]}")
        lines.append(line)
    formatted = "\n".join(lines)
    return formatted, mapping

# --- Build the Telegram Application (global) ---
application = Application.builder().token(TOKEN).build()

# --- Bot Handlers ---
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Start handler received: %s", update.message.text if update.message else "None")
    if update.message:
        await update.message.reply_text(WELCOME_TEXT)
        logger.info("Sent welcome text to user %s", update.message.from_user.id)
    return ASK_PASSWORD

async def ask_password_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text if update.message else ""
    logger.debug("ask_password received: %s", text)
    if text == "vibes":
        if update.message:
            await update.message.reply_text("Access granted! What would you like to do?",
                                              reply_markup=InlineKeyboardMarkup(MAIN_MENU))
        return ConversationHandler.END
    else:
        if update.message:
            await update.message.reply_text("Incorrect password. Please try again.")
        return ASK_PASSWORD

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        logger.error("Button handler: no callback_query")
        return ConversationHandler.END
    await query.answer()
    if query.data == "input":
        await query.edit_message_text("Please enter the name of the place:")
        return ASK_NAME
    elif query.data == "edit":
        formatted, mapping = await list_records_formatted()
        if mapping:
            context.user_data["record_list_edit"] = mapping
            await query.edit_message_text("Select a record to edit by sending its number:\n" + formatted)
            return SELECT_RECORD_EDIT
        else:
            await query.edit_message_text("No records found.")
            return ConversationHandler.END
    elif query.data == "delete":
        formatted, mapping = await list_records_formatted()
        if mapping:
            context.user_data["record_list_delete"] = mapping
            await query.edit_message_text("Select a record to delete by sending its number:\n" + formatted)
            return SELECT_RECORD_DELETE
        else:
            await query.edit_message_text("No records found.")
            return ConversationHandler.END
    elif query.data == "rankings":
        await show_rankings(update, context)
        return ConversationHandler.END
    else:
        await query.answer("Unknown option selected.", show_alert=True)
        return ConversationHandler.END

async def ask_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["vibe_data"] = {}
    context.user_data["vibe_data"]["name"] = update.message.text
    await update.message.reply_text("Enter the date (DD/MM/YYYY):")
    return ASK_DATE

async def ask_date_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_text = update.message.text
    if not re.match(r"^\d{2}/\d{2}/\d{4}$", date_text) or not is_valid_date(date_text):
        await update.message.reply_text("Invalid format or date. Please use DD/MM/YYYY:")
        return ASK_DATE
    context.user_data["vibe_data"]["date"] = date_text
    await update.message.reply_text("Score for Food (1-5):", reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
    return ASK_FOOD

async def ask_food_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        context.user_data["vibe_data"]["food"] = int(query.data)
    except ValueError:
        await query.edit_message_text("Invalid input for food score. Please try again:")
        return ASK_FOOD
    await query.edit_message_text("Score for Place (1-5):", reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
    return ASK_PLACE

async def ask_place_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        context.user_data["vibe_data"]["place"] = int(query.data)
    except ValueError:
        await query.edit_message_text("Invalid input for place score. Please try again:")
        return ASK_PLACE
    await query.edit_message_text("Score for Spaciousness (1-5):", reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
    return ASK_SPACIOUSNESS

async def ask_spaciousness_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        context.user_data["vibe_data"]["spaciousness"] = int(query.data)
    except ValueError:
        await query.edit_message_text("Invalid input for spaciousness score. Please try again:")
        return ASK_SPACIOUSNESS
    await query.edit_message_text("Score for Convo (1-5):", reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
    return ASK_CONVO

async def ask_convo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        context.user_data["vibe_data"]["convo"] = int(query.data)
    except ValueError:
        await query.edit_message_text("Invalid input for convo score. Please try again:")
        return ASK_CONVO
    await query.edit_message_text("Is the vibe good or bad?", reply_markup=InlineKeyboardMarkup(VIBE_BUTTONS))
    return ASK_VIBE

async def ask_vibe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() == "yes":
        data = context.user_data.get("vibe_data", {})
        await append_row([data.get("name"), data.get("date"), data.get("food"),
                          data.get("place"), data.get("spaciousness"),
                          data.get("convo"), data.get("vibe")])
        await update.message.reply_text("Data saved!", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    else:
        await update.message.reply_text("Input canceled.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    context.user_data.clear()
    return ConversationHandler.END

# --- New Handlers for Editing via Record Selection ---
async def select_record_edit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        choice = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Invalid selection. Please enter a number.")
        return SELECT_RECORD_EDIT
    mapping = context.user_data.get("record_list_edit", {})
    if choice not in mapping:
        await update.message.reply_text("Selection out of range. Please enter a valid number.")
        return SELECT_RECORD_EDIT
    context.user_data["selected_record_edit"] = mapping[choice]
    record = mapping[choice]["data"]
    formatted = (f"Name: {record[0]}\nDate: {record[1]}\nFood: {record[2]}\n"
                 f"Place: {record[3]}\nSpaciousness: {record[4]}\nConvo: {record[5]}\nVibe: {record[6]}")
    await update.message.reply_text(f"You selected:\n{formatted}\nWhich field do you want to edit? (Food, Place, Spaciousness, Convo, Vibe)")
    return SHOW_CURRENT_DATA

# --- New Handlers for Deletion via Record Selection ---
async def select_record_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        choice = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Invalid selection. Please enter a number.")
        return SELECT_RECORD_DELETE
    mapping = context.user_data.get("record_list_delete", {})
    if choice not in mapping:
        await update.message.reply_text("Selection out of range. Please enter a valid number.")
        return SELECT_RECORD_DELETE
    context.user_data["selected_record_delete"] = mapping[choice]
    record = mapping[choice]["data"]
    formatted = (f"Name: {record[0]}\nDate: {record[1]}\nFood: {record[2]}\n"
                 f"Place: {record[3]}\nSpaciousness: {record[4]}\nConvo: {record[5]}\nVibe: {record[6]}")
    await update.message.reply_text(f"You selected:\n{formatted}\nAre you sure you want to delete this record? (yes/no)")
    return CONFIRM_DELETE

async def confirm_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() == "yes":
        record = context.user_data.get("selected_record_delete")
        if record:
            await delete_row(record["index"])
            await update.message.reply_text("Record deleted.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
        else:
            await update.message.reply_text("Error: Record not found.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    else:
        await update.message.reply_text("Deletion canceled.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    context.user_data.clear()
    return ConversationHandler.END

async def show_current_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def ask_new_value_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["new_value"] = query.data
    field = context.user_data.get("field_to_edit", "")
    await query.edit_message_text(f"New {field.capitalize()}: {query.data}\nConfirm? (yes/no):")
    return CONFIRM_EDIT

async def confirm_edit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() == "yes":
        record = context.user_data.get("selected_record_edit")
        field = context.user_data.get("field_to_edit")
        new_value = context.user_data.get("new_value")
        col_map = {"food": 3, "place": 4, "spaciousness": 5, "convo": 6, "vibe": 7}
        if field in col_map and record:
            await update_cell(record["index"], col_map[field], new_value)
            await update.message.reply_text("Data updated!", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
        else:
            await update.message.reply_text("Error: Invalid field or record data.")
    else:
        await update.message.reply_text("Edit canceled.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    context.user_data.clear()
    return ConversationHandler.END

async def show_rankings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        all_data = await get_all_values()
    except Exception as e:
        if update.callback_query:
            await update.callback_query.edit_message_text("Error retrieving data.")
        return
    records = all_data[1:]
    good_vibes = [row for row in records if len(row) >= 7 and row[6].lower() == "good"]
    bad_vibes = [row for row in records if len(row) >= 7 and row[6].lower() == "bad"]
    if not good_vibes or not bad_vibes:
        if update.callback_query:
            await update.callback_query.edit_message_text("Not enough data for rankings.")
        return
    attrs = {"food": 2, "place": 3, "spaciousness": 4, "convo": 5}
    try:
        good_avg = {attr: sum(int(row[col]) for row in good_vibes) / len(good_vibes)
                    for attr, col in attrs.items()}
        bad_avg = {attr: sum(int(row[col]) for row in bad_vibes) / len(bad_vibes)
                   for attr, col in attrs.items()}
    except Exception as e:
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

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation canceled.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    context.user_data.clear()
    return ConversationHandler.END

# --- Self-Pinging Background Task ---
async def self_ping():
    url = SELF_PING_URL or f"http://localhost:{os.environ.get('PORT', 5000)}/"
    logger.info("Starting self-ping on URL: %s", url)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    text = await response.text()
                    logger.debug("Self-ping successful, response: %s", text)
        except Exception as e:
            logger.error("Self-ping failed: %s", e)
        await asyncio.sleep(300)  # every 5 minutes

# --- Conversation Handler Setup ---
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start_handler), CallbackQueryHandler(button_handler)],
    states={
        # Data input flow
        ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password_handler)],
        ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name_handler)],
        ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_date_handler)],
        ASK_FOOD: [CallbackQueryHandler(ask_food_handler)],
        ASK_PLACE: [CallbackQueryHandler(ask_place_handler)],
        ASK_SPACIOUSNESS: [CallbackQueryHandler(ask_spaciousness_handler)],
        ASK_CONVO: [CallbackQueryHandler(ask_convo_handler)],
        ASK_VIBE: [CallbackQueryHandler(ask_vibe_handler)],
        CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_handler)],
        # Editing flow
        SELECT_RECORD_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_record_edit_handler)],
        SHOW_CURRENT_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, show_current_data_handler)],
        ASK_NEW_VALUE: [CallbackQueryHandler(ask_new_value_handler)],
        CONFIRM_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_edit_handler)],
        # Deletion flow
        SELECT_RECORD_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_record_delete_handler)],
        CONFIRM_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_delete_handler)],
    },
    fallbacks=[CommandHandler("cancel", cancel_handler)]
)
application.add_handler(conv_handler)

# --- Quart Startup Hook ---
@app.before_serving
async def startup():
    await application.initialize()
    bot_me = await application.bot.get_me()
    logger.info("Telegram Application initialized. Bot info: %s", bot_me)
    app.add_background_task(self_ping)

# --- Webhook Endpoint ---
@app.route('/webhook', methods=['POST'])
async def webhook():
    logger.info("Webhook received a request")
    update_json = await request.get_json()
    logger.info("Raw update JSON: %s", json.dumps(update_json))
    update = Update.de_json(update_json, application.bot)
    logger.debug("Processing update: %s", update.update_id)
    await application.process_update(update)
    logger.debug("Update processed: %s", update.update_id)
    return '', 200

@app.route('/')
async def home():
    return "Bot is running"

if __name__ == "__main__":
    asyncio.run(app.run_task(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))))
