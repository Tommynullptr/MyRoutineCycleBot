#TODO: put database management here
#TODO: check for single responsability principle: in this file i only want metods that manage queries

import os
import psycopg


DatabaseError = psycopg.Error


DATABASE_URL = os.environ["DATABASE_URL"]

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
        
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO routine_activities (telegram_user_id, position, name, description)
                VALUES (%s, %s, %s, %s)
                """,
                [
                    (user_id, position, activity["name"], activity["description"])
                    for position, activity in enumerate(activities)
                ],
            )
