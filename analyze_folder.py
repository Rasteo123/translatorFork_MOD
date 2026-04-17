import os
import shutil  # <--- НОВЫЙ КОД: Добавляем импорт для удаления папок
# --- Настройки ---
# Имя файла, в который будут сохранены результаты
OUTPUT_FILENAME = "directory_contents.txt"

# Список папок, которые нужно полностью исключить из анализа.
# Просто добавьте имя папки в этот список.
EXCLUDED_DIRS = [
    "venv",          # Виртуальные окружения Python
    "vendored_libs", # <-- ВОТ ДОБАВЛЕННАЯ ПАПКА
    ".git",          # Папки репозитория Git
    "__pycache__",   # Кэш-файлы Python
    "node_modules",  # Зависимости JavaScript/Node.js
    ".vscode",       # Настройки редактора VS Code
    ".idea",         # Настройки редакторов от JetBrains (PyCharm)
]
# -----------------

def analyze_directory(start_path='.'):
    """
    Рекурсивно анализирует директорию, читает все файлы
    и сохраняет их содержимое в один выходной файл,
    исключая указанные папки.
    """
    try:
        script_name = os.path.basename(__file__)
    except NameError:
        script_name = None

    print(f"Начинаю анализ директории: {os.path.abspath(start_path)}")
    print(f"Исключаемые папки: {', '.join(EXCLUDED_DIRS)}")
    print(f"Результаты будут сохранены в файл: {OUTPUT_FILENAME}")

    with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as output_file:
        
        for root, dirs, files in os.walk(start_path, topdown=True):
            
            # --- ВОТ ГЛАВНОЕ ИЗМЕНЕНИЕ ---
            # Исключаем папки из дальнейшего обхода.
            # Мы изменяем список 'dirs' на лету, чтобы os.walk() не заходил в них.
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            # ---------------------------

            dirs.sort()
            files.sort()

            for filename in files:
                if filename == script_name or filename == OUTPUT_FILENAME:
                    continue

                filepath = os.path.join(root, filename)

                output_file.write(f'{"="*40} Файл: {filepath} {"="*40}\n\n')

                try:
                    with open(filepath, 'r', encoding='utf-8', errors='strict') as input_file:
                        content = input_file.read()
                        output_file.write(content)
                except UnicodeDecodeError:
                    output_file.write('[ОШИБКА ЧТЕНИЯ]: Не удалось прочитать файл как текст (UTF-8).\n'
                                      'Возможно, это бинарный файл (картинка, архив и т.д.).')
                except Exception as e:
                    output_file.write(f'[ОШИБКА ЧТЕНИЯ]: {e}')
                
                output_file.write(f'\n\n{"="*90}\n\n\n')

    print("Анализ завершен.")
    print(f"Все данные сохранены в файл: {os.path.abspath(OUTPUT_FILENAME)}")


from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.resolve()
EXCLUDE_DIRS = {'venv', '.venv', 'env', '.git', '__pycache__', 'dist', 'build'}
# <--- НОВЫЙ КОД: Функция для очистки кэша ---
def clean_python_cache(start_path):
    """
    Рекурсивно находит и удаляет все папки __pycache__ в директории проекта.
    """
    print("\n--- Этап 0: Очистка кэша Python (__pycache__) ---")
    deleted_count = 0
    for root, dirs, files in os.walk(start_path):
        if '__pycache__' in dirs:
            pycache_path = os.path.join(root, '__pycache__')
            # Проверяем, чтобы случайно не удалить что-то из исключенных папок
            if not any(excluded in pycache_path for excluded in EXCLUDE_DIRS if excluded != '__pycache__'):
                try:
                    print(f"  -> Удаление: {pycache_path}")
                    shutil.rmtree(pycache_path)
                    deleted_count += 1
                except OSError as e:
                    print(f"  [Предупреждение] Не удалось удалить {pycache_path}: {e}")
    
    if deleted_count > 0:
        print(f"✅ Очистка завершена. Удалено папок: {deleted_count}.")
    else:
        print("✅ Кэш Python чист, удалять нечего.")
    return deleted_count
# --- КОНЕЦ НОВОГО КОДА ---

if __name__ == "__main__":
    clean_python_cache(PROJECT_ROOT)
    analyze_directory()
