import asyncio
import logging
import os
import psycopg

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)


# For debugging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]

MAX_ACTIVITIES = 10

ADD_NAME, ADD_DESCRIPTION, DELETE_ACTIVITY, REORDER_ACTIVITIES, SET_CURRENT = range(5)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["Show routine", "Next activity"],
        ["Add activity", "Delete activity"],
        ["Reorder activities", "Start from activity"],
    ],

    resize_keyboard=True,
)


def connect_database():
    """Open a PostgreSQL connection with a bounded connection wait."""
    return psycopg.connect(DATABASE_URL, connect_timeout=10)



def initialize_database() -> None:
    """Create the database tables used to store user routines and activities."""

    with connect_database() as connection:

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_routines (
                telegram_user_id BIGINT PRIMARY KEY,
                current_index INTEGER NOT NULL DEFAULT 0 CHECK (current_index >= 0)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS routine_activities (
                telegram_user_id BIGINT NOT NULL REFERENCES user_routines(telegram_user_id) ON DELETE CASCADE,
                position INTEGER NOT NULL CHECK (position >= 0 AND position < 10),
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                PRIMARY KEY (telegram_user_id, position)
            )
            """
        )

def get_routine(user_id: int) -> dict:
    """Load one user's routine and activities in their saved order."""

    with connect_database() as connection:

        row = connection.execute(
            "SELECT current_index FROM user_routines WHERE telegram_user_id = %s", (user_id,)
        ).fetchone()

        if row is None:
            return {"activities": [], "current_index": 0}
        
        activity_rows = connection.execute(
            """
            SELECT name, description FROM routine_activities
            WHERE telegram_user_id = %s ORDER BY position
            """,
            (user_id,),
        ).fetchall()

    activities = [{"name": name, "description": description} for name, description in activity_rows]
    current_index = row[0] % len(activities) if activities else 0

    return {"activities": activities, "current_index": current_index}

def save_routine(user_id: int, routine: dict) -> None:
    """Save one user's current index and replace their stored activities."""

    activities = routine["activities"]
    current_index = routine["current_index"] if activities else 0

    with connect_database() as connection:

        connection.execute(
            """
            INSERT INTO user_routines (telegram_user_id, current_index)
            VALUES (%s, %s)
            ON CONFLICT (telegram_user_id)
            DO UPDATE SET current_index = EXCLUDED.current_index
            """,
            (user_id, current_index),
        )

        connection.execute(
            "DELETE FROM routine_activities WHERE telegram_user_id = %s", (user_id,)
        )
        
        connection.executemany(
            """
            INSERT INTO routine_activities (telegram_user_id, position, name, description)
            VALUES (%s, %s, %s, %s)
            """,
            [
                (user_id, position, activity["name"], activity["description"])
                for position, activity in enumerate(activities)
            ],
        )


async def get_routine_async(user_id: int) -> dict:
    """Load a routine without blocking the Telegram event loop."""
    return await asyncio.to_thread(get_routine, user_id)


async def save_routine_async(user_id: int, routine: dict) -> None:
    """Save a routine without blocking the Telegram event loop."""
    await asyncio.to_thread(save_routine, user_id, routine)

def format_routine(routine: dict) -> str:
    """Format a routine as a readable list and mark its current activity."""

    activities = routine["activities"]

    if not activities:
        return "Your routine is empty. Add your first activity to get started."

    lines = ["Your routine:"]

    for index, activity in enumerate(activities):
        marker = "▶️" if index == routine["current_index"] else "▫️"
        description = f" — {activity['description']}" if activity["description"] else ""
        lines.append(f"{marker} {index + 1}. {activity['name']}{description}")

    return "\n".join(lines)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome the user and show their routine with the main menu."""

    routine = await get_routine_async(update.effective_user.id)

    await update.message.reply_text(
        "Welcome! Create and manage your personal activity routine using the menu below.\n\n"
        + format_routine(routine),
        reply_markup=MAIN_KEYBOARD,
    )

async def show_routine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the user's current routine."""

    await update.message.reply_text(
        format_routine(await get_routine_async(update.effective_user.id)), reply_markup=MAIN_KEYBOARD
    )

async def begin_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start adding an activity if the routine has fewer than ten."""

    routine = await get_routine_async(update.effective_user.id)

    if len(routine["activities"]) >= MAX_ACTIVITIES:
        await update.message.reply_text(
            f"Your routine already has {MAX_ACTIVITIES} activities. Delete one before adding another.",
            reply_markup=MAIN_KEYBOARD,
        )

        return ConversationHandler.END
    
    await update.message.reply_text("What is the activity name? Send /cancel to stop.")

    return ADD_NAME

async def receive_activity_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the new activity's name and ask for its description."""

    name = update.message.text.strip()

    if not name:
        await update.message.reply_text("The name cannot be empty. Please choose an activity name.")
        return ADD_NAME
    
    context.user_data["new_activity_name"] = name
    await update.message.reply_text(
        "Add a short description, or send /skip to leave it blank."
    )

    return ADD_DESCRIPTION

async def complete_add_activity(
    update: Update, context: ContextTypes.DEFAULT_TYPE, description: str
) -> int:
    """Save the pending activity, allowing an empty description."""
    user_id = update.effective_user.id
    name = context.user_data.get("new_activity_name")
    if not name:
        await update.message.reply_text(
            "I couldn't find the activity name. Please start again with Add activity.",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    try:
        logging.info("Loading routine to add an activity for user %s", user_id)
        routine = await get_routine_async(user_id)
        if len(routine["activities"]) >= MAX_ACTIVITIES:
            context.user_data.pop("new_activity_name", None)
            await update.message.reply_text(
                f"Your routine already has {MAX_ACTIVITIES} activities. Delete one before adding another.",
                reply_markup=MAIN_KEYBOARD,
            )
            return ConversationHandler.END

        routine["activities"].append({"name": name, "description": description})
        logging.info("Saving new activity for user %s", user_id)
        await save_routine_async(user_id, routine)
        logging.info("Saved new activity for user %s", user_id)
    except psycopg.Error:
        logging.exception("Failed to save a new activity for user %s", user_id)
        context.user_data.pop("new_activity_name", None)
        await update.message.reply_text(
            "I couldn't save the activity because the database is unavailable. Please try again later.",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    context.user_data.pop("new_activity_name", None)
    await update.message.reply_text("Activity added.\n\n" + format_routine(routine), reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


async def receive_activity_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the new activity with the description provided by the user."""
    return await complete_add_activity(update, context, update.message.text.strip())


async def skip_activity_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the new activity without a description."""
    logging.info("Received /skip command for user %s", update.effective_user.id)
    return await complete_add_activity(update, context, "")

async def begin_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show the routine and ask which activity the user wants to delete."""

    routine = await get_routine_async(update.effective_user.id)

    if not routine["activities"]:
        await update.message.reply_text("Your routine is empty.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    
    await update.message.reply_text(
        format_routine(routine) + "\n\nSend the number of the activity to delete, or /cancel."
    )

    return DELETE_ACTIVITY

async def delete_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Delete the selected activity and adjust the current index."""

    routine = await get_routine_async(update.effective_user.id)

    try:
        position = int(update.message.text.strip()) - 1

    except ValueError:
        await update.message.reply_text("Send an activity number from the list, or /cancel.")
        return DELETE_ACTIVITY
    
    if not 0 <= position < len(routine["activities"]):
        await update.message.reply_text("That number is not in the list. Try again, or /cancel.")
        return DELETE_ACTIVITY

    removed = routine["activities"].pop(position)

    if not routine["activities"]:
        routine["current_index"] = 0

    elif position < routine["current_index"]:
        routine["current_index"] -= 1

    elif position == routine["current_index"]:
        routine["current_index"] %= len(routine["activities"])

    await save_routine_async(update.effective_user.id, routine)

    await update.message.reply_text(
        f"Deleted: {removed['name']}\n\n{format_routine(routine)}", reply_markup=MAIN_KEYBOARD
    )

    return ConversationHandler.END


async def begin_reorder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show the routine and ask for the desired activity order."""

    routine = await get_routine_async(update.effective_user.id)
    count = len(routine["activities"])

    if count < 2:
        await update.message.reply_text(
            "Add at least two activities before reordering.", reply_markup=MAIN_KEYBOARD
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        format_routine(routine)
        + "\n\nSend the activity numbers in the new order, separated by spaces. "
        + f"For example: {' '.join(str(number) for number in range(count, 0, -1))}. Send /cancel to stop."
    )

    return REORDER_ACTIVITIES

async def reorder_activities(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate and save the user's new activity order."""

    routine = await get_routine_async(update.effective_user.id)

    try:
        order = [int(value) - 1 for value in update.message.text.split()]

    except ValueError:
        order = []

    count = len(routine["activities"])

    if len(order) != count or sorted(order) != list(range(count)):
        await update.message.reply_text(
            f"Please list every number from 1 to {count} exactly once, separated by spaces."
        )
        return REORDER_ACTIVITIES

    old_activities = routine["activities"]
    old_current_index = routine["current_index"]
    routine["activities"] = [old_activities[index] for index in order]
    routine["current_index"] = order.index(old_current_index)

    await save_routine_async(update.effective_user.id, routine)
    await update.message.reply_text("Routine reordered.\n\n" + format_routine(routine), reply_markup=MAIN_KEYBOARD)
    
    return ConversationHandler.END

async def begin_set_current(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show the routine and ask which activity should become current."""

    routine = await get_routine_async(update.effective_user.id)

    if not routine["activities"]:
        await update.message.reply_text("Your routine is empty.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
        
    await update.message.reply_text(
        format_routine(routine) + "\n\nSend the number of the activity to start from, or /cancel."
    )

    return SET_CURRENT

async def set_current(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Set the selected activity as the routine's current activity."""

    routine = await get_routine_async(update.effective_user.id)

    try:
        position = int(update.message.text.strip()) - 1

    except ValueError:
        await update.message.reply_text("Send an activity number from the list, or /cancel.")
        return SET_CURRENT
    
    if not 0 <= position < len(routine["activities"]):
        await update.message.reply_text("That number is not in the list. Try again, or /cancel.")
        return SET_CURRENT
    
    routine["current_index"] = position
    await save_routine_async(update.effective_user.id, routine)
    activity = routine["activities"][position]

    await update.message.reply_text(
        f"Your routine will start from: {activity['name']}\n\n{format_routine(routine)}",
        reply_markup=MAIN_KEYBOARD,
    )

    return ConversationHandler.END

async def next_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Advance the user's routine to the next activity and save it."""

    user_id = update.effective_user.id
    routine = await get_routine_async(user_id)
    activities = routine["activities"]

    if not activities:
        await update.message.reply_text("Your routine is empty. Add an activity first.", reply_markup=MAIN_KEYBOARD)
        return
    
    routine["current_index"] = (routine["current_index"] + 1) % len(activities)
    await save_routine_async(user_id, routine)
    current = activities[routine["current_index"]]

    await update.message.reply_text(
        f"Activity completed. Your next activity is: {current['name']}\n{current['description']}",
        reply_markup=MAIN_KEYBOARD,
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the active conversation and return to the main menu."""

    context.user_data.pop("new_activity_name", None)
    await update.message.reply_text("Action cancelled.", reply_markup=MAIN_KEYBOARD)

    return ConversationHandler.END



def main() -> None:
    """Configure the bot and start its webhook server."""

    initialize_database()

    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        ConversationHandler(
            entry_points=[
                CommandHandler("add", begin_add),
                MessageHandler(filters.Regex(r"^Add activity$"), begin_add),
                CommandHandler("delete", begin_delete),
                MessageHandler(filters.Regex(r"^Delete activity$"), begin_delete),
                CommandHandler("reorder", begin_reorder),
                MessageHandler(filters.Regex(r"^Reorder activities$"), begin_reorder),
                CommandHandler("startfrom", begin_set_current),
                MessageHandler(filters.Regex(r"^Start from activity$"), begin_set_current),
            ],
            states={
                ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_activity_name)],
                ADD_DESCRIPTION: [
                    CommandHandler("skip", skip_activity_description),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, receive_activity_description),
                ],
                DELETE_ACTIVITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_activity)],
                REORDER_ACTIVITIES: [MessageHandler(filters.TEXT & ~filters.COMMAND, reorder_activities)],
                SET_CURRENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_current)],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            allow_reentry=True,
        )
    )
    app.add_handler(CommandHandler("routine", show_routine))
    app.add_handler(CommandHandler("next", next_activity))
    app.add_handler(MessageHandler(filters.Regex(r"^Show routine$"), show_routine))
    app.add_handler(MessageHandler(filters.Regex(r"^Next activity$"), next_activity))
    render_url = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
    webhook_secret = os.environ["WEBHOOK_SECRET"]
    webhook_path = "telegram-webhook"

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        url_path=webhook_path,
        webhook_url=f"{render_url}/{webhook_path}",
        secret_token=webhook_secret,
    )


if __name__ == "__main__":
    main()
