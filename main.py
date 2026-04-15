import json
import requests
import time
from datetime import datetime
import os

# Для Telegram бота
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# Инициализация клиентов API
class AIClients:
    def __init__(self):
        # DeepSeek API (бесплатный через OpenRouter)
        self.deepseek_api_key = os.getenv('DEEPSEEK_API_KEY2')
        self.deepseek_url = "https://openrouter.ai/api/v1/chat/completions"

        # YandexGPT API
        self.yandex_api_key = os.getenv('YANDEX_API_KEY')
        self.yandex_folder_id = os.getenv('YANDEX_FOLDER_ID')
        self.yandex_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def check_apis(self):
        available_apis = []
        if self.deepseek_api_key:
            available_apis.append("DeepSeek")
        if self.yandex_folder_id and self.yandex_api_key:
            available_apis.append("YandexGPT")
        return available_apis

class TrainingZoneCoach:
    def __init__(self, ai_clients, user_id):
        self.ai_clients = ai_clients
        self.user_id = user_id
        self.context_file = f"training_context_{user_id}.json"
        
        # Загружаем или создаем контекст пользователя
        self.context = self.load_context()
        
        if not self.context:
            self.context = {
                "trainer_personality": """Ты - тренер из 'Тренировочной зоны' в стиле Пола Уэйда. 
                Ты строгий, прямолинейный, мотивирующий тренер, который проповедует силовой тренинг без понтов.
                Ты не веришь в волшебные таблетки, только в тяжелую работу, дисциплину и правильное питание.
                Твои ответы должны быть краткими, жесткими, но справедливыми. Ты ругаешь за лень, хвалишь за настоящие усилия.
                Используй сленг: 'железо', 'база', 'сталь', 'пот', 'дисциплина', 'зона комфорта'.""",
                
                "training_log": [],
                "nutrition_log": [],
                "user_stats": {
                    "last_training": None,
                    "current_mood": None,
                    "progress_notes": []
                }
            }
        
        self.conversation_history = []
    
    def load_context(self):
        """Загрузить контекст пользователя"""
        try:
            with open(self.context_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return None
    
    def save_context(self):
        """Сохранить контекст пользователя"""
        with open(self.context_file, 'w', encoding='utf-8') as f:
            json.dump(self.context, f, ensure_ascii=False, indent=2)
    
    def call_api(self, user_message, system_prompt):
        """Вызов API"""
        if self.ai_clients.deepseek_api_key:
            return self.call_deepseek(user_message, system_prompt)
        elif self.ai_clients.yandex_api_key:
            return self.call_yandexgpt(user_message, system_prompt)
        else:
            return "❌ Нет доступных API для работы тренера!"
    
    def call_deepseek(self, user_message, system_prompt):
        """Вызов DeepSeek через OpenRouter"""
        headers = {
            "Authorization": f"Bearer {self.ai_clients.deepseek_api_key}",
            "Content-Type": "application/json"
        }
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        payload = {
            "model": "deepseek/deepseek-chat:free",
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": 500
        }
        
        try:
            response = requests.post(self.ai_clients.deepseek_url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            else:
                return f"Ошибка API: {response.status_code}"
        except Exception as e:
            return f"Ошибка: {str(e)}"
    
    def call_yandexgpt(self, user_message, system_prompt):
        """Вызов YandexGPT"""
        headers = {
            "Authorization": f"Api-Key {self.ai_clients.yandex_api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "modelUri": f"gpt://{self.ai_clients.yandex_folder_id}/yandexgpt-lite",
            "completionOptions": {
                "stream": False,
                "temperature": 0.7,
                "maxTokens": 500
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_message}
            ]
        }
        
        try:
            response = requests.post(self.ai_clients.yandex_url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()['result']['alternatives'][0]['message']['text']
            else:
                return f"Ошибка API: {response.status_code}"
        except Exception as e:
            return f"Ошибка: {str(e)}"
    
    def process_training(self, training_text):
        """Обработка записи о тренировке"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        training_entry = {
            "timestamp": timestamp,
            "text": training_text
        }
        self.context["training_log"].append(training_entry)
        self.context["user_stats"]["last_training"] = timestamp
        
        self.save_context()
        
        system_prompt = self.context["trainer_personality"] + f"""
        
        Контекст о спортсмене:
        - Последняя тренировка: {training_text}
        - Всего тренировок: {len(self.context['training_log'])}
        - История питания: {len(self.context['nutrition_log'])} записей
        
        Ты видишь запись о тренировке. Оцени ее жестко, но справедливо. 
        Дай совет, что улучшить, похвали если есть прогресс или отчитай за слабость.
        Спроси о самочувствии после тренировки.
        Ответ должен быть кратким (2-3 предложения).
        """
        
        user_message = f"Моя тренировка: {training_text}"
        
        response = self.call_api(user_message, system_prompt)
        return response
    
    def process_nutrition(self, food_text):
        """Обработка записи о еде"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        nutrition_entry = {
            "timestamp": timestamp,
            "text": food_text
        }
        self.context["nutrition_log"].append(nutrition_entry)
        self.save_context()
        
        system_prompt = self.context["trainer_personality"] + f"""
        
        Спортсмен записал, что съел: {food_text}
        История питания (последние 5 записей): {json.dumps(self.context['nutrition_log'][-5:], ensure_ascii=False)}
        
        Оцени его питание с точки зрения силового тренинга.
        Если еда правильная - похвали. Если нет - отчитай и объясни почему.
        Дай короткий совет по питанию для роста силы.
        Ответ должен быть кратким (2-3 предложения).
        """
        
        user_message = f"Я съел: {food_text}"
        
        response = self.call_api(user_message, system_prompt)
        return response
    
    def ask_about_food(self):
        """Спросить совет по еде"""
        system_prompt = self.context["trainer_personality"] + f"""
        
        История питания спортсмена (последние записи):
        {json.dumps(self.context['nutrition_log'][-5:], ensure_ascii=False, indent=2) if self.context['nutrition_log'] else 'Нет записей о питании'}
        
        Спортсмен спрашивает совет по питанию. Дай конкретные рекомендации что можно поесть сейчас,
        учитывая его тренировки и предыдущие записи о еде. Будь практичным и жестким.
        Предложи 2-3 конкретных варианта для силового тренинга.
        Ответ должен быть кратким (3-4 предложения).
        """
        
        response = self.call_api("Что мне сейчас съесть для восстановления и роста силы?", system_prompt)
        return response
    
    def check_status(self):
        """Проверить статус и самочувствие"""
        system_prompt = self.context["trainer_personality"] + f"""
        
        Статистика спортсмена:
        - Всего тренировок: {len(self.context['training_log'])}
        - Последняя тренировка: {self.context['training_log'][-1]['text'] if self.context['training_log'] else 'Нет данных'}
        - Последний прием пищи: {self.context['nutrition_log'][-1]['text'] if self.context['nutrition_log'] else 'Нет данных'}
        
        Дай короткую мотивирующую речь в стиле 'Тренировочной зоны'.
        Спроси о самочувствии и настроении.
        Ответ должен быть кратким (3-4 предложения).
        """
        
        response = self.call_api("Расскажи о моем прогрессе и спроси о самочувствии", system_prompt)
        return response
    
    def get_stats(self):
        """Получить статистику пользователя"""
        stats = f"""
📊 *Твоя статистика в Тренировочной зоне*

🏋️ *Тренировки:* {len(self.context['training_log'])}
🍽️ *Записи о еде:* {len(self.context['nutrition_log'])}

📅 *Последняя тренировка:* 
{self.context['training_log'][-1]['text'][:100] if self.context['training_log'] else 'Нет данных'}

💪 *Прогресс:* 
{self.context['user_stats']['progress_notes'][-1] if self.context['user_stats']['progress_notes'] else 'Только начинаешь путь'}
        """
        return stats

# Telegram бот
class TrainingZoneBot:
    def __init__(self, token):
        self.token = token
        self.users = {}  # Словарь для хранения тренеров пользователей
        self.ai_clients = AIClients()
        self.ai_clients.check_apis()
    
    def get_coach(self, user_id):
        """Получить или создать тренера для пользователя"""
        if user_id not in self.users:
            self.users[user_id] = TrainingZoneCoach(self.ai_clients, user_id)
        return self.users[user_id]
    
    def start(self, update: Update, context: CallbackContext):
        """Обработчик команды /start"""
        welcome_text = """
💪 *ДОБРО ПОЖАЛОВАТЬ В ТРЕНИРОВОЧНУЮ ЗОНУ!* 💪

Я твой персональный тренер в стиле Пола Уэйда. 
Здесь нет понтов и волшебных таблеток - только железо, пот и дисциплина!

*Команды:*
• `/training` [описание] - записать тренировку
  Пример: `/training Приседал 100кг 5х5, жим 80кг 5х5`

• `/food` [что съел] - записать прием пищи
  Пример: `/food Съел 200г курицы с гречкой`

• `/meal` - получить совет что поесть

• `/status` - узнать свой прогресс

• `/stats` - показать статистику

• `/help` - показать это сообщение

*Также можешь просто писать сообщения, и я отвечу!*

Погнали! Железо не ждет! 💪
        """
        update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)
    
    def handle_training(self, update: Update, context: CallbackContext):
        """Обработчик команды /training"""
        if not context.args:
            update.message.reply_text("❌ Напиши тренировку после команды.\nПример: `/training Присел 100кг 5х5`", parse_mode=ParseMode.MARKDOWN)
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
            update.message.reply_text("❌ Напиши что съел после команды.\nПример: `/food Курица с рисом`", parse_mode=ParseMode.MARKDOWN)
            return
        
        food_text = ' '.join(context.args)
        user_id = update.effective_user.id
        coach = self.get_coach(user_id)
        
        update.message.reply_text("🍽️ Оцениваю твое питание...")
        
        response = coach.process_nutrition(food_text)
        update.message.reply_text(f"💪 *Тренер:* {response}", parse_mode=ParseMode.MARKDOWN)
    
    def handle_meal_advice(self, update: Update, context: CallbackContext):
        """Обработчик команды /meal - совет по еде"""
        user_id = update.effective_user.id
        coach = self.get_coach(user_id)
        
        update.message.reply_text("🍽️ Думаю что тебе лучше съесть...")
        
        response = coach.ask_about_food()
        update.message.reply_text(f"💪 *Тренер:* {response}", parse_mode=ParseMode.MARKDOWN)
    
    def handle_status(self, update: Update, context: CallbackContext):
        """Обработчик команды /status"""
        user_id = update.effective_user.id
        coach = self.get_coach(user_id)
        
        update.message.reply_text("🤔 Анализирую твой прогресс...")
        
        response = coach.check_status()
        update.message.reply_text(f"💪 *Тренер:* {response}", parse_mode=ParseMode.MARKDOWN)
    
    def handle_stats(self, update: Update, context: CallbackContext):
        """Обработчик команды /stats"""
        user_id = update.effective_user.id
        coach = self.get_coach(user_id)
        
        stats = coach.get_stats()
        update.message.reply_text(stats, parse_mode=ParseMode.MARKDOWN)
    
    def handle_message(self, update: Update, context: CallbackContext):
        """Обработчик обычных сообщений"""
        user_message = update.message.text
        user_id = update.effective_user.id
        coach = self.get_coach(user_id)
        
        # Проверяем специальные команды в тексте
        if user_message.lower().startswith('тренировка'):
            training_text = user_message[11:].strip()
            if training_text:
                update.message.reply_text("🏋️ Анализирую тренировку...")
                response = coach.process_training(training_text)
                update.message.reply_text(f"💪 *Тренер:* {response}", parse_mode=ParseMode.MARKDOWN)
                return
        
        elif user_message.lower().startswith('еда'):
            food_text = user_message[3:].strip()
            if food_text:
                update.message.reply_text("🍽️ Оцениваю твое питание...")
                response = coach.process_nutrition(food_text)
                update.message.reply_text(f"💪 *Тренер:* {response}", parse_mode=ParseMode.MARKDOWN)
                return
        
        elif user_message.lower() in ['что поесть', 'что съесть', 'meal']:
            update.message.reply_text("🍽️ Думаю что тебе лучше съесть...")
            response = coach.ask_about_food()
            update.message.reply_text(f"💪 *Тренер:* {response}", parse_mode=ParseMode.MARKDOWN)
            return
        
        elif user_message.lower() in ['статус', 'прогресс', 'status']:
            update.message.reply_text("🤔 Анализирую твой прогресс...")
            response = coach.check_status()
            update.message.reply_text(f"💪 *Тренер:* {response}", parse_mode=ParseMode.MARKDOWN)
            return
        
        # Обычный вопрос тренеру
        update.message.reply_text("🤔 Думаю...")
        
        system_prompt = coach.context["trainer_personality"] + f"""
        Спортсмен задает вопрос. Ответь в своем стиле кратко (2-3 предложения).
        Статистика: {len(coach.context['training_log'])} тренировок, {len(coach.context['nutrition_log'])} записей о еде.
        """
        response = coach.call_api(user_message, system_prompt)
        update.message.reply_text(f"💪 *Тренер:* {response}", parse_mode=ParseMode.MARKDOWN)
    
    def run(self):
        """Запуск бота"""
        updater = Updater(self.token, use_context=True)
        dp = updater.dispatcher
        
        # Регистрируем команды
        dp.add_handler(CommandHandler("start", self.start))
        dp.add_handler(CommandHandler("help", self.start))
        dp.add_handler(CommandHandler("training", self.handle_training))
        dp.add_handler(CommandHandler("food", self.handle_food))
        dp.add_handler(CommandHandler("meal", self.handle_meal_advice))
        dp.add_handler(CommandHandler("status", self.handle_status))
        dp.add_handler(CommandHandler("stats", self.handle_stats))
        
        # Обработчик текстовых сообщений
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))
        
        # Запускаем бота
        updater.start_polling()
        print("✅ Бот запущен! Нажмите Ctrl+C для остановки")
        updater.idle()

# Запуск бота
if __name__ == "__main__":
    # Получаем токен из переменных окружения
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TELEGRAM_TOKEN:
        print("❌ Ошибка: Не найден TELEGRAM_BOT_TOKEN в переменных окружения!")
        print("Установите переменную окружения или введите токен вручную:")
        TELEGRAM_TOKEN = input("Введите токен Telegram бота: ").strip()
    
    # Проверяем API ключи
    ai_clients = AIClients()
    available_apis = ai_clients.check_apis()
    
    if available_apis:
        print(f"✅ Доступные API: {', '.join(available_apis)}")
        print("🚀 Запуск Telegram бота...")
        bot = TrainingZoneBot(TELEGRAM_TOKEN)
        bot.run()
    else:
        print("❌ Нет доступных API! Установите переменные окружения:")
        print("  - DEEPSEEK_API_KEY2")
        print("  - или YANDEX_API_KEY и YANDEX_FOLDER_ID")
