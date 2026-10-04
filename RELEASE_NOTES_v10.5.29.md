# GitHub Release v10.5.29

Suggested tag: `v10.5.29`
Suggested title: `translatorFork_MOD v10.5.29`
Range: `v10.5.28..v10.5.29` (`2026-10-04`)

## Release Body

# translatorFork_MOD v10.5.29

## Исправления

- Исправлено зависание при сверке проекта после удаления файлов переведённых глав. Окно «Идёт анализ проекта…» больше не перекрывает вопрос об очистке отсутствующих файлов и не блокирует кнопки «Да» и «Нет».
- Исправлена обработка быстрых ответов и последовательных вопросов при синхронизации. При выборе «Нет» или закрытии вопроса крестиком записи сохраняются, а сверка продолжается.

Полный список изменений: https://github.com/Rasteo123/translatorFork_MOD/compare/v10.5.28...v10.5.29

## Assets

Сборки прикрепляет GitHub Actions (`release.yml`) после проверки тега:

- `GeminiTranslator-Setup.exe`
- `GeminiTranslator-Portable.exe`
- `GeminiTranslator-macOS.dmg` или `GeminiTranslator-macOS.zip`
- `update-manifest.json`
