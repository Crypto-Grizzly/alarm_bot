import asyncio
import logging
import os
from datetime import datetime, time, timedelta, date
from typing import Dict, List, Optional
import sqlite3
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode

# Импортируем конфигурацию
from config import (
    BOT_TOKEN, ALLOWED_USERS, DATABASE_PATH, LOG_LEVEL, LOG_FILE, LOG_FORMAT,
    REMINDER_CHECK_INTERVAL, REPEAT_REMINDER_INTERVAL, MAX_REMINDER_ATTEMPTS,
    REPEAT_CHECK_INTERVAL, REMINDER_TEXT, REPEAT_REMINDER_TEXT, WELCOME_TEXT,
    ENABLE_REPEAT_REMINDERS, ENABLE_FILE_LOGGING, validate_config, create_directories
)

# Регистрируем адаптеры и конвертеры для sqlite3
def adapt_datetime(dt):
    return dt.isoformat()

def adapt_date(d):
    return d.isoformat()

def convert_datetime(s):
    return datetime.fromisoformat(s.decode('utf-8'))

def convert_date(s):
    return date.fromisoformat(s.decode('utf-8'))

sqlite3.register_adapter(datetime, adapt_datetime)
sqlite3.register_adapter(date, adapt_date)
sqlite3.register_converter("TIMESTAMP", convert_datetime)
sqlite3.register_converter("DATE", convert_date)

# Проверяем конфигурацию
config_errors = validate_config()
if config_errors:
    print("❌ Ошибки конфигурации:")
    for error in config_errors:
        print(f"  - {error}")
    print("\n📝 Проверьте файл config.py")
    exit(1)

# Создаём необходимые директории
create_directories()

# Фильтр для скрытия токена в логах
class TokenFilter(logging.Filter):
    def __init__(self, token):
        super().__init__()
        self.token = token

    def filter(self, record):
        if self.token:
            record.msg = record.msg.replace(self.token, "[HIDDEN_TOKEN]")
        return True

# Настройка логирования
log_handlers = [logging.StreamHandler()]
if ENABLE_FILE_LOGGING:
    log_handlers.append(logging.FileHandler(LOG_FILE, encoding='utf-8'))

# Применяем фильтр ко всем обработчикам
token_filter = TokenFilter(BOT_TOKEN)
for handler in log_handlers:
    handler.addFilter(token_filter)

logging.basicConfig(
    handlers=log_handlers,
    format=LOG_FORMAT,
    level=getattr(logging, LOG_LEVEL.upper())
)
logger = logging.getLogger(__name__)

# Устанавливаем уровень логирования для python-telegram-bot
logging.getLogger("telegram").setLevel(logging.WARNING)

