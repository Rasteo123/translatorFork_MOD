"""
EPUB Translator - Продвинутый инструмент для перевода EPUB книг
Поддерживает несколько AI провайдеров: Google Gemini, OpenRouter, HuggingFace
"""

__version__ = "9.9.0"
__author__ = "Команда EPUB Translator"
__license__ = "MIT"


# Информация о версии
VERSION_INFO = {
    'major': 9,
    'minor': 9,
    'patch': 0,
    'release': 'beta',
    'build': 'b'
}

def get_version():
    """Возвращает полную строку версии"""
    return __version__

def get_version_tuple():
    """Возвращает версию как кортеж (major, minor, patch)"""
    return (VERSION_INFO['major'], VERSION_INFO['minor'], VERSION_INFO['patch'])

# Метаданные пакета
__all__ = [
    'main',
    'run_translation_with_auto_restart',
    'get_version',
    'get_version_tuple',
    '__version__',
    '__author__',
    '__license__'
]

# Информация о пакете для диалогов помощи/о программе
PACKAGE_INFO = {
    'name': 'EPUB Переводчик',
    'description': 'Профессиональный инструмент для перевода EPUB книг с помощью AI',
    'version': __version__,
    'author': __author__,
    'license': __license__,
    'homepage': 'https://github.com/yourusername/epub-translator',
    'docs': 'https://epub-translator.readthedocs.io',
    'support': 'https://github.com/yourusername/epub-translator/issues'
}