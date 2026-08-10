# Animated Window Sizing + In-Window Modal Overlays — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MainShell плавно анимирует размер окна под требования текущего экрана (macOS-подобно, кроссплатформенно), а мелкие диалоги показываются как встроенные карточки поверх затемнённого интерфейса (как в App Store / Claude Desktop) вместо отдельных окон.

**Architecture:** Три независимых блока. (1) `CurrentSizeStack` — QStackedWidget, чьи size-хинты отражают только текущую страницу, чтобы скрытые страницы не блокировали уменьшение окна; хинты можно «замораживать» на время анимации. (2) `WindowResizeController` — слушает `stack_changed`, вычисляет целевой размер (пользовательский размер за сессию → `preferred_window_size` страницы → только минимум), клампит по экрану и анимирует `QPropertyAnimation(b"size")`. (3) `OverlayHost` + `present_dialog()` — существующие QDialog встраиваются в карточку без системной рамки поверх затемнённого и отключённого (`setEnabled(False)`) контента; `accept/reject/finished` у QDialog работают и в качестве child-виджета, `setEnabled(False)` на фоне даёт модальность (клики, Tab-фокус, шорткаты). Fallback без шелла — обычный `dialog.open()`.

**Tech Stack:** PyQt6, pytest (+offscreen QPA, см. tests/conftest.py).

## Global Constraints

- Кроссплатформенно: только изменение размера окна, без перемещения (Wayland не разрешает move).
- Не анимировать, когда окно maximized / fullscreen / minimized или невидимо.
- Целевой размер: clamp между минимумом страницы и `QScreen.availableGeometry()`.
- Пользовательский ручной размер запоминается на сессию per-страница (ключ — имя класса страницы) и имеет приоритет над `preferred_window_size`.
- Быстрые переходы: текущая анимация останавливается и перезапускается на новую цель.
- Существующие QDialog переносятся в overlay без изменения их внутренней логики; меняются только точки вызова (`exec()` → callback).
- Все существующие тесты остаются зелёными.

---

### Task 1: CurrentSizeStack — хинты по текущей странице

**Files:**
- Modify: `gemini_translator/ui/shell.py` (новый класс + использование в MainShell)
- Test: `tests/test_current_size_stack.py`

**Interfaces:**
- Produces: `CurrentSizeStack(QStackedWidget)` с методом `set_size_hint_override(QSize | None)`; `sizeHint()`/`minimumSizeHint()` считаются только по `currentWidget()`.

- [ ] Step 1: тест (минимум следует за текущей страницей; override замораживает).
- [ ] Step 2: реализация в shell.py; `MainShell` создаёт `CurrentSizeStack` вместо `QStackedWidget`.
- [ ] Step 3: pytest tests/test_current_size_stack.py + существующие shell-тесты. Commit.

### Task 2: WindowResizeController + preferred_window_size

**Files:**
- Create: `gemini_translator/ui/window_resize.py`
- Modify: `gemini_translator/ui/shell.py` (атрибут `ShellPage.preferred_window_size`, wiring в MainShell)
- Modify: страницы с мёртвым `resize()` → `preferred_window_size`:
  - `validation.py` TranslationValidatorPage (1180, 760), AIRepairReviewPage (1480, 840)
  - `consistency_checker.py` ConsistencyValidatorPage (1400, 950)
  - `glossary_dialogs/term_frequency_analyzer.py` TermFrequencyAnalyzerPage (1200, 800)
  - плюс sweep остальных ShellPage на `self.resize(` в `__init__`
- Test: `tests/test_window_resize_controller.py`

**Interfaces:**
- Consumes: `NavigationController.stack_changed`, `CurrentSizeStack.set_size_hint_override`.
- Produces: `WindowResizeController(window, navigation, stack, duration_ms=None)`, `set_duration(ms)`; `ShellPage.preferred_window_size: tuple[int, int] | None = None`; у MainShell — `self.resize_controller`.

Логика цели: remembered[str(class)] → preferred → None (если текущий размер уже покрывает минимум — не трогаем); `expandedTo(минимум окна)`, `boundedTo(availableGeometry)`. На время анимации стек замораживается `QSize(0,0)`, по завершении освобождается. Ручные resize запоминаются в eventFilter (когда нет анимации и окно в нормальном состоянии).

- [ ] Step 1: тесты (grow к preferred при push; shrink при pop; clamp по экрану; remembered приоритетнее preferred; retarget при быстрых переходах). duration=0 в тестах.
- [ ] Step 2: реализация + wiring + конверсия страниц.
- [ ] Step 3: pytest новые + shell-тесты. Commit.

### Task 3: OverlayHost + present_dialog

**Files:**
- Create: `gemini_translator/ui/overlay_host.py`
- Modify: `gemini_translator/ui/shell.py` (`self.overlay_host = OverlayHost(self, blocked=central)`)
- Test: `tests/test_overlay_host.py`

**Interfaces:**
- Produces:
  - `OverlayHost(window, blocked)` — сам красит затемнение, глотает мышь, следит за resize окна через eventFilter; `present(dialog, on_finished=None)`.
  - `find_overlay_host(widget) -> OverlayHost | None` — по `widget.window().overlay_host`.
  - `present_dialog(context, dialog, on_finished=None)` — overlay, иначе fallback `dialog.open()` (window-modal) + `deleteLater` по finished.

Карточка: QFrame#overlayCard, стиль `palette(window)` + радиус 12 + рамка + QGraphicsDropShadowEffect; внутренние отступы 12px; размер = `dialog.size()` если WA_Resized, иначе `sizeHint()`, clamp: min (minimumSize/minimumSizeHint) … 90% хоста; по центру. Вложенные present — стеком, нижняя карточка `setEnabled(False)`. Dismiss: карточка удаляется (`deleteLater`, диалог умирает вместе с ней — callback вызывается синхронно до этого), фон включается при пустом стеке, фокус восстанавливается. Esc работает штатно (QDialog.keyPressEvent), фокус заводится внутрь диалога при показе.

- [ ] Step 1: тесты (показ: хост видим, фон disabled; accept → callback(Accepted), фон enabled, хост скрыт; Esc → Rejected; вложенность; fallback без шелла; восстановление фокуса).
- [ ] Step 2: реализация + wiring.
- [ ] Step 3: pytest. Commit.

### Task 4: Первая волна миграции точек вызова

**Files:**
- Modify: `consistency_checker.py` `_open_chapter_selection_dialog` (exec → present_dialog + callback)
- Modify: `pages/home_page.py` `_open_proxy_settings`, `dialogs/misc.py:265`, `dialogs/setup.py:1830` (ProxySettingsDialog exec → present_dialog)
- Test: обновить `tests/test_home_page.py` при необходимости; `tests/test_proxy_dialog_ssh_mode.py` не трогаем (прямое инстанцирование).

- [ ] Step 1: переписать точки вызова на callback-и (`QDialog.DialogCode.Accepted` проверяется в callback).
- [ ] Step 2: pytest затронутые тесты; полный прогон. Commit.

### Task 5: Полная верификация

- [ ] Полный pytest.
- [ ] Ручной smoke в GUI отложен: окружение headless, попросить пользователя проверить визуально.
