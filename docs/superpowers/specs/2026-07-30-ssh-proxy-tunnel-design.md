# Автоматический SSH-туннель для прокси

## Проблема

Прокси в приложении сегодня — чисто клиентская настройка: пользователь указывает
`host/port/user/pass` уже существующего SOCKS5/SOCKS4/HTTP-прокси, и
`api/base.py` строит из этого `aiohttp_socks.ProxyConnector`. Никакого
управления соединением приложение не делает — `GlobalProxyController`
([proxy_tool.py](../../../gemini_translator/utils/proxy_tool.py)) лишь
ретранслирует событие `proxy_settings_changed` дальше как
`current_proxy_status`.

У пользователя есть свой VPS (91.107.201.91) и SSH-ключ; фактический прокси —
это локальный SOCKS5-туннель, поднимаемый командой `ssh -D <port> -N`. Сейчас
туннель нужно запускать и следить за ним вручную в отдельном терминале, что
приводит к сбоям перевода при обрыве соединения (см. инцидент: перевод упал
после пары глав с `ProxyConnectionError: Couldn't connect to proxy
127.0.0.1:8080` — SSH-процесс не был поднят/не пережил обрыв).

Нужно: приложение само поднимает и держит туннель, пока включена галочка
"Включить прокси" в `ProxySettingsDialog`.

## Область

- Только macOS (пользователь работает на macOS; Windows-поддержка — отдельная
  задача при необходимости).
- Ключ без passphrase (без интеграции с ssh-agent/запросом пароля).
- Автоматизируется только режим "SSH-туннель" — обычный
  SOCKS5/SOCKS4/HTTP-прокси (уже готовый, без SSH) продолжает работать как
  раньше, без изменений в этой части.

## Модель данных

Расширяем существующий словарь `proxy_settings`
([settings.py:897,932](../../../gemini_translator/utils/settings.py)) новыми
полями, не трогая текущие:

```python
proxy_settings = {
    "enabled": bool,
    "type": "SOCKS5" | "SOCKS4" | "HTTP",   # без изменений
    "host": str,      # для tunnel_mode="ssh" — фиксированно "127.0.0.1"
    "port": int,      # для tunnel_mode="ssh" — локальный порт туннеля
    "user": str,      # без изменений (для tunnel_mode="ssh" не используется)
    "pass": str,      # без изменений (для tunnel_mode="ssh" не используется)
    "saved_proxies": [...],                 # без изменений по структуре

    # новые поля:
    "tunnel_mode": "none" | "ssh",
    "ssh_host": str,
    "ssh_port": int,           # по умолчанию 22
    "ssh_user": str,
    "ssh_key_path": str,       # путь к приватному ключу, только путь — файл не читается приложением
}
```

`saved_proxies` — каждая запись в списке аналогично может нести эти поля,
если сохранена в режиме "ssh".

## Компонент: `SshTunnelManager`

Новый файл `gemini_translator/utils/ssh_tunnel.py`, по образцу
[`PowerInhibitor`](../../../gemini_translator/utils/power_inhibitor.py) —
небольшой, тестируемый, с инжектируемой фабрикой процесса:

```python
class SshTunnelManager(QObject):
    status_changed = pyqtSignal(str, str)  # (state, message)
    # states: "connecting" | "up" | "down" | "error"

    def __init__(self, popen_factory: Callable[[list[str]], object] | None = None): ...
    def start(self, *, ssh_host, ssh_port, ssh_user, ssh_key_path, local_port) -> None: ...
    def stop(self) -> None: ...
    @property
    def active(self) -> bool: ...
```

Команда запуска (список аргументов, без shell):

```
ssh -i <ssh_key_path> -p <ssh_port> -D <local_port> -N
    -o StrictHostKeyChecking=yes
    -o ServerAliveInterval=15
    -o ServerAliveCountMax=3
    -o ExitOnForwardFailure=yes
    <ssh_user>@<ssh_host>
```

Поведение:

- Внутренний `QTimer` (интервал 1с) проверяет `process.poll()`.
- При падении процесса — экспоненциальный backoff перед перезапуском:
  3s → 6s → 12s → 24s → 60s (капается на 60s), повторяется бесконечно, пока не
  вызван `stop()`.
- `stderr` процесса читается неблокирующе (отдельный поток/`selectors`);
  последняя строка (`Permission denied`, `Host key verification failed`,
  `Connection refused` и т.п.) передаётся через `status_changed("error", ...)`.
- `stop()`: `terminate()` → `wait(timeout=3)` → `kill()` при таймауте — тот же
  паттерн, что уже в
  [`PowerInhibitor.allow_sleep`](../../../gemini_translator/utils/power_inhibitor.py:107-125).
- `start()`/`stop()` идемпотентны (проверяют `active` перед действием) —
  повторные быстрые вызовы не создают гонок.
- Host key проверяется строго по обычному `~/.ssh/known_hosts` пользователя
  (`StrictHostKeyChecking=yes`, без авто-добавления) — при незнакомом хосте
  `ssh` завершится с ошибкой, которая всплывёт в статусе; пользователь должен
  один раз вручную подтвердить fingerprint через обычный `ssh` в терминале.
