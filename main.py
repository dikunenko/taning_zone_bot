import json
import os
import re
import sqlite3
import traceback
from datetime import datetime, date
from typing import Dict, Any, Optional, List

import requests

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext


# ==================== КОНФИГУРАЦИЯ ====================
ADMIN_USER_ID = 365244826  # ЗАМЕНИТЕ НА ВАШ TELEGRAM USER ID


# ==================== ЛОГГЕР ====================
class DebugLogger:
    def __init__(self, bot=None, admin_id=None):
        self.bot = bot
        self.admin_id = admin_id
        self.logs = []

    def send_log(self, message: str, message_type: str = "info"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        emoji = {
            "info": "📘",
            "success": "✅",
            "error": "❌",
            "warning": "⚠️",
            "api": "🌐",
            "database": "🗄️",
            "user": "👤"
        }.get(message_type, "📘")

        short_message = f"[{timestamp}] {message}"
        self.logs.append(short_message)
        print(short_message)

        if self.bot and self.admin_id:
            text = f"{emoji} [{timestamp}]\n{message}"
            if len(text) > 3900:
                text = text[:3900] + "\n...[truncated]"
            try:
                self.bot.send_message(
                    chat_id=self.admin_id,
                    text=text,
                    disable_web_page_preview=True
                )
            except Exception as e:
                print(f"Не удалось отправить лог в Telegram: {e}")

    def log_error(self, error: Exception, context: str = ""):
        tb = traceback.format_exc()
        msg = f"ОШИБКА: {str(error)}\nКонтекст: {context}\nТрейсбек:\n{tb}"
        self.send_log(msg, "error")


debug_logger = DebugLogger()


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def safe_json_loads(text: str) -> Dict[str, Any]:
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    fenced_json = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced_json:
        try:
            return json.loads(fenced_json.group(1))
        except Exception:
            pass

    fenced_any = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if fenced_any:
        try:
            return json.loads(fenced_any.group(1))
        except Exception:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            return json.loads(candidate)
        except Exception:
            pass

    raise ValueError(f"Не удалось распарсить JSON из ответа модели: {text[:500]}")


def today_iso() -> str:
    return date.today().isoformat()


def to_float_or_none(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None


def to_int_or_none(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except Exception:
        return None


# ==================== БАЗА ДАННЫХ ====================
class Database:
    def __init__(self, db_name: str = "training_zone.db"):
        self.db_name = db_name
        self.init_db()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self):
        conn = self.get_conn()
        cur = conn.cursor()

        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS raw_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                entry_type TEXT NOT NULL CHECK(entry_type IN ('training', 'nutrition', 'chat')),
                raw_text TEXT NOT NULL,
                source TEXT DEFAULT 'telegram',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS training_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                raw_entry_id INTEGER,
                session_date DATE NOT NULL,
                started_at TIMESTAMP,
                notes TEXT,
                perceived_effort INTEGER,
                duration_minutes INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id),
                FOREIGN KEY(raw_entry_id) REFERENCES raw_entries(id)
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS training_exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                exercise_name TEXT NOT NULL,
                muscle_group TEXT,
                exercise_order INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES training_sessions(id)
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS training_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exercise_id INTEGER NOT NULL,
                set_order INTEGER NOT NULL,
                reps INTEGER,
                weight_kg REAL,
                duration_sec INTEGER,
                distance_m REAL,
                completed INTEGER DEFAULT 1,
                rpe REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(exercise_id) REFERENCES training_exercises(id)
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS nutrition_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                raw_entry_id INTEGER,
                meal_date DATE NOT NULL,
                meal_time TIMESTAMP,
                meal_type TEXT,
                notes TEXT,
                total_calories REAL DEFAULT 0,
                total_protein REAL DEFAULT 0,
                total_fat REAL DEFAULT 0,
                total_carbs REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id),
                FOREIGN KEY(raw_entry_id) REFERENCES raw_entries(id)
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS nutrition_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                grams REAL,
                calories REAL,
                protein REAL,
                fat REAL,
                carbs REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(entry_id) REFERENCES nutrition_entries(id)
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_context (
                user_id INTEGER PRIMARY KEY,
                context_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS ai_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                raw_entry_id INTEGER,
                entry_type TEXT NOT NULL,
                model_primary TEXT,
                model_validator TEXT,
                primary_output_json TEXT,
                validator_output_json TEXT,
                final_output_json TEXT,
                validation_status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id),
                FOREIGN KEY(raw_entry_id) REFERENCES raw_entries(id)
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_states (
                user_id INTEGER PRIMARY KEY,
                state TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS bot_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_text TEXT,
                log_type TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()

        debug_logger.send_log("База данных инициализирована", "database")

    def save_bot_log(self, log_text: str, log_type: str = "info"):
        try:
            conn = self.get_conn()
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO bot_logs (log_text, log_type)
                VALUES (?, ?)
            ''', (log_text, log_type))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def add_user(self, user_id: int, username: str = None, first_name: str = None):
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
        ''', (user_id, username, first_name))
        conn.commit()
        conn.close()

    def set_user_state(self, user_id: int, state: Optional[str]):
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO user_states (user_id, state, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                state = excluded.state,
                updated_at = CURRENT_TIMESTAMP
        ''', (user_id, state))
        conn.commit()
        conn.close()

    def get_user_state(self, user_id: int) -> Optional[str]:
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute('SELECT state FROM user_states WHERE user_id = ?', (user_id,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None

    def clear_user_state(self, user_id: int):
        self.set_user_state(user_id, None)

    def save_raw_entry(self, user_id: int, entry_type: str, raw_text: str) -> int:
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO raw_entries (user_id, entry_type, raw_text)
            VALUES (?, ?, ?)
        ''', (user_id, entry_type, raw_text))
        raw_id = cur.lastrowid
        conn.commit()
        conn.close()
        return raw_id

    def save_context(self, user_id: int, context: dict):
        conn = self.get_conn()
        cur = conn.cursor()
        context_json = json.dumps(context, ensure_ascii=False, default=str)
        cur.execute('''
            INSERT INTO user_context (user_id, context_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                context_json = excluded.context_json,
                updated_at = CURRENT_TIMESTAMP
        ''', (user_id, context_json))
        conn.commit()
        conn.close()

    def load_context(self, user_id: int) -> Optional[dict]:
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute('SELECT context_json FROM user_context WHERE user_id = ?', (user_id,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return json.loads(row[0])
        return None

    def save_training_structured(self, user_id: int, raw_entry_id: int, data: Dict[str, Any]) -> int:
        conn = self.get_conn()
        cur = conn.cursor()

        session_date = data.get("session_date") or today_iso()
        notes = data.get("notes")
        perceived_effort = to_int_or_none(data.get("perceived_effort"))
        duration_minutes = to_int_or_none(data.get("duration_minutes"))

        cur.execute('''
            INSERT INTO training_sessions (
                user_id, raw_entry_id, session_date, notes, perceived_effort, duration_minutes
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, raw_entry_id, session_date, notes, perceived_effort, duration_minutes))
        session_id = cur.lastrowid

        for i, ex in enumerate(data.get("exercises", []), start=1):
            exercise_name = ex.get("exercise_name") or f"Упражнение {i}"
            muscle_group = ex.get("muscle_group")

            cur.execute('''
                INSERT INTO training_exercises (session_id, exercise_name, muscle_group, exercise_order)
                VALUES (?, ?, ?, ?)
            ''', (session_id, exercise_name, muscle_group, i))
            exercise_id = cur.lastrowid

            for j, s in enumerate(ex.get("sets", []), start=1):
                cur.execute('''
                    INSERT INTO training_sets (
                        exercise_id, set_order, reps, weight_kg, duration_sec, distance_m, completed, rpe
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    exercise_id,
                    j,
                    to_int_or_none(s.get("reps")),
                    to_float_or_none(s.get("weight_kg")),
                    to_int_or_none(s.get("duration_sec")),
                    to_float_or_none(s.get("distance_m")),
                    1 if s.get("completed", True) else 0,
                    to_float_or_none(s.get("rpe"))
                ))

        conn.commit()
        conn.close()
        return session_id

    def save_nutrition_structured(self, user_id: int, raw_entry_id: int, data: Dict[str, Any]) -> int:
        conn = self.get_conn()
        cur = conn.cursor()

        meal_date = data.get("meal_date") or today_iso()
        meal_type = data.get("meal_type")
        notes = data.get("notes")

        total_calories = float(data.get("total_calories", 0) or 0)
        total_protein = float(data.get("total_protein", 0) or 0)
        total_fat = float(data.get("total_fat", 0) or 0)
        total_carbs = float(data.get("total_carbs", 0) or 0)

        cur.execute('''
            INSERT INTO nutrition_entries (
                user_id, raw_entry_id, meal_date, meal_type, notes,
                total_calories, total_protein, total_fat, total_carbs
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id, raw_entry_id, meal_date, meal_type, notes,
            total_calories, total_protein, total_fat, total_carbs
        ))
        entry_id = cur.lastrowid

        for item in data.get("items", []):
            item_name = item.get("item_name") or "Продукт"
            cur.execute('''
                INSERT INTO nutrition_items (
                    entry_id, item_name, grams, calories, protein, fat, carbs
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                entry_id,
                item_name,
                to_float_or_none(item.get("grams")),
                to_float_or_none(item.get("calories")),
                to_float_or_none(item.get("protein")),
                to_float_or_none(item.get("fat")),
                to_float_or_none(item.get("carbs"))
            ))

        conn.commit()
        conn.close()
        return entry_id

    def save_ai_audit(
        self,
        user_id: int,
        raw_entry_id: int,
        entry_type: str,
        model_primary: str,
        model_validator: str,
        primary_output: Dict[str, Any],
        validator_output: Dict[str, Any],
        final_output: Dict[str, Any],
        validation_status: str
    ):
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO ai_audits (
                user_id, raw_entry_id, entry_type, model_primary, model_validator,
                primary_output_json, validator_output_json, final_output_json, validation_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id,
            raw_entry_id,
            entry_type,
            model_primary,
            model_validator,
            json.dumps(primary_output, ensure_ascii=False),
            json.dumps(validator_output, ensure_ascii=False),
            json.dumps(final_output, ensure_ascii=False),
            validation_status
        ))
        conn.commit()
        conn.close()

    def get_recent_trainings_context(self, user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        conn = self.get_conn()
        cur = conn.cursor()

        cur.execute('''
            SELECT id, session_date, notes, perceived_effort, duration_minutes
            FROM training_sessions
            WHERE user_id = ?
            ORDER BY session_date DESC, id DESC
            LIMIT ?
        ''', (user_id, limit))

        sessions = []
        for session_row in cur.fetchall():
            session_id, session_date, notes, effort, duration = session_row

            cur.execute('''
                SELECT id, exercise_name, muscle_group, exercise_order
                FROM training_exercises
                WHERE session_id = ?
                ORDER BY exercise_order ASC, id ASC
            ''', (session_id,))
            ex_rows = cur.fetchall()

            exercises = []
            for ex_id, ex_name, muscle_group, ex_order in ex_rows:
                cur.execute('''
                    SELECT set_order, reps, weight_kg, duration_sec, distance_m, completed, rpe
                    FROM training_sets
                    WHERE exercise_id = ?
                    ORDER BY set_order ASC
                ''', (ex_id,))
                sets = [
                    {
                        "set_order": r[0],
                        "reps": r[1],
                        "weight_kg": r[2],
                        "duration_sec": r[3],
                        "distance_m": r[4],
                        "completed": bool(r[5]),
                        "rpe": r[6]
                    }
                    for r in cur.fetchall()
                ]
                exercises.append({
                    "exercise_name": ex_name,
                    "muscle_group": muscle_group,
                    "exercise_order": ex_order,
                    "sets": sets
                })

            sessions.append({
                "session_id": session_id,
                "session_date": session_date,
                "notes": notes,
                "perceived_effort": effort,
                "duration_minutes": duration,
                "exercises": exercises
            })

        conn.close()
        return sessions

    def get_recent_nutrition_context(self, user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        conn = self.get_conn()
        cur = conn.cursor()

        cur.execute('''
            SELECT id, meal_date, meal_type, notes, total_calories, total_protein, total_fat, total_carbs
            FROM nutrition_entries
            WHERE user_id = ?
            ORDER BY meal_date DESC, id DESC
            LIMIT ?
        ''', (user_id, limit))

        entries = []
        for row in cur.fetchall():
            entry_id, meal_date, meal_type, notes, calories, protein, fat, carbs = row

            cur.execute('''
                SELECT item_name, grams, calories, protein, fat, carbs
                FROM nutrition_items
                WHERE entry_id = ?
                ORDER BY id ASC
            ''', (entry_id,))
            items = [
                {
                    "item_name": r[0],
                    "grams": r[1],
                    "calories": r[2],
                    "protein": r[3],
                    "fat": r[4],
                    "carbs": r[5]
                }
                for r in cur.fetchall()
            ]

            entries.append({
                "entry_id": entry_id,
                "meal_date": meal_date,
                "meal_type": meal_type,
                "notes": notes,
                "total_calories": calories,
                "total_protein": protein,
                "total_fat": fat,
                "total_carbs": carbs,
                "items": items
            })

        conn.close()
        return entries

    def get_daily_training_analytics(self, user_id: int, days: int = 30) -> List[Dict[str, Any]]:
        conn = self.get_conn()
        cur = conn.cursor()

        cur.execute('''
            SELECT
                ts.session_date,
                COUNT(DISTINCT ts.id) AS sessions_count,
                COUNT(DISTINCT te.id) AS exercises_count,
                COUNT(tset.id) AS sets_count,
                ROUND(SUM(COALESCE(tset.weight_kg, 0) * COALESCE(tset.reps, 0)), 2) AS total_volume
            FROM training_sessions ts
            LEFT JOIN training_exercises te ON te.session_id = ts.id
            LEFT JOIN training_sets tset ON tset.exercise_id = te.id
            WHERE ts.user_id = ?
              AND ts.session_date >= date('now', ?)
            GROUP BY ts.session_date
            ORDER BY ts.session_date DESC
        ''', (user_id, f'-{days} day'))

        rows = cur.fetchall()
        conn.close()

        return [
            {
                "date": r[0],
                "sessions_count": r[1],
                "exercises_count": r[2],
                "sets_count": r[3],
                "total_volume": r[4] or 0
            }
            for r in rows
        ]

    def get_daily_nutrition_analytics(self, user_id: int, days: int = 30) -> List[Dict[str, Any]]:
        conn = self.get_conn()
        cur = conn.cursor()

        cur.execute('''
            SELECT
                meal_date,
                COUNT(*) AS meals_count,
                ROUND(SUM(total_calories), 2) AS calories,
                ROUND(SUM(total_protein), 2) AS protein,
                ROUND(SUM(total_fat), 2) AS fat,
                ROUND(SUM(total_carbs), 2) AS carbs
            FROM nutrition_entries
            WHERE user_id = ?
              AND meal_date >= date('now', ?)
            GROUP BY meal_date
            ORDER BY meal_date DESC
        ''', (user_id, f'-{days} day'))

        rows = cur.fetchall()
        conn.close()

        return [
            {
                "date": r[0],
                "meals_count": r[1],
                "calories": r[2] or 0,
                "protein": r[3] or 0,
                "fat": r[4] or 0,
                "carbs": r[5] or 0
            }
            for r in rows
        ]

    def get_today_training_analytics(self, user_id: int) -> Optional[Dict[str, Any]]:
        data = self.get_daily_training_analytics(user_id, 1)
        return data[0] if data else None

    def get_today_nutrition_analytics(self, user_id: int) -> Optional[Dict[str, Any]]:
        data = self.get_daily_nutrition_analytics(user_id, 1)
        return data[0] if data else None


# ==================== AI КЛИЕНТЫ ====================
class AIClients:
    def __init__(self):
        self.deepseek_api_key = os.getenv('DEEPSEEK_API_KEY2') or os.getenv('OPENROUTER_API_KEY')
        self.deepseek_url = "https://openrouter.ai/api/v1/chat/completions"

        self.yandex_api_key = os.getenv('YANDEX_API_KEY')
        self.yandex_folder_id = os.getenv('YANDEX_FOLDER_ID')
        self.yandex_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

        self.use_deepseek = bool(self.deepseek_api_key)
        self.use_yandex = bool(self.yandex_api_key and self.yandex_folder_id)

        debug_logger.send_log(
            f"API инициализированы | DeepSeek/OpenRouter: {self.use_deepseek} | YandexGPT: {self.use_yandex}",
            "api"
        )

    def call_api(self, user_message: str, system_prompt: str, temperature: float = 0.2, max_tokens: int = 1200) -> str:
        if self.use_deepseek:
            try:
                return self.call_deepseek(user_message, system_prompt, temperature, max_tokens)
            except Exception as e:
                debug_logger.log_error(e, "DeepSeek/OpenRouter call")
                if self.use_yandex:
                    debug_logger.send_log("Переход на YandexGPT как fallback", "warning")
                    return self.call_yandexgpt(user_message, system_prompt, temperature, max_tokens)
                raise

        if self.use_yandex:
            return self.call_yandexgpt(user_message, system_prompt, temperature, max_tokens)

        raise RuntimeError("Нет доступных API ключей для моделей")

    def call_json_api(self, user_message: str, system_prompt: str, temperature: float = 0.1, max_tokens: int = 1400) -> Dict[str, Any]:
        text = self.call_api(user_message, system_prompt, temperature=temperature, max_tokens=max_tokens)
        return safe_json_loads(text)

    def call_deepseek(self, user_message: str, system_prompt: str, temperature: float = 0.2, max_tokens: int = 1200) -> str:
        headers = {
            "Authorization": f"Bearer {self.deepseek_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://t.me/TrainingZoneBot",
            "X-Title": "Training Zone Coach"
        }

        payload = {
            "model": "deepseek/deepseek-chat:free",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        response = requests.post(self.deepseek_url, json=payload, headers=headers, timeout=60)
        if response.status_code != 200:
            raise RuntimeError(f"DeepSeek/OpenRouter {response.status_code}: {response.text[:500]}")
        result = response.json()
        return result["choices"][0]["message"]["content"]

    def call_yandexgpt(self, user_message: str, system_prompt: str, temperature: float = 0.2, max_tokens: int = 1200) -> str:
        headers = {
            "Authorization": f"Api-Key {self.yandex_api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "modelUri": f"gpt://{self.yandex_folder_id}/yandexgpt-lite",
            "completionOptions": {
                "stream": False,
                "temperature": temperature,
                "maxTokens": str(max_tokens)
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_message}
            ]
        }

        response = requests.post(self.yandex_url, json=payload, headers=headers, timeout=60)
        if response.status_code != 200:
            raise RuntimeError(f"YandexGPT {response.status_code}: {response.text[:500]}")
        result = response.json()
        return result["result"]["alternatives"][0]["message"]["text"]


# ==================== ПАРСЕР ТРЕНИРОВОК ====================
class TrainingParserService:
    def __init__(self, ai_clients: AIClients, db: Database):
        self.ai = ai_clients
        self.db = db

    def parse_and_validate_training(self, user_id: int, raw_text: str) -> Dict[str, Any]:
        raw_entry_id = self.db.save_raw_entry(user_id, "training", raw_text)

        parse_prompt = f"""
Ты извлекаешь структуру тренировки из текста пользователя.
Верни только JSON. Никаких пояснений, только валидный JSON.

Сегодняшняя дата: {today_iso()}

Формат ответа:
{{
  "session_date": "YYYY-MM-DD",
  "notes": "",
  "perceived_effort": null,
  "duration_minutes": null,
  "exercises": [
    {{
      "exercise_name": "string",
      "muscle_group": "string|null",
      "sets": [
        {{
          "set_order": 1,
          "reps": 5,
          "weight_kg": 100,
          "duration_sec": null,
          "distance_m": null,
          "completed": true,
          "rpe": null
        }}
      ]
    }}
  ]
}}

Правила:
- если дата не указана, ставь сегодняшнюю;
- если написано "100кг 5х5" или "5х5 100кг", создай 5 подходов по 5 повторений с весом 100;
- если только "10,8,6", создай 3 подхода по повторениям 10, 8, 6;
- если вес не указан, ставь null;
- не выдумывай упражнения, которых нет в тексте;
- если информации мало, всё равно верни максимально аккуратную структуру;
- только JSON.
"""

        primary = self.ai.call_json_api(raw_text, parse_prompt)

        validate_prompt = f"""
Ты — валидатор разбора тренировок.
Сравни исходный текст и уже распарсенный JSON.
Нужно проверить, правильно ли извлечены упражнения, подходы, повторы, веса и дата.

Верни только JSON в формате:
{{
  "is_valid": true,
  "issues": [],
  "corrected_data": {{
    "session_date": "YYYY-MM-DD",
    "notes": "",
    "perceived_effort": null,
    "duration_minutes": null,
    "exercises": []
  }},
  "confidence": 0.0
}}

Правила:
- если JSON правильный, верни is_valid=true и corrected_data равным исправленному или исходному варианту;
- если есть ошибки, перечисли их в issues и исправь структуру в corrected_data;
- confidence от 0 до 1;
- только JSON.

Исходный текст:
{raw_text}

Распарсенный JSON:
{json.dumps(primary, ensure_ascii=False)}
"""
        validator = self.ai.call_json_api("Проверь разбор тренировки", validate_prompt)

        is_valid = bool(validator.get("is_valid", False))
        confidence = float(validator.get("confidence", 0) or 0)

        if is_valid and confidence >= 0.70:
            final_data = validator.get("corrected_data") or primary
            status = "approved"
        elif validator.get("corrected_data"):
            final_data = validator["corrected_data"]
            status = "corrected"
        else:
            final_data = primary
            status = "rejected"

        self.db.save_ai_audit(
            user_id=user_id,
            raw_entry_id=raw_entry_id,
            entry_type="training",
            model_primary="parser_model",
            model_validator="validator_model",
            primary_output=primary,
            validator_output=validator,
            final_output=final_data,
            validation_status=status
        )

        if status == "rejected":
            return {
                "ok": False,
                "status": status,
                "raw_entry_id": raw_entry_id,
                "message": "Не смог уверенно разобрать тренировку. Напиши подробнее: упражнение, вес, подходы, повторы."
            }

        session_id = self.db.save_training_structured(user_id, raw_entry_id, final_data)

        return {
            "ok": True,
            "status": status,
            "raw_entry_id": raw_entry_id,
            "session_id": session_id,
            "data": final_data
        }


# ==================== ПАРСЕР ПИТАНИЯ ====================
class NutritionParserService:
    def __init__(self, ai_clients: AIClients, db: Database):
        self.ai = ai_clients
        self.db = db

    def parse_and_validate_nutrition(self, user_id: int, raw_text: str) -> Dict[str, Any]:
        raw_entry_id = self.db.save_raw_entry(user_id, "nutrition", raw_text)

        parse_prompt = f"""
Ты извлекаешь структуру приёма пищи из текста пользователя.
Верни только JSON. Никаких пояснений, только JSON.

Сегодняшняя дата: {today_iso()}

Формат:
{{
  "meal_date": "YYYY-MM-DD",
  "meal_type": "breakfast|lunch|dinner|snack|null",
  "notes": "",
  "total_calories": 0,
  "total_protein": 0,
  "total_fat": 0,
  "total_carbs": 0,
  "items": [
    {{
      "item_name": "string",
      "grams": null,
      "calories": 0,
      "protein": 0,
      "fat": 0,
      "carbs": 0
    }}
  ]
}}

Правила:
- если дата не указана, ставь сегодняшнюю;
- если тип приёма пищи не указан, можно поставить null;
- если точных БЖУ нет, оцени приблизительно и разумно;
- если есть граммы, учитывай их;
- total_* должны быть суммой items;
- только JSON.
"""

        primary = self.ai.call_json_api(raw_text, parse_prompt)

        validate_prompt = f"""
Ты — валидатор разбора питания.
Сравни исходный текст и JSON.
Проверь: продукты, граммы, примерные калории и БЖУ, итоговые суммы.

Верни только JSON в формате:
{{
  "is_valid": true,
  "issues": [],
  "corrected_data": {{
    "meal_date": "YYYY-MM-DD",
    "meal_type": null,
    "notes": "",
    "total_calories": 0,
    "total_protein": 0,
    "total_fat": 0,
    "total_carbs": 0,
    "items": []
  }},
  "confidence": 0.0
}}

Только JSON.

Исходный текст:
{raw_text}

Распарсенный JSON:
{json.dumps(primary, ensure_ascii=False)}
"""
        validator = self.ai.call_json_api("Проверь разбор питания", validate_prompt)

        is_valid = bool(validator.get("is_valid", False))
        confidence = float(validator.get("confidence", 0) or 0)

        if is_valid and confidence >= 0.70:
            final_data = validator.get("corrected_data") or primary
            status = "approved"
        elif validator.get("corrected_data"):
            final_data = validator["corrected_data"]
            status = "corrected"
        else:
            final_data = primary
            status = "rejected"

        self.db.save_ai_audit(
            user_id=user_id,
            raw_entry_id=raw_entry_id,
            entry_type="nutrition",
            model_primary="parser_model",
            model_validator="validator_model",
            primary_output=primary,
            validator_output=validator,
            final_output=final_data,
            validation_status=status
        )

        if status == "rejected":
            return {
                "ok": False,
                "status": status,
                "raw_entry_id": raw_entry_id,
                "message": "Не смог уверенно разобрать питание. Напиши подробнее: продукты, граммы, способ приготовления."
            }

        entry_id = self.db.save_nutrition_structured(user_id, raw_entry_id, final_data)

        return {
            "ok": True,
            "status": status,
            "raw_entry_id": raw_entry_id,
            "entry_id": entry_id,
            "data": final_data
        }


# ==================== АНАЛИТИКА ====================
class AnalyticsService:
    def __init__(self, db: Database):
        self.db = db

    def get_today_report_text(self, user_id: int) -> str:
        t = self.db.get_today_training_analytics(user_id)
        n = self.db.get_today_nutrition_analytics(user_id)

        lines = ["📅 Отчёт за сегодня\n"]

        if t:
            lines.append(
                f"🏋️ Тренировки: {t['sessions_count']}, упражнений: {t['exercises_count']}, "
                f"подходов: {t['sets_count']}, объём: {t['total_volume']}"
            )
        else:
            lines.append("🏋️ Тренировок сегодня нет.")

        if n:
            lines.append(
                f"🍽️ Питание: {n['meals_count']} приёмов, "
                f"{n['calories']} ккал, Б {n['protein']}, Ж {n['fat']}, У {n['carbs']}"
            )
        else:
            lines.append("🍽️ Записей о питании сегодня нет.")

        return "\n".join(lines)

    def get_daily_report_text(self, user_id: int, days: int = 7) -> str:
        training = self.db.get_daily_training_analytics(user_id, days)
        nutrition = self.db.get_daily_nutrition_analytics(user_id, days)

        lines = [f"📊 Отчёт за {days} дн.\n"]

        lines.append("🏋️ Тренировки:")
        if training:
            for row in training:
                lines.append(
                    f"{row['date']}: трен. {row['sessions_count']}, "
                    f"упр. {row['exercises_count']}, подходов {row['sets_count']}, "
                    f"объём {row['total_volume']}"
                )
        else:
            lines.append("Нет данных.")

        lines.append("\n🍽️ Питание:")
        if nutrition:
            for row in nutrition:
                lines.append(
                    f"{row['date']}: приёмов {row['meals_count']}, "
                    f"ккал {row['calories']}, Б {row['protein']}, Ж {row['fat']}, У {row['carbs']}"
                )
        else:
            lines.append("Нет данных.")

        return "\n".join(lines)

    def get_summary_for_ai(self, user_id: int, days: int = 14) -> str:
        training = self.db.get_daily_training_analytics(user_id, days)
        nutrition = self.db.get_daily_nutrition_analytics(user_id, days)

        lines = [f"Аналитика за {days} дней."]

        if training:
            total_sessions = sum(x["sessions_count"] for x in training)
            total_sets = sum(x["sets_count"] for x in training)
            total_volume = sum(x["total_volume"] for x in training)
            lines.append(
                f"Тренировки: {total_sessions} сессий, {total_sets} подходов, суммарный объём {round(total_volume, 2)}."
            )
        else:
            lines.append("Тренировок за период нет.")

        if nutrition:
            total_kcal = sum(x["calories"] for x in nutrition)
            total_protein = sum(x["protein"] for x in nutrition)
            total_fat = sum(x["fat"] for x in nutrition)
            total_carbs = sum(x["carbs"] for x in nutrition)
            days_count = max(len(nutrition), 1)
            lines.append(
                f"Питание: среднее в день — {round(total_kcal / days_count, 1)} ккал, "
                f"Б {round(total_protein / days_count, 1)}, "
                f"Ж {round(total_fat / days_count, 1)}, "
                f"У {round(total_carbs / days_count, 1)}."
            )
        else:
            lines.append("Записей о питании за период нет.")

        return "\n".join(lines)


# ==================== ТРЕНЕР ====================
class TrainingZoneCoach:
    def __init__(self, ai_clients: AIClients, user_id: int, db: Database, analytics: AnalyticsService):
        self.ai_clients = ai_clients
        self.user_id = user_id
        self.db = db
        self.analytics = analytics

        self.context = self.db.load_context(user_id)
        if not self.context:
            self.context = {
                "trainer_personality": (
                    "Ты — жёсткий, прямолинейный, но справедливый тренер в стиле Пола Уэйда. "
                    "Ты за дисциплину, прогрессию нагрузки, базовые движения, восстановление и разумное питание. "
                    "Ты говоришь коротко, по делу, без воды и без магического мышления. "
                    "Если спортсмен ленится — указывай на это. "
                    "Если делает дело — признавай усилия. "
                    "Ответ 3-6 предложений, конкретно."
                ),
                "user_stats": {
                    "start_date": datetime.now().isoformat(),
                    "notes": []
                }
            }
            self.db.save_context(user_id, self.context)

    def _get_full_context(self) -> str:
        trainings = self.db.get_recent_trainings_context(self.user_id, 5)
        nutrition = self.db.get_recent_nutrition_context(self.user_id, 5)
        analytics_text = self.analytics.get_summary_for_ai(self.user_id, 14)

        lines = [self.context["trainer_personality"], "", "КОНТЕКСТ СПОРТСМЕНА:", analytics_text]

        if trainings:
            lines.append("\nПОСЛЕДНИЕ ТРЕНИРОВКИ:")
            for session in trainings:
                lines.append(f"- {session['session_date']}:")
                for ex in session["exercises"][:5]:
                    sets_desc = []
                    for s in ex["sets"][:6]:
                        reps = s["reps"]
                        w = s["weight_kg"]
                        if reps is not None and w is not None:
                            sets_desc.append(f"{reps}x{w}кг")
                        elif reps is not None:
                            sets_desc.append(f"{reps} повт.")
                        elif w is not None:
                            sets_desc.append(f"{w}кг")
                    lines.append(f"  • {ex['exercise_name']}: {', '.join(sets_desc[:6])}")

        if nutrition:
            lines.append("\nПОСЛЕДНЕЕ ПИТАНИЕ:")
            for entry in nutrition:
                lines.append(
                    f"- {entry['meal_date']}: {entry['total_calories']} ккал, "
                    f"Б {entry['total_protein']}, Ж {entry['total_fat']}, У {entry['total_carbs']}"
                )

        return "\n".join(lines)

    def evaluate_training(self, structured_data: Dict[str, Any]) -> str:
        system_prompt = self._get_full_context() + """
Только что спортсмен записал новую тренировку.
Оцени её жёстко, но справедливо.
Скажи:
1. что в ней хорошо,
2. что слабое место,
3. один конкретный следующий шаг.
"""
        user_message = f"Новая тренировка спортсмена:\n{json.dumps(structured_data, ensure_ascii=False)}"
        return self.ai_clients.call_api(user_message, system_prompt, temperature=0.6, max_tokens=700)

    def evaluate_nutrition(self, structured_data: Dict[str, Any]) -> str:
        system_prompt = self._get_full_context() + """
Спортсмен записал новый приём пищи.
Оцени питание для силы, восстановления и прогресса.
Скажи:
1. что нормально,
2. чего не хватает или что лишнее,
3. один конкретный совет на следующий приём пищи.
"""
        user_message = f"Новая запись о питании:\n{json.dumps(structured_data, ensure_ascii=False)}"
        return self.ai_clients.call_api(user_message, system_prompt, temperature=0.6, max_tokens=700)

    def chat(self, user_message: str) -> str:
        system_prompt = self._get_full_context() + """
Спортсмен задал вопрос.
Отвечай как опытный тренер, кратко, конкретно, жёстко, но по делу.
Используй его историю тренировок, питания и аналитику.
"""
        return self.ai_clients.call_api(user_message, system_prompt, temperature=0.7, max_tokens=700)


# ==================== TELEGRAM БОТ ====================
class TrainingZoneBot:
    def __init__(self, token: str, admin_id: int):
        self.token = token
        self.admin_id = admin_id

        global debug_logger
        debug_logger = DebugLogger(None, admin_id)

        self.db = Database()
        self.ai_clients = AIClients()
        self.training_parser = TrainingParserService(self.ai_clients, self.db)
        self.nutrition_parser = NutritionParserService(self.ai_clients, self.db)
        self.analytics_service = AnalyticsService(self.db)
        self.coaches = {}

        debug_logger.send_log("Бот инициализирован", "info")

    def get_coach(self, user_id: int) -> TrainingZoneCoach:
        if user_id not in self.coaches:
            self.coaches[user_id] = TrainingZoneCoach(
                self.ai_clients,
                user_id,
                self.db,
                self.analytics_service
            )
        return self.coaches[user_id]

    def get_reply_keyboard(self):
        keyboard = [
            [KeyboardButton("🏋️ Записать тренировку"), KeyboardButton("🍽️ Записать еду")],
            [KeyboardButton("📅 Сегодня"), KeyboardButton("📊 Отчёт за 7 дней")],
            [KeyboardButton("❓ Задать вопрос тренеру")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    def start(self, update: Update, context: CallbackContext):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        self.db.clear_user_state(user.id)

        text = (
            "💪 ТРЕНИРОВОЧНАЯ ЗОНА 💪\n\n"
            "Я могу:\n"
            "• сохранять тренировки в структурированном виде\n"
            "• сохранять питание и считать БЖУ/калории\n"
            "• строить аналитику по дням\n"
            "• отвечать как тренер с учётом твоей истории\n\n"
            "Команды:\n"
            "/training описание\n"
            "/food описание\n"
            "/today\n"
            "/report 7\n"
            "/analytics 30\n"
            "/help\n\n"
            "Или используй кнопки ниже."
        )

        update.message.reply_text(text, reply_markup=self.get_reply_keyboard())
        debug_logger.send_log(f"/start от @{user.username} ({user.id})", "user")

    def help_command(self, update: Update, context: CallbackContext):
        text = (
            "📋 Доступные команды:\n\n"
            "/training [описание] — записать тренировку\n"
            "/food [описание] — записать еду\n"
            "/today — краткий отчёт за сегодня\n"
            "/report [days] — отчёт по дням, например /report 14\n"
            "/analytics [days] — отчёт + совет тренера, например /analytics 30\n\n"
            "Примеры:\n"
            "/training Присед 100кг 5х5, жим 80кг 5х5, подтягивания 10,8,6\n"
            "/food Куриная грудка 200г, гречка 150г, овощи\n\n"
            "Можно также нажимать кнопки и потом просто отправлять текст."
        )
        update.message.reply_text(text, reply_markup=self.get_reply_keyboard())

    def today_command(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        report = self.analytics_service.get_today_report_text(user_id)
        update.message.reply_text(report, reply_markup=self.get_reply_keyboard())

    def report_command(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        days = 7
        if context.args:
            try:
                days = max(1, min(365, int(context.args[0])))
            except Exception:
                pass

        report = self.analytics_service.get_daily_report_text(user_id, days)
        update.message.reply_text(report, reply_markup=self.get_reply_keyboard())

    def analytics_command(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        days = 14
        if context.args:
            try:
                days = max(1, min(365, int(context.args[0])))
            except Exception:
                pass

        report = self.analytics_service.get_daily_report_text(user_id, days)
        coach = self.get_coach(user_id)

        try:
            advice = coach.chat(
                f"Вот аналитика спортсмена за {days} дней:\n{report}\n\n"
                f"Дай краткий вывод о дисциплине, прогрессе и одном главном приоритете."
            )
            final_text = f"{report}\n\n💪 Комментарий тренера:\n{advice}"
        except Exception as e:
            debug_logger.log_error(e, "analytics_command coach advice")
            final_text = report

        update.message.reply_text(final_text, reply_markup=self.get_reply_keyboard())

    def process_training_text(self, update: Update, training_text: str):
        user = update.effective_user
        user_id = user.id

        update.message.reply_text("🏋️ Разбираю тренировку, структурирую и проверяю второй моделью...")

        try:
            result = self.training_parser.parse_and_validate_training(user_id, training_text)

            if not result["ok"]:
                update.message.reply_text(f"⚠️ {result['message']}", reply_markup=self.get_reply_keyboard())
                return

            coach = self.get_coach(user_id)
            coach_response = coach.evaluate_training(result["data"])

            session_date = result["data"].get("session_date", today_iso())
            exercises_count = len(result["data"].get("exercises", []))
            sets_count = sum(len(ex.get("sets", [])) for ex in result["data"].get("exercises", []))

            text = (
                f"✅ Тренировка сохранена\n"
                f"Дата: {session_date}\n"
                f"Упражнений: {exercises_count}\n"
                f"Подходов: {sets_count}\n"
                f"Статус проверки: {result['status']}\n\n"
                f"💪 Тренер:\n{coach_response}"
            )
            update.message.reply_text(text, reply_markup=self.get_reply_keyboard())

            debug_logger.send_log(
                f"Тренировка сохранена для {user_id} | session_id={result.get('session_id')} | status={result['status']}",
                "success"
            )

        except Exception as e:
            debug_logger.log_error(e, "process_training_text")
            update.message.reply_text("⚠️ Ошибка при обработке тренировки.", reply_markup=self.get_reply_keyboard())

    def process_food_text(self, update: Update, food_text: str):
        user = update.effective_user
        user_id = user.id

        update.message.reply_text("🍽️ Разбираю питание, считаю БЖУ и проверяю второй моделью...")

        try:
            result = self.nutrition_parser.parse_and_validate_nutrition(user_id, food_text)

            if not result["ok"]:
                update.message.reply_text(f"⚠️ {result['message']}", reply_markup=self.get_reply_keyboard())
                return

            coach = self.get_coach(user_id)
            coach_response = coach.evaluate_nutrition(result["data"])

            meal_date = result["data"].get("meal_date", today_iso())
            calories = result["data"].get("total_calories", 0)
            protein = result["data"].get("total_protein", 0)
            fat = result["data"].get("total_fat", 0)
            carbs = result["data"].get("total_carbs", 0)

            text = (
                f"✅ Питание сохранено\n"
                f"Дата: {meal_date}\n"
                f"Ккал: {calories}\n"
                f"Б: {protein} | Ж: {fat} | У: {carbs}\n"
                f"Статус проверки: {result['status']}\n\n"
                f"💪 Тренер:\n{coach_response}"
            )
            update.message.reply_text(text, reply_markup=self.get_reply_keyboard())

            debug_logger.send_log(
                f"Питание сохранено для {user_id} | entry_id={result.get('entry_id')} | status={result['status']}",
                "success"
            )

        except Exception as e:
            debug_logger.log_error(e, "process_food_text")
            update.message.reply_text("⚠️ Ошибка при обработке питания.", reply_markup=self.get_reply_keyboard())

    def training_command(self, update: Update, context: CallbackContext):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)

        if not context.args:
            self.db.set_user_state(user.id, "awaiting_training")
            update.message.reply_text(
                "✍️ Пришли текстом тренировку.\n"
                "Пример:\n"
                "Присед 100кг 5х5, жим 80кг 5х5, подтягивания 10,8,6",
                reply_markup=self.get_reply_keyboard()
            )
            return

        training_text = " ".join(context.args)
        self.process_training_text(update, training_text)

    def food_command(self, update: Update, context: CallbackContext):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)

        if not context.args:
            self.db.set_user_state(user.id, "awaiting_food")
            update.message.reply_text(
                "✍️ Пришли текстом, что ты съел.\n"
                "Пример:\n"
                "Куриная грудка 200г, гречка 150г, овощи",
                reply_markup=self.get_reply_keyboard()
            )
            return

        food_text = " ".join(context.args)
        self.process_food_text(update, food_text)

    def handle_message(self, update: Update, context: CallbackContext):
        user = update.effective_user
        user_id = user.id
        text = (update.message.text or "").strip()

        self.db.add_user(user_id, user.username, user.first_name)

        debug_logger.send_log(f"Сообщение от @{user.username} ({user_id}): {text[:300]}", "user")

        if text == "🏋️ Записать тренировку":
            self.db.set_user_state(user_id, "awaiting_training")
            update.message.reply_text(
                "✍️ Напиши тренировку текстом.\n"
                "Пример:\n"
                "Присед 100кг 5х5, жим 80кг 5х5, подтягивания 10,8,6",
                reply_markup=self.get_reply_keyboard()
            )
            return

        if text == "🍽️ Записать еду":
            self.db.set_user_state(user_id, "awaiting_food")
            update.message.reply_text(
                "✍️ Напиши, что съел.\n"
                "Пример:\n"
                "Омлет из 3 яиц, овсянка 80г, банан",
                reply_markup=self.get_reply_keyboard()
            )
            return

        if text == "📅 Сегодня":
            report = self.analytics_service.get_today_report_text(user_id)
            update.message.reply_text(report, reply_markup=self.get_reply_keyboard())
            return

        if text == "📊 Отчёт за 7 дней":
            report = self.analytics_service.get_daily_report_text(user_id, 7)
            update.message.reply_text(report, reply_markup=self.get_reply_keyboard())
            return

        if text == "❓ Задать вопрос тренеру":
            self.db.set_user_state(user_id, "awaiting_question")
            update.message.reply_text(
                "💬 Задай вопрос.\n"
                "Например:\n"
                "• Что мне съесть после тренировки?\n"
                "• Почему нет прогресса в жиме?\n"
                "• Как улучшить восстановление?",
                reply_markup=self.get_reply_keyboard()
            )
            return

        state = self.db.get_user_state(user_id)

        if state == "awaiting_training":
            self.db.clear_user_state(user_id)
            self.process_training_text(update, text)
            return

        if state == "awaiting_food":
            self.db.clear_user_state(user_id)
            self.process_food_text(update, text)
            return

        if state == "awaiting_question":
            self.db.clear_user_state(user_id)

        try:
            self.db.save_raw_entry(user_id, "chat", text)
        except Exception:
            pass

        update.message.reply_text("🤔 Думаю...", reply_markup=self.get_reply_keyboard())

        try:
            coach = self.get_coach(user_id)
            response = coach.chat(text)
            update.message.reply_text(f"💪 Тренер:\n{response}", reply_markup=self.get_reply_keyboard())
        except Exception as e:
            debug_logger.log_error(e, "handle_message coach.chat")
            update.message.reply_text("⚠️ Ошибка при обработке сообщения.", reply_markup=self.get_reply_keyboard())

    def run(self):
        updater = Updater(self.token, use_context=True)
        debug_logger.bot = updater.bot

        try:
            updater.bot.send_message(
                chat_id=self.admin_id,
                text="✅ Бот Тренировочная зона запущен.\nЛоги и ошибки будут приходить сюда."
            )
            debug_logger.send_log("Подключение к Telegram API успешно", "success")
        except Exception as e:
            print(f"Не удалось отправить стартовое сообщение админу: {e}")

        dp = updater.dispatcher

        dp.add_handler(CommandHandler("start", self.start))
        dp.add_handler(CommandHandler("help", self.help_command))
        dp.add_handler(CommandHandler("training", self.training_command))
        dp.add_handler(CommandHandler("food", self.food_command))
        dp.add_handler(CommandHandler("today", self.today_command))
        dp.add_handler(CommandHandler("report", self.report_command))
        dp.add_handler(CommandHandler("analytics", self.analytics_command))

        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))

        debug_logger.send_log("Все обработчики зарегистрированы. Запускаю polling...", "info")
        print("✅ Бот запущен")
        updater.start_polling()
        updater.idle()


# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

    if not TELEGRAM_TOKEN:
        print("❌ Не найден TELEGRAM_BOT_TOKEN")
        TELEGRAM_TOKEN = input("Введите токен Telegram бота: ").strip()

    print("=" * 60)
    print("🚀 ЗАПУСК БОТА ТРЕНИРОВОЧНАЯ ЗОНА")
    print("=" * 60)
    print(f"ADMIN_USER_ID: {ADMIN_USER_ID}")
    print("=" * 60)

    bot = TrainingZoneBot(TELEGRAM_TOKEN, ADMIN_USER_ID)
    bot.run()
