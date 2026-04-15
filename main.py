import json
import requests
import time
from datetime import datetime
import os
import sqlite3
from typing import Dict, Any, Optional

# Telegram imports для версии 13.15
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# ==================== БАЗА ДАННЫХ ====================
class Database:
    def __init__(self, db_name='training_zone.db'):
        self.db_name = db_name
        self.init_db()
    
    def init_db(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()
        
        # Таблица пользователей
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица тренировок
        cur.execute('''
            CREATE TABLE IF NOT EXISTS trainings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                training_text TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        
        # Таблица питания
        cur.execute('''
            CREATE TABLE IF NOT EXISTS nutrition (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                food_text TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        
        # Таблица контекста пользователя
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_context (
                user_id INTEGER PRIMARY KEY,
                context_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ База данных готова")
    
    def add_user(self, user_id: int, username: str = None, first_name: str = None):
        """Добавить пользователя"""
        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()
        cur.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
        ''', (user_id, username, first_name))
        conn.commit()
        conn.close()
    
    def save_training(self, user_id: int, training_text: str):
        """Сохранить тренировку"""
        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO trainings (user_id, training_text)
            VALUES (?, ?)
        ''', (user_id, training_text))
        conn.commit()
        conn.close()
    
    def save_nutrition(self, user_id: int, food_text: str):
        """Сохранить прием пищи"""
        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO nutrition (user_id, food_text)
            VALUES (?, ?)
        ''', (user_id, food_text))
        conn.commit()
        conn.close()
    
    def get_recent_trainings(self, user_id: int, limit: int = 5) -> list:
        """Получить последние тренировки"""
        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()
        cur.execute('''
            SELECT training_text, timestamp FROM trainings 
            WHERE user_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (user_id, limit))
        results = cur.fetchall()
        conn.close()
        return results
    
    def get_recent_nutrition(self, user_id: int, limit: int = 5) -> list:
        """Получить последние записи о еде"""
        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()
        cur.execute('''
            SELECT food_text, timestamp FROM nutrition 
            WHERE user_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (user_id, limit))
        results = cur.fetchall()
        conn.close()
        return results
    
    def save_context(self, user_id: int, context: dict):
        """Сохранить контекст пользователя"""
        conn = sqlite3.connect(self.db_name)
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
        """Загрузить контекст пользователя"""
        conn = sqlite3.connect(self.db_name)
        cur = conn.cursor()
        cur.execute('SELECT context_json FROM user_context WHERE user_id = ?', (user_id,))
        result = cur.fetchone()
        conn.close()
        
        if result and result[0]:
            return json.loads(result[0])
        return None

# ==================== API КЛИЕНТ ====================
class AIClients:
    def __init__(self):
        self.deepseek_api_key = os.getenv('DEEPSEEK_API_KEY2') or os.getenv('OPENROUTER_API_KEY')
        self.deepseek_url = "https://openrouter.ai/api/v1/chat/completions"
        
        self.yandex_api_key = os.getenv('YANDEX_API_KEY')
        self.yandex_folder_id = os.getenv('YANDEX_FOLDER_ID')
        self.yandex_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    
    def call_deepseek(self, user_message: str, system_prompt: str) -> str:
        """Вызов DeepSeek API"""
        headers = {
            "Authorization": f"Bearer {self.deepseek_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://t.me/TrainingZoneBot",
            "X-Title": "Training Zone Coach"
        }
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        payload = {
            "model": "deepseek/deepseek-chat:free",
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": 800
        }
        
        try:
            response = requests.post(self.deepseek_url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            else:
                return f"⚠️ Ошибка API: {response.status_code}"
        except Exception as e:
            return f"⚠️ Ошибка: {str(e)}"
    
    def call_yandexgpt(self, user_message: str, system_prompt: str) -> str:
        """Вызов YandexGPT"""
        headers = {
            "Authorization": f"Api-Key {self.yandex_api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "modelUri": f"gpt://{self.yandex_folder_id}/yandexgpt-lite",
            "completionOptions": {
                "stream": False,
                "temperature": 0.7,
                "maxTokens": 800
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_message}
            ]
        }
        
        try:
            response = requests.post(self.yandex_url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()['result']['alternatives'][0]['message']['text']
            else:
                return f"⚠️ Ошибка API: {response.status_code}"
        except Exception as e:
            return f"⚠️ Ошибка: {str(e)}"

# ==================== ТРЕНЕР ====================
class TrainingZoneCoach:
    def __init__(self, ai_clients: AIClients, user_id: int, db: Database):
        self.ai_clients = ai_clients
        self.user_id = user_id
        self.db = db
        
        # Загружаем контекст из БД
        self.context = self.db.load_context(user_id)
        if not self.context:
            self.context = {
                "trainer_personality": """Ты - тренер из 'Тренировочной зоны' в стиле Пола Уэйда, но и практикующий тренинг 5\3\1.
                Ты строгий, прямолинейный, мотивирующий тренер, который проповедует силовой тренинг без понтов, но и без панибратства, вежливо.
                Ты не веришь в волшебные таблетки, только в тяжелую работу, дисциплину и правильное питание.
                Твои ответы должны быть краткими, жесткими, но справедливыми. Ты ругаешь за лень, хвалишь за настоящие усилия.
                Отвечай как опытный тренер, давай конкретные советы. Ответ должен быть 2-4 предложения.""",
                
                "user_stats": {
                    "start_date": datetime.now().isoformat(),
                    "notes": []
                }
            }
            self.db.save_context(user_id, self.context)
    
    def _get_full_context(self) -> str:
        """Собрать полный контекст для ИИ"""
        trainings = self.db.get_recent_trainings(self.user_id, 5)
        nutrition = self.db.get_recent_nutrition(self.user_id, 5)
        
        context_text = self.context["trainer_personality"] + "\n\n"
        context_text += "📊 ИСТОРИЯ СПОРТСМЕНА:\n"
        
        if trainings:
            context_text += "\n🏋️ ПОСЛЕДНИЕ ТРЕНИРОВКИ:\n"
            for text, ts in trainings:
                context_text += f"  • {text[:100]} ({ts[:16]})\n"
        
        if nutrition:
            context_text += "\n🍽️ ПОСЛЕДНЕЕ ПИТАНИЕ:\n"
            for text, ts in nutrition:
                context_text += f"  • {text[:100]} ({ts[:16]})\n"
        
        context_text += f"\n📈 ВСЕГО ТРЕНИРОВОК: {len(trainings)}\n"
        context_text += f"📈 ВСЕГО ЗАПИСЕЙ О ЕДЕ: {len(nutrition)}\n"
        
        return context_text
    
    def process_training(self, training_text: str) -> str:
        """Обработать тренировку"""
        self.db.save_training(self.user_id, training_text)
        
        system_prompt = self._get_full_context() + """
        Только что спортсмен записал тренировку. Оцени ее жестко, но справедливо.
        Спроси о самочувствии после тренировки.
        """
        
        response = self.ai_clients.call_deepseek(
            f"Моя тренировка: {training_text}",
            system_prompt
        )
        return response
    
    def process_nutrition(self, food_text: str) -> str:
        """Обработать запись о еде"""
        self.db.save_nutrition(self.user_id, food_text)
        
        system_prompt = self._get_full_context() + """
        Спортсмен записал прием пищи. Оцени его питание.
        Дай короткий совет по питанию для роста силы.
        """
        
        response = self.ai_clients.call_deepseek(
            f"Я съел: {food_text}",
            system_prompt
        )
        return response
    
    def chat(self, user_message: str) -> str:
        """Свободный диалог с тренером"""
        system_prompt = self._get_full_context() + """
        Спортсмен задал вопрос или поделился мыслями. 
        Ответь как опытный тренер, используя контекст его тренировок и питания.
        Дай конкретный совет или мотивацию.
        """
        
        response = self.ai_clients.call_deepseek(user_message, system_prompt)
        return response

# ==================== TELEGRAM БОТ ====================
class TrainingZoneBot:
    def __init__(self, token: str):
        self.token = token
        self.db = Database()
        self.ai_clients = AIClients()
        self.coaches = {}  # Кэш тренеров для пользователей
        
    def get_coach(self, user_id: int) -> TrainingZoneCoach:
        """Получить или создать тренера для пользователя"""
        if user_id not in self.coaches:
            self.coaches[user_id] = TrainingZoneCoach(self.ai_clients, user_id, self.db)
            self.db.add_user(user_id)
        return self.coaches[user_id]
    
    def start(self, update: Update, context: CallbackContext):
        """Команда /start"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        # Создаем меню с кнопками
        keyboard = [
            [KeyboardButton("🏋️ Записать тренировку"), KeyboardButton("🍽️ Записать еду")],
            [KeyboardButton("❓ Задать вопрос тренеру")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        welcome_text = """
💪 *ТРЕНИРОВОЧНАЯ ЗОНА* 💪

Привет, боец! Я твой персональный тренер в стиле Пола Уэйда.

*Что ты можешь делать:*
• Нажать кнопку *"Записать тренировку"* - я сохраню и оценю
• Нажать кнопку *"Записать еду"* - я проконтролирую питание
• Нажать кнопку *"Задать вопрос"* - спросить совет, мотивацию, технику

*Важно:* Я помню ВСЕ твои тренировки и приёмы пищи. 
Можешь спросить меня в любой момент: "Что мне сегодня съесть?" или "Как улучшить приседания?"

Погнали! Железо не ждет! 💪
        """
        
        update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    
    def handle_training(self, update: Update, context: CallbackContext):
        """Обработчик команды /training"""
        if not context.args:
            update.message.reply_text(
                "❌ Напиши тренировку после команды.\n"
                "Пример: `/training Присел 100кг 5х5, жим 80кг 5х5`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        training_text = ' '.join(context.args)
        user_id = update.effective_user.id
        coach = self.get_coach(user_id)
        
        update.message.reply_text("🏋️ Анализирую тренировку...")
        
        response = coach.process_training(training_text)
        update.message.reply_text(f"💪 *Тренер:* {response}", parse_mode=ParseMode.MARKDOWN)
    
    def handle_food(self, update: Update, context: CallbackContext):
        """Обработчик команды /food"""
        if not context.args:
            update.message.reply_text(
                "❌ Напиши что съел после команды.\n"
                "Пример: `/food Курица с гречкой и овощами`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        food_text = ' '.join(context.args)
        user_id = update.effective_user.id
        coach = self.get_coach(user_id)
        
        update.message.reply_text("🍽️ Оцениваю твое питание...")
        
        response = coach.process_nutrition(food_text)
        update.message.reply_text(f"💪 *Тренер:* {response}", parse_mode=ParseMode.MARKDOWN)
    
    def handle_message(self, update: Update, context: CallbackContext):
        """Обработчик обычных сообщений"""
        user_message = update.message.text
        user_id = update.effective_user.id
        coach = self.get_coach(user_id)
        
        # Обрабатываем кнопки меню
        if user_message == "🏋️ Записать тренировку":
            update.message.reply_text(
                "✍️ Напиши свою тренировку в формате:\n"
                "`Присел 100кг 5х5, жим лежа 80кг 5х5, подтягивания 10,8,6`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        elif user_message == "🍽️ Записать еду":
            update.message.reply_text(
                "✍️ Напиши что ты съел:\n"
                "`Куриная грудка 200г, гречка 150г, салат овощной`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        elif user_message == "❓ Задать вопрос тренеру":
            update.message.reply_text(
                "💬 Задай свой вопрос. Я отвечу, учитывая твои тренировки и питание.\n\n"
                "Примеры:\n"
                "• Что мне сегодня съесть после тренировки?\n"
                "• Как улучшить технику приседаний?\n"
                "• Почему у меня нет прогресса?\n"
                "• Дай мотивацию позаниматься!"
            )
            return
        
        # Обычное сообщение - отправляем тренеру
        update.message.reply_text("🤔 Думаю...")
        response = coach.chat(user_message)
        update.message.reply_text(f"💪 *Тренер:* {response}", parse_mode=ParseMode.MARKDOWN)
    
    def help_command(self, update: Update, context: CallbackContext):
        """Команда /help"""
        help_text = """
📋 *Доступные команды:*

• `/training [описание]` - записать тренировку
• `/food [описание]` - записать прием пищи

*Или просто напиши сообщение тренеру!*
Я отвечу на любой вопрос, учитывая твою историю тренировок и питания.

*Примеры вопросов:*
• "Что мне сегодня съесть?"
• "Как улучшить жим лежа?"
• "Почему болят колени?"
• "Дай мотивацию!"

Твои данные сохраняются, я помню всё! 💪
        """
        update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    def run(self):
        """Запуск бота"""
        updater = Updater(self.token, use_context=True)
        dp = updater.dispatcher
        
        # Команды
        dp.add_handler(CommandHandler("start", self.start))
        dp.add_handler(CommandHandler("help", self.help_command))
        dp.add_handler(CommandHandler("training", self.handle_training))
        dp.add_handler(CommandHandler("food", self.handle_food))
        
        # Обработчик всех сообщений
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))
        
        print("✅ Бот запущен!")
        updater.start_polling()
        updater.idle()

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TELEGRAM_TOKEN:
        print("❌ Не найден TELEGRAM_BOT_TOKEN!")
        TELEGRAM_TOKEN = input("Введите токен Telegram бота: ").strip()
    
    bot = TrainingZoneBot(TELEGRAM_TOKEN)
    bot.run()
