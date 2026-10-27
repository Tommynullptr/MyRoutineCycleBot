import logging
import json
import os

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters



# Logs to check for errors and debug information
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = "8918095840:AAEHsVLYBxnrB0ETm7lsFTQv412_ca924Pc"

ACTIVITY = ["Palestra 🏋️", "Sviluppo C# 💻", "Streaming/Gaming 🎮"]
ACTIVITY_FILE = "activity_cycle.json"

# Method to load an activity (saved in a file)
def load_activity():
    if os.path.exists(ACTIVITY_FILE):
        with open(ACTIVITY_FILE, "r") as f:
            return json.load(f).get("index", 0)
    return 0

# Method to save the current activity   
def save_activity(index):
    with open(ACTIVITY_FILE, "w") as f:
        json.dump({"index": index}, f)

# Command/start: set custom commands in bot chat
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    index = load_activity()
    current_activity = ACTIVITY[index]

    # User buttons
    keyboard = [[f"Done! ✅ ({current_activity})"], ["Show current activity 🔍"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"Hello! Your routine is active.\n\nCurrent activity: **{current_activity}**",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# Manage click on keyboard
async def manage_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    index = load_activity()

    if text.startswith("Done! ✅"):
        # Go to next activity in the cycle
        next_index = (index + 1) % 3
        save_activity(next_index)
        
        next_activity = ACTIVITY[next_index]
        
        # Update the keyboard with the new activity
        keyboard = [[f"Done! ✅ ({next_activity})"], ["Show current activity 🔍"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"Nice! The activity has been marked as completed. \n\nNext activity in queue: **{next_activity}**",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        
    elif text == "Show current activity 🔍":
        await update.message.reply_text(f"Next activity -> **{ACTIVITY[index]}**", parse_mode="Markdown")

def main():
    # Start bot
    app = Application.builder().token(TOKEN).build()

    # Link commands and buttons to their respective methods
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manage_message))

    # Bot remains active and listens for messages
    print("Bot started successfully...")
    app.run_polling()

if __name__ == '__main__':
    main()
