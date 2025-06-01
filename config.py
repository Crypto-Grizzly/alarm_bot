# config.py

# Токен бота
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Замените на ваш токен бота

# Список разрешённых пользователей (ID Telegram)
ALLOWED_USERS = [123456789, 987654321]  # Замените на реальные ID пользователей

# Путь к базе данных
DATABASE_PATH = "data/vitamins.db"

# Настройки логирования
LOG_LEVEL = "WARNING"  
LOG_FILE = "logs/bot.log"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
ENABLE_FILE_LOGGING = True

# Настройки напоминаний
REMINDER_CHECK_INTERVAL = 60  # Проверка каждые 60 секунд
REPEAT_CHECK_INTERVAL = 300  # Проверка повторных напоминаний каждые 5 минут
REPEAT_REMINDER_INTERVAL = 1800  # Повтор напоминания каждые 30 минут
MAX_REMINDER_ATTEMPTS = 3  # Максимальное количество попыток напоминания
ENABLE_REPEAT_REMINDERS = True

# Тексты сообщений
WELCOME_TEXT = "Добро пожаловать в бот для отслеживания приёма витаминов!\n\n" \
               "Используйте кнопки ниже для управления витаминами."
REMINDER_TEXT = "⏰ Пора принять {vitamin_name} в {reminder_time}!"
REPEAT_REMINDER_TEXT = "🔄 Напоминание #{attempt}: Пора принять {vitamin_name}! (Попытка {attempt} из {max_attempts})"

def validate_config() -> list:
    """Проверка конфигурации"""
    errors = []
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        errors.append("Токен бота не указан или не заменён в config.py")
    if not ALLOWED_USERS:
        errors.append("Список разрешённых пользователей пуст")
    if not DATABASE_PATH:
        errors.append("Путь к базе данных не указан")
    return errors

def create_directories():
    """Создание необходимых директорий"""
    import os
    db_dir = os.path.dirname(DATABASE_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
    if ENABLE_FILE_LOGGING:
        log_dir = os.path.dirname(LOG_FILE)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
