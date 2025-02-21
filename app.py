import os
import re
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ConversationHandler, MessageHandler, ContextTypes,
    filters
)

import gspread
from datetime import datetime
app = Flask(__name__)

# Telegram Bot Token (set via environment variable later)
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Google Sheets Setup (Publicly editable sheet, no credentials)
SHEET_ID = "1uOl8diQh5ic9iqHjsq_ohyKp2fo4GAEzBhyIfZBPfF0"  # Replace with your SHEET_ID
gc = gspread.Client(None)  # No authentication for public sheet
sheet = gc.open_by_key(SHEET_ID).sheet1

# Conversation States
(ASK_PASSWORD, ASK_NAME, ASK_DATE, ASK_FOOD, ASK_PLACE, ASK_SPACIOUSNESS, ASK_CONVO,
 ASK_VIBE, CONFIRM, ASK_NAME_FOR_EDIT, ASK_DATE_FOR_EDIT, SHOW_CURRENT_DATA,
 ASK_FIELD_TO_EDIT, ASK_NEW_VALUE, CONFIRM_EDIT) = range(15)

# Inline Keyboards
SCORE_BUTTONS = [[InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]]
VIBE_BUTTONS = [
    [InlineKeyboardButton("Good", callback_data="good"),
     InlineKeyboardButton("Bad", callback_data="bad")]
]

# Main Menu
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Prompt the user for the password
    await update.message.reply_text(WELCOME_TEXT)
    return ASK_PASSWORD