class VitaminDatabase:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DATABASE_PATH
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных"""
        with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
            cursor = conn.cursor()
            
            # Таблица витаминов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS vitamins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    reminder_time TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица записей о приёме
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS vitamin_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vitamin_id INTEGER,
                    user_id INTEGER NOT NULL,
                    taken_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'taken',
                    FOREIGN KEY (vitamin_id) REFERENCES vitamins (id)
                )
            ''')
            
            # Таблица активных напоминаний
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS active_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vitamin_id INTEGER,
                    user_id INTEGER NOT NULL,
                    reminder_date DATE,
                    last_reminder TIMESTAMP,
                    attempts INTEGER DEFAULT 0,
                    FOREIGN KEY (vitamin_id) REFERENCES vitamins (id)
                )
            ''')
            
            conn.commit()
    
    def add_vitamin(self, user_id: int, name: str, reminder_time: str) -> bool:
        """Добавление нового витамина"""
        try:
            with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO vitamins (user_id, name, reminder_time) VALUES (?, ?, ?)",
                    (user_id, name, reminder_time)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка добавления витамина: {e}")
            return False
    
    def get_user_vitamins(self, user_id: int) -> List[tuple]:
        """Получение всех витаминов пользователя"""
        with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, name, reminder_time, is_active FROM vitamins WHERE user_id = ? AND is_active = 1",
                (user_id,)
            )
            return cursor.fetchall()
    
    def log_vitamin_intake(self, vitamin_id: int, user_id: int, status: str = 'taken') -> bool:
        """Запись о приёме витамина"""
        try:
            chicago_tz = pytz.timezone("America/Chicago")
            with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO vitamin_logs (vitamin_id, user_id, status) VALUES (?, ?, ?)",
                    (vitamin_id, user_id, status)
                )
                # Удаляем активное напоминание если витамин принят
                if status == 'taken':
                    today = datetime.now(chicago_tz).date()
                    cursor.execute(
                        "DELETE FROM active_reminders WHERE vitamin_id = ? AND user_id = ? AND reminder_date = ?",
                        (vitamin_id, user_id, today)
                    )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка записи приёма витамина: {e}")
            return False
    
    def add_active_reminder(self, vitamin_id: int, user_id: int) -> bool:
        """Добавление активного напоминания"""
        try:
            chicago_tz = pytz.timezone("America/Chicago")
            today = datetime.now(chicago_tz).date()
            with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
                cursor = conn.cursor()
                # Проверяем, есть ли уже напоминание на сегодня
                cursor.execute(
                    "SELECT id FROM active_reminders WHERE vitamin_id = ? AND user_id = ? AND reminder_date = ?",
                    (vitamin_id, user_id, today)
                )
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO active_reminders (vitamin_id, user_id, reminder_date, last_reminder) VALUES (?, ?, ?, ?)",
                        (vitamin_id, user_id, today, datetime.now(chicago_tz))
                    )
                    conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка добавления напоминания: {e}")
            return False
    
    def get_active_reminders(self, user_id: int) -> List[tuple]:
        """Получение активных напоминаний"""
        chicago_tz = pytz.timezone("America/Chicago")
        today = datetime.now(chicago_tz).date()
        with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT ar.id, ar.vitamin_id, v.name, ar.attempts, ar.last_reminder
                FROM active_reminders ar
                JOIN vitamins v ON ar.vitamin_id = v.id
                WHERE ar.user_id = ? AND ar.reminder_date = ?
            ''', (user_id, today))
            return cursor.fetchall()
    
    def update_reminder_attempt(self, reminder_id: int) -> bool:
        """Обновление попытки напоминания"""
        try:
            chicago_tz = pytz.timezone("America/Chicago")
            with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE active_reminders SET attempts = attempts + 1, last_reminder = ? WHERE id = ?",
                    (datetime.now(chicago_tz), reminder_id)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка обновления попытки: {e}")
            return False
    
    def delete_vitamin(self, vitamin_id: int, user_id: int) -> bool:
        """Удаление витамина"""
        try:
            with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE vitamins SET is_active = 0 WHERE id = ? AND user_id = ?",
                    (vitamin_id, user_id)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка удаления витамина: {e}")
            return False

# Глобальная переменная для базы данных
db = VitaminDatabase()

# Словарь для хранения состояний пользователей
user_states = {}

def check_user_access(user_id: int) -> bool:
    """Проверка доступа пользователя"""
    return user_id in ALLOWED_USERS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user_id = update.effective_user.id
    
    if not check_user_access(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этому боту.")
        return
    
    keyboard = [
        [KeyboardButton("💊 Мои витамины"), KeyboardButton("➕ Добавить витамин")],
        [KeyboardButton("📊 Статистика"), KeyboardButton("⚙️ Настройки")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        WELCOME_TEXT,
        reply_markup=reply_markup
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user_id = update.effective_user.id
    
    if not check_user_access(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этому боту.")
        return
    
    text = update.message.text
    
    if text == "💊 Мои витамины":
        await show_vitamins(update, context)
    elif text == "➕ Добавить витамин":
        await add_vitamin_start(update, context)
    elif text == "📊 Статистика":
        await show_statistics(update, context)
    elif text == "⚙️ Настройки":
        await show_settings(update, context)
    elif user_id in user_states:
        await handle_user_input(update, context)
    else:
        await update.message.reply_text("Используйте кнопки меню для навигации.")

async def show_vitamins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список витаминов"""
    user_id = update.effective_user.id
    vitamins = db.get_user_vitamins(user_id)
    
    if not vitamins:
        await update.message.reply_text("📝 У вас пока нет добавленных витаминов.\nИспользуйте кнопку '➕ Добавить витамин'")
        return
    
    text = "💊 Ваши витамины:\n\n"
    keyboard = []
    
    for vitamin_id, name, reminder_time, is_active in vitamins:
        text += f"• {name} - {reminder_time}\n"
        keyboard.append([InlineKeyboardButton(f"✅ Принял {name}", callback_data=f"taken_{vitamin_id}")])
        keyboard.append([InlineKeyboardButton(f"❌ Удалить {name}", callback_data=f"delete_{vitamin_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)

async def add_vitamin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления витамина"""
    user_id = update.effective_user.id
    user_states[user_id] = {"step": "name"}
    
    await update.message.reply_text("💊 Введите название витамина:")

async def handle_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка пользовательского ввода"""
    user_id = update.effective_user.id
    state = user_states.get(user_id, {})
    text = update.message.text
    
    if state.get("step") == "name":
        user_states[user_id]["name"] = text
        user_states[user_id]["step"] = "time"
        await update.message.reply_text(
            f"⏰ Введите время напоминания для '{text}' в формате ЧЧ:ММ\n" +
            "Например: 09:00 или 18:30"
        )
    
    elif state.get("step") == "time":
        try:
            # Проверяем формат времени
            time_obj = datetime.strptime(text, "%H:%M").time()
            
            # Сохраняем витамин в базу
            name = user_states[user_id]["name"]
            if db.add_vitamin(user_id, name, text):
                await update.message.reply_text(f"✅ Витамин '{name}' добавлен!\nНапоминание установлено на {text}")
                
                # Очищаем состояние
                del user_states[user_id]
                
                # Запускаем напоминание
                await schedule_vitamin_reminder(context, user_id, name, time_obj)
            else:
                await update.message.reply_text("❌ Ошибка при добавлении витамина. Попробуйте ещё раз.")
                del user_states[user_id]
        
        except ValueError:
            await update.message.reply_text("❌ Неверный формат времени. Используйте ЧЧ:ММ (например, 09:00)")

async def send_postponed_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправка отложенного напоминания"""
    job = context.job
    user_id = job.data["user_id"]
    vitamin_id = job.data["vitamin_id"]
    vitamin_name = job.data["vitamin_name"]
    reminder_time = job.data["reminder_time"]
    
    # Отправляем напоминание
    keyboard = [
        [
            InlineKeyboardButton("✅ Принял", callback_data=f"taken_{vitamin_id}"),
            InlineKeyboardButton("⏰ Через 5 мин", callback_data=f"postpone_5_{vitamin_id}"),
            InlineKeyboardButton("⏰ Через 10 мин", callback_data=f"postpone_10_{vitamin_id}"),
            InlineKeyboardButton("⏰ Через 20 мин", callback_data=f"postpone_20_{vitamin_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=REMINDER_TEXT.format(vitamin_name=vitamin_name, reminder_time=reminder_time),
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка отправки отложенного напоминания пользователю {user_id}: {e}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на inline кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if not check_user_access(user_id):
        await query.edit_message_text("❌ У вас нет доступа к этому боту.")
        return
    
    if data.startswith("taken_"):
        vitamin_id = int(data.split("_")[1])
        if db.log_vitamin_intake(vitamin_id, user_id, "taken"):
            await query.edit_message_text("✅ Отлично! Приём витамина отмечен.")
        else:
            await query.edit_message_text("❌ Ошибка при записи. Попробуйте ещё раз.")
    
    elif data.startswith("delete_"):
        vitamin_id = int(data.split("_")[1])
        if db.delete_vitamin(vitamin_id, user_id):
            await query.edit_message_text("🗑️ Витамин удалён.")
        else:
            await query.edit_message_text("❌ Ошибка при удалении.")
    
    elif data.startswith("postpone_"):
        # Обработка отложенного напоминания
        parts = data.split("_")
        delay = int(parts[1])  # 5, 10 или 20 минут
        vitamin_id = int(parts[2])
        
        # Получаем информацию о витамине
        vitamins = db.get_user_vitamins(user_id)
        vitamin = next((v for v in vitamins if v[0] == vitamin_id), None)
        if not vitamin:
            await query.edit_message_text("❌ Витамин не найден.")
            return
        
        vitamin_name, reminder_time = vitamin[1], vitamin[2]
        
        # Планируем отложенное напоминание
        context.job_queue.run_once(
            send_postponed_reminder,
            delay * 60,  # Переводим минуты в секунды
            data={
                "user_id": user_id,
                "vitamin_id": vitamin_id,
                "vitamin_name": vitamin_name,
                "reminder_time": reminder_time
            }
        )
        
        await query.edit_message_text(f"⏰ Напоминание отложено на {delay} минут.")
    
    elif data == "toggle_repeat_reminders":
        # Переключаем настройку повторных напоминаний
        if "settings" not in user_states:
            user_states["settings"] = {}
        if user_id not in user_states["settings"]:
            user_states["settings"][user_id] = {"repeat_reminders": ENABLE_REPEAT_REMINDERS}
        
        user_states["settings"][user_id]["repeat_reminders"] = not user_states["settings"][user_id]["repeat_reminders"]
        await query.edit_message_text(
            f"Повторные напоминания теперь {'включены' if user_states['settings'][user_id]['repeat_reminders'] else 'выключены'}."
        )

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику"""
    user_id = update.effective_user.id
    
    try:
        with sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
            cursor = conn.cursor()
            # Получаем статистику: количество принятых витаминов
            cursor.execute(
                "SELECT status, COUNT(*) FROM vitamin_logs WHERE user_id = ? GROUP BY status",
                (user_id,)
            )
            stats = cursor.fetchall()
            
            if not stats:
                await update.message.reply_text("📊 У вас пока нет записей о приёме витаминов.")
                return
            
            # Формируем текст статистики
            taken = next((count for status, count in stats if status == 'taken'), 0)
            
            text = "📊 Ваша статистика:\n\n"
            text += f"✅ Принято витаминов: {taken}\n"
            text += f"Всего записей: {taken}"
            
            await update.message.reply_text(text)
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке статистики. Попробуйте позже.")

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать настройки"""
    user_id = update.effective_user.id
    
    # Предположим, у нас есть глобальный словарь для пользовательских настроек
    if "settings" not in user_states:
        user_states["settings"] = {}
    if user_id not in user_states["settings"]:
        user_states["settings"][user_id] = {"repeat_reminders": ENABLE_REPEAT_REMINDERS}
    
    # Текущие настройки пользователя
    repeat_reminders = user_states["settings"][user_id]["repeat_reminders"]
    
    # Формируем текст настроек
    text = "⚙️ Ваши настройки:\n\n"
    text += f"Повторные напоминания: {'Вкл' if repeat_reminders else 'Выкл'}\n"
    
    # Кнопки для изменения настроек
    keyboard = [
        [
            InlineKeyboardButton(
                "🔄 Выключить повторные напоминания" if repeat_reminders else "🔄 Включить повторные напоминания",
                callback_data="toggle_repeat_reminders"
            )
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, reply_markup=reply_markup)

async def schedule_vitamin_reminder(context: ContextTypes.DEFAULT_TYPE, user_id: int, vitamin_name: str, reminder_time: time):
    """Планирование напоминания"""
    # Эта функция будет вызываться планировщиком
    pass

async def send_vitamin_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправка напоминаний о витаминах"""
    chicago_tz = pytz.timezone("America/Chicago")
    current_time = datetime.now(chicago_tz).time()
    current_time_str = current_time.strftime("%H:%M")
    
    # Проверяем всех пользователей
    for user_id in ALLOWED_USERS:
        vitamins = db.get_user_vitamins(user_id)
        
        for vitamin_id, name, reminder_time, is_active in vitamins:
            if reminder_time == current_time_str:
                # Создаём активное напоминание
                db.add_active_reminder(vitamin_id, user_id)
                
                # Отправляем напоминание
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Принял", callback_data=f"taken_{vitamin_id}"),
                        InlineKeyboardButton("⏰ Через 5 мин", callback_data=f"postpone_5_{vitamin_id}"),
                        InlineKeyboardButton("⏰ Через 10 мин", callback_data=f"postpone_10_{vitamin_id}"),
                        InlineKeyboardButton("⏰ Через 20 мин", callback_data=f"postpone_20_{vitamin_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=REMINDER_TEXT.format(vitamin_name=name, reminder_time=reminder_time),
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки напоминания пользователю {user_id}: {e}")

async def send_repeat_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Отправка повторных напоминаний"""
    chicago_tz = pytz.timezone("America/Chicago")
    for user_id in ALLOWED_USERS:
        # Проверяем настройки пользователя
        if "settings" in user_states and user_id in user_states["settings"]:
            if not user_states["settings"][user_id]["repeat_reminders"]:
                continue  # Пропускаем, если повторные напоминания выключены
        else:
            if not ENABLE_REPEAT_REMINDERS:
                continue  # Пропускаем, если повторные напоминания выключены глобально
        
        reminders = db.get_active_reminders(user_id)
        
        for reminder_id, vitamin_id, vitamin_name, attempts, last_reminder in reminders:
            # Проверяем, прошло ли достаточно времени с последнего напоминания
            last_time = datetime.fromisoformat(last_reminder).replace(tzinfo=chicago_tz)
            current_time = datetime.now(chicago_tz)
            if current_time - last_time >= timedelta(seconds=REPEAT_REMINDER_INTERVAL) and attempts < MAX_REMINDER_ATTEMPTS:
                
                # Обновляем попытку
                db.update_reminder_attempt(reminder_id)
                
                # Отправляем повторное напоминание
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Принял", callback_data=f"taken_{vitamin_id}"),
                        InlineKeyboardButton("⏰ Через 5 мин", callback_data=f"postpone_5_{vitamin_id}"),
                        InlineKeyboardButton("⏰ Через 10 мин", callback_data=f"postpone_10_{vitamin_id}"),
                        InlineKeyboardButton("⏰ Через 20 мин", callback_data=f"postpone_20_{vitamin_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=REPEAT_REMINDER_TEXT.format(
                            vitamin_name=vitamin_name,
                            attempt=attempts + 1,
                            max_attempts=MAX_REMINDER_ATTEMPTS
                        ),
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки повторного напоминания: {e}")

def main():
    """Основная функция"""
    # Создаём приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Проверяем наличие JobQueue
    if application.job_queue is None:
        print("❌ Ошибка: JobQueue не доступен. Установите библиотеку с поддержкой job-queue:")
        print("pip install \"python-telegram-bot[job-queue]\"")
        exit(1)
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Добавляем периодические задачи
    job_queue = application.job_queue
    job_queue.run_repeating(send_vitamin_reminder, interval=REMINDER_CHECK_INTERVAL, first=10)
    if ENABLE_REPEAT_REMINDERS:
        job_queue.run_repeating(send_repeat_reminders, interval=REPEAT_CHECK_INTERVAL, first=REPEAT_CHECK_INTERVAL)
    
    # Запускаем бота
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