- Класс ничего не знает про `event_bus` — только про процесс и его статус,
  что позволяет тестировать его изолированно.

## Интеграция с `GlobalProxyController`

Расширяем существующий класс
([proxy_tool.py](../../../gemini_translator/utils/proxy_tool.py)), не заводим
параллельный:

- На `proxy_settings_changed`/`proxy_started` с `enabled=True and
  tunnel_mode=="ssh"` — если `SshTunnelManager` не активен, вызвать
  `start(...)` с параметрами из `data`.
- На `enabled=False` или `tunnel_mode != "ssh"` — вызвать `stop()`.
- При инициализации приложения — если сохранённые настройки уже содержат
  `enabled=True and tunnel_mode=="ssh"`, контроллер поднимает туннель сразу
  при старте (без необходимости открывать диалог и нажимать что-либо) — это и
  есть ответ на исходный запрос: включённая галочка = туннель поднят,
  независимо от того, когда она была включена.
- Подписка на `status_changed` от `SshTunnelManager` — ретранслируется как
  `current_proxy_status` с доп. полем `tunnel_state`, тем же каналом, которым
  уже пользуется UI
  ([setup.py:1824](../../../gemini_translator/ui/dialogs/setup.py:1824),
  [misc.py:294](../../../gemini_translator/ui/dialogs/misc.py:294)).
- На закрытии приложения (`aboutToQuit`/там же, где освобождается
  `PowerInhibitor`) — `stop()` вызывается явно, чтобы не оставлять висящий
  `ssh`-процесс.

Готовность локального порта отдельно не ожидается: если API-запрос уйдёт
раньше, чем туннель поднялся, он получит `ProxyConnectionError` и будет
повторён существующей логикой ретраев в оркестраторе провайдеров — отдельный
"wait for port" механизм не нужен (YAGNI).

## Изменения UI (`ProxySettingsDialog`)

В [proxy.py](../../../gemini_translator/ui/dialogs/proxy.py):

- Переключатель режима: "Обычный прокси" / "Автотуннель через SSH"
  (`tunnel_mode`).
- В режиме "Автотуннель через SSH" вместо полей `Пользователь`/`Пароль`
  показываются: **SSH-хост**, **SSH-порт** (по умолчанию 22),
  **SSH-пользователь**, **Путь к приватному ключу** (текстовое поле + кнопка
  "Обзор..." через `QFileDialog`; содержимое ключа не читается и не
  валидируется приложением, только путь).
- Поле хоста/порта в этом режиме превращается в "Локальный порт туннеля"
  (`host` фиксирован как `127.0.0.1`, редактируется только порт).
- Под чекбоксом "Включить прокси" — лейбл статуса, обновляемый по
  `current_proxy_status`: "Отключено" / "Подключение..." / "Активен" /
  "Ошибка: <текст из stderr>".
- В таблице `saved_proxies`: для записей с `tunnel_mode="ssh"` колонка "Хост"
  показывает SSH-хост.
- Валидация в `validate_inputs()`: при `tunnel_mode=="ssh"` — путь к ключу
  должен существовать и быть файлом (`os.path.isfile`), SSH-хост и
  SSH-пользователь не пустые; иначе — `QMessageBox.warning`, как для
  остальных полей.

## Обработка ошибок и краевые случаи

- **`ssh` не найден в PATH** (`FileNotFoundError` от `Popen`) —
  `status_changed("error", "ssh не найден в PATH")`, ретраев не делаем
  (конфигурационная проблема, не транзиентная).
- **Ключ недоступен/удалён** — `Permission denied`/`No such file` от `ssh`
  попадают в `status_changed` как есть; отдельно "постоянные" ошибки от
  "транзиентных" не различаем в первой версии — backoff продолжается до явного
  `stop()`, но ошибка всё время видна в статусе.
- **Аварийное завершение приложения** (не через `aboutToQuit`) — процесс
  `ssh` может остаться висеть; принимаемый риск, симметричный уже
  существующему риску с `caffeinate` в `PowerInhibitor`.
- **Быстрые повторные переключения галочки** — `start`/`stop` идемпотентны,
  гонок не создают (всё в Qt event loop).

## Тестирование

- Юнит-тесты `SshTunnelManager` с фейковой `popen_factory` (управляемый
  `poll()`): корректность собранной команды/аргументов, рост и кап backoff,
  `terminate → wait → kill` при `stop()`, эмиссия `status_changed` в
  ожидаемых состояниях — по образцу тестов `PowerInhibitor`.
- Тесты wiring в `GlobalProxyController`: `proxy_settings_changed` с нужными
  полями вызывает `start`/`stop` на мокнутом менеджере — по образцу
  [test_proxy_session_reset.py](../../../tests/test_proxy_session_reset.py).
- Тест валидации `ProxySettingsDialog.validate_inputs()`: несуществующий путь
  к ключу/пустой SSH-хост блокируют "Принять".
- Реальное соединение до личного VPS пользователя не покрывается автотестом
  (внешняя инфраструктура) — только ручной смоук-тест после реализации.