async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Validate the password
    if update.message.text == "vibes":
        await update.message.reply_text(
            "Access granted! What would you like to do?",
            reply_markup=InlineKeyboardMarkup(MAIN_MENU)
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text("Incorrect password. Please try again.")
        return ASK_PASSWORD

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "input":
        await query.edit_message_text("Please enter the name of the place:")
        return ASK_NAME
    elif query.data == "edit":
        await query.edit_message_text("Enter the name of the place to edit:")
        return ASK_NAME_FOR_EDIT
    elif query.data == "rankings":
        await show_rankings(update, context)
        return ConversationHandler.END

# Input Vibe Data Flow
async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["vibe_data"] = {}
    context.user_data["vibe_data"]["name"] = update.message.text
    await update.message.reply_text("Enter the date (DD/MM/YYYY):")
    return ASK_DATE

async def ask_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    query = update.callback_query
    await query.answer()
    context.user_data["vibe_data"]["food"] = int(query.data)
    await query.edit_message_text("Score for Place (1-5):", reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
    return ASK_PLACE

async def ask_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["vibe_data"]["place"] = int(query.data)
    await query.edit_message_text("Score for Spaciousness (1-5):", reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
    return ASK_SPACIOUSNESS

async def ask_spaciousness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["vibe_data"]["spaciousness"] = int(query.data)
    await query.edit_message_text("Score for Convo (1-5):", reply_markup=InlineKeyboardMarkup(SCORE_BUTTONS))
    return ASK_CONVO

async def ask_convo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["vibe_data"]["convo"] = int(query.data)
    await query.edit_message_text("Is the vibe good or bad?", reply_markup=InlineKeyboardMarkup(VIBE_BUTTONS))
    return ASK_VIBE

async def ask_vibe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["vibe_data"]["vibe"] = query.data
    data = context.user_data["vibe_data"]
    confirm_text = (
        f"Confirm your input:\n"
        f"Name: {data['name']}\n"
        f"Date: {data['date']}\n"
        f"Food: {data['food']}\n"
        f"Place: {data['place']}\n"
        f"Spaciousness: {data['spaciousness']}\n"
        f"Convo: {data['convo']}\n"
        f"Vibe: {data['vibe']}\n"
        "Reply 'yes' to save, 'no' to cancel:"
    )
    await query.edit_message_text(confirm_text)
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() == "yes":
        data = context.user_data["vibe_data"]
        sheet.append_row([data["name"], data["date"], data["food"], data["place"],
                          data["spaciousness"], data["convo"], data["vibe"]])
        await update.message.reply_text("Data saved!", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    else:
        await update.message.reply_text("Input canceled.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    context.user_data.clear()
    return ConversationHandler.END

# Edit Vibe Data Flow
async def ask_name_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["edit_data"] = {"name": update.message.text}
    await update.message.reply_text("Enter the date (DD/MM/YYYY) to identify the entry:")
    return ASK_DATE_FOR_EDIT

async def ask_date_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_text = update.message.text
    if not re.match(r"^\d{2}/\d{2}/\d{4}$", date_text) or not is_valid_date(date_text):
        await update.message.reply_text("Invalid format or date. Please use DD/MM/YYYY:")
        return ASK_DATE_FOR_EDIT
    context.user_data["edit_data"]["date"] = date_text
    row = find_row(context.user_data["edit_data"]["name"], date_text)
    if row:
        context.user_data["edit_row"] = row
        await update.message.reply_text(
            f"Current data:\n{format_row(row)}\nWhich field to edit? (Food, Place, Spaciousness, Convo, Vibe):"
        )
        return SHOW_CURRENT_DATA
    else:
        await update.message.reply_text("Entry not found.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
        return ConversationHandler.END

def find_row(name, date):
    all_data = sheet.get_all_values()[1:]  # Skip header
    for i, row in enumerate(all_data, 2):
        if row[0] == name and row[1] == date:
            return {"index": i, "data": row}
    return None

def format_row(row):
    return (f"Name: {row['data'][0]}\nDate: {row['data'][1]}\nFood: {row['data'][2]}\n"
            f"Place: {row['data'][3]}\nSpaciousness: {row['data'][4]}\nConvo: {row['data'][5]}\n"
            f"Vibe: {row['data'][6]}")

async def show_current_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    query = update.callback_query
    await query.answer()
    context.user_data["new_value"] = query.data
    field = context.user_data["field_to_edit"]
    await query.edit_message_text(f"New {field.capitalize()}: {query.data}\nConfirm? (yes/no):")
    return CONFIRM_EDIT

async def confirm_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() == "yes":
        row = context.user_data["edit_row"]
        field = context.user_data["field_to_edit"]
        new_value = context.user_data["new_value"]
        col_map = {"food": 3, "place": 4, "spaciousness": 5, "convo": 6, "vibe": 7}
        sheet.update_cell(row["index"], col_map[field], new_value)
        await update.message.reply_text("Data updated!", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    else:
        await update.message.reply_text("Edit canceled.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    context.user_data.clear()
    return ConversationHandler.END

# Rankings
async def show_rankings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_data = sheet.get_all_values()[1:]  # Skip header
    good_vibes = [row for row in all_data if row[6] == "good"]
    bad_vibes = [row for row in all_data if row[6] == "bad"]

    if not good_vibes or not bad_vibes:
        await update.callback_query.edit_message_text("Not enough data for rankings.")
        return

    attrs = {"food": 2, "place": 3, "spaciousness": 4, "convo": 5}
    good_avg = {attr: sum(int(row[col]) for row in good_vibes) / len(good_vibes) for attr, col in attrs.items()}
    bad_avg = {attr: sum(int(row[col]) for row in bad_vibes) / len(bad_vibes) for attr, col in attrs.items()}
    diffs = {attr: good_avg[attr] - bad_avg[attr] for attr in attrs}

    ranking = sorted(diffs.items(), key=lambda x: x[1], reverse=True)
    ranking_text = "Ranking of attributes for good vibes:\n" + "\n".join(
        f"{i+1}. {attr.capitalize()} (difference: {diff:.2f})" for i, (attr, diff) in enumerate(ranking)
    )
    good_avg_text = "Good vibes averages:\n" + "\n".join(f"- {k.capitalize()}: {v:.2f}" for k, v in good_avg.items())
    bad_avg_text = "Bad vibes averages:\n" + "\n".join(f"- {k.capitalize()}: {v:.2f}" for k, v in bad_avg.items())

    await update.callback_query.edit_message_text(f"{ranking_text}\n\n{good_avg_text}\n\n{bad_avg_text}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation canceled.", reply_markup=InlineKeyboardMarkup(MAIN_MENU))
    context.user_data.clear()
    return ConversationHandler.END

# Flask Webhook
@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    application.process_update(update)
    return "OK"

# Bot Application
application = Application.builder().token(TOKEN).build()
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start), CallbackQueryHandler(button)],
    states={
        ASK_NAME: [MessageHandler(Filters.text & ~Filters.command, ask_name)],
        ASK_DATE: [MessageHandler(Filters.text & ~Filters.command, ask_date)],
        ASK_FOOD: [CallbackQueryHandler(ask_food)],
        ASK_PLACE: [CallbackQueryHandler(ask_place)],
        ASK_SPACIOUSNESS: [CallbackQueryHandler(ask_spaciousness)],
        ASK_CONVO: [CallbackQueryHandler(ask_convo)],
        ASK_VIBE: [CallbackQueryHandler(ask_vibe)],
        CONFIRM: [MessageHandler(Filters.text & ~Filters.command, confirm)],
        ASK_NAME_FOR_EDIT: [MessageHandler(Filters.text & ~Filters.command, ask_name_for_edit)],
        ASK_DATE_FOR_EDIT: [MessageHandler(Filters.text & ~Filters.command, ask_date_for_edit)],
        SHOW_CURRENT_DATA: [MessageHandler(Filters.text & ~Filters.command, show_current_data)],
        ASK_NEW_VALUE: [CallbackQueryHandler(ask_new_value)],
        CONFIRM_EDIT: [MessageHandler(Filters.text & ~Filters.command, confirm_edit)],
    },
    fallbacks=[CommandHandler("cancel", cancel)]
)
application.add_handler(conv_handler)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
