# pip install playwright
# playwright install chromium

import asyncio
import traceback
from ..base import BaseApiHandler
from ..errors import (
    ContentFilterError, NetworkError, LocationBlockedError, 
    RateLimitExceededError, ModelNotFoundError, ValidationFailedError, 
    TemporaryRateLimitError, PartialGenerationError, OperationCancelledError
)

PLAYWRIGHT_AVAILABLE = False
class PlaywrightTimeoutError(Exception): pass

# try:
    # from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
    # PLAYWRIGHT_AVAILABLE = True
# except ImportError:
    # PLAYWRIGHT_AVAILABLE = False
    # class PlaywrightTimeoutError(Exception): pass

class BrowserApiHandler(BaseApiHandler):
    def __init__(self, worker):
        super().__init__(worker)
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("pip install playwright")
        
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.is_logged_in = False

    def setup_client(self, client_override=None, proxy_settings=None):
        super().setup_client(client_override, proxy_settings)
        self.service_url = self.worker.provider_config.get("base_url", "https://chatgpt.com")
        
        # Парсим логин/пароль из API key
        if ":" in self.worker.api_key:
            self.username, self.password = self.worker.api_key.split(":", 1)
        else:
            self.username = self.worker.api_key
            self.password = None

        # Загружаем селекторы
        self.selectors = self.worker.provider_config.get("selectors", {})
        return True

    async def _ensure_browser_active(self):
        if self.page and not self.page.is_closed():
            return

        self.playwright = await async_playwright().start()
        
        proxy_cfg = None
        if self.proxy_settings and self.proxy_settings.get('enabled'):
            proxy_cfg = {
                "server": f"{self.proxy_settings['host']}:{self.proxy_settings['port']}"
            }
            if self.proxy_settings.get('user'):
                proxy_cfg["username"] = self.proxy_settings['user']
                proxy_cfg["password"] = self.proxy_settings['pass']

        self.browser = await self.playwright.chromium.launch(
            headless=False, # Смените на True для скрытого режима
            proxy=proxy_cfg,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )
        self.page = await self.context.new_page()

    async def _perform_login(self):
        try:
            print(f"[Browser] Переход на {self.service_url}...")
            await self.page.goto(self.service_url, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # Если поле ввода уже видно - мы залогинены (cookies)
            if await self.page.locator(self.selectors["input_box"]).is_visible():
                self.is_logged_in = True
                return

            print(f"[Browser] Логинимся как {self.username}...")
            # Логика входа (примерная, зависит от сайта)
            if self.selectors.get("login_btn_start"):
                await self.page.click(self.selectors["login_btn_start"])
            
            await self.page.fill(self.selectors["login_email"], self.username)
            await self.page.keyboard.press("Enter")
            await asyncio.sleep(2)
            
            if self.password:
                await self.page.fill(self.selectors["login_password"], self.password)
                await self.page.keyboard.press("Enter")
            
            # Ждем появления чата
            await self.page.wait_for_selector(self.selectors["input_box"], timeout=60000)
            self.is_logged_in = True
            
        except Exception as e:
            raise NetworkError(f"Ошибка входа: {e}")

    async def _start_new_chat(self):
        """
        Критически важная функция: Сброс контекста.
        Либо жмет кнопку 'New Chat', либо переходит на чистый URL.
        """
        try:
            new_chat_btn_sel = self.selectors.get("new_chat_button")
            
            # Стратегия 1: Если есть кнопка "Новый чат", жмем её
            if new_chat_btn_sel and await self.page.locator(new_chat_btn_sel).is_visible():
                await self.page.click(new_chat_btn_sel)
            else:
                # Стратегия 2: Просто переходим на базовый URL (обычно это создает новый чат)
                # Проверяем, не находимся ли мы уже на чистой странице, чтобы не перезагружать зря
                if self.page.url.split('?')[0].rstrip('/') != self.service_url.rstrip('/'):
                    await self.page.goto(self.service_url)
            
            # После сброса обязательно ждем, пока поле ввода снова станет доступным
            await self.page.wait_for_selector(self.selectors["input_box"], state="visible", timeout=10000)
            
        except Exception as e:
            print(f"[Browser WARN] Не удалось сбросить чат: {e}")
            # Пытаемся продолжить, возможно, мы и так в новом чате

    
    async def _run_setup_steps(self):
        """Выполняет предварительные клики (например, переключение режима)."""
        steps = self.worker.provider_config.get("setup_steps", [])
        if not steps:
            return

        print(f"[Browser] Выполнение {len(steps)} шагов настройки...")
        try:
            for step in steps:
                action = step.get("action", "click")
                selector = step.get("selector")
                
                if action == "click":
                    # Ждем появления и кликаем
                    loc = self.page.locator(selector).first
                    if await loc.is_visible():
                        await loc.click()
                        # Небольшая пауза для анимации меню
                        await asyncio.sleep(0.5)
                    else:
                        print(f"[Browser WARN] Не найден элемент для шага: {selector}")
                        
        except Exception as e:
            print(f"[Browser WARN] Ошибка настройки чата: {e}")
    
    
    async def call_api(self, prompt, log_prefix, allow_incomplete=False, use_stream=True, debug=False, max_output_tokens=None):
        try:
            self._debug_record_request(
                {
                    "service_url": self.service_url,
                    "prompt": prompt,
                    "selectors": self.selectors,
                },
                extra={"mode": "browser"},
            )
            await self._ensure_browser_active()
            
            if not self.is_logged_in:
                await self._perform_login()

            # --- ВОТ ТУТ ГЛАВНОЕ ИЗМЕНЕНИЕ ---
            # Перед каждым запросом очищаем контекст
            await self._start_new_chat()
            # ---------------------------------

            # 1. Ввод текста
            input_box = self.page.locator(self.selectors["input_box"])
            await input_box.fill(prompt)
            await asyncio.sleep(0.5) 
            
            # 2. ---> НАСТРОЙКА (Включение Writing Mode) <---
            await self._run_setup_steps()
            
            # 3. Нажатие отправить
            send_btn = self.page.locator(self.selectors["send_button"])
            if await send_btn.is_enabled():
                await send_btn.click()
            else:
                 # Иногда кнопка активируется только после ввода, пробуем нажать Enter
                await input_box.press("Enter")

            # 4. Ожидание ответа
            last_text = ""
            stall_counter = 0
            
            # Ждем появления блока ответа
            response_selector = self.selectors.get("last_message", ".markdown") # Дефолт
            try:
                # Берем ПОСЛЕДНИЙ элемент, так как это новый ответ
                locator = self.page.locator(response_selector).last
                await locator.wait_for(state="visible", timeout=15000)
            except PlaywrightTimeoutError:
                 raise NetworkError("Сайт не начал отвечать (timeout).")

            # Цикл "стриминга"
            while True:
                if self.worker.is_cancelled:
                    raise OperationCancelledError("Отмена пользователем")

                current_text = await locator.inner_text()
                
                if current_text != last_text:
                    last_text = current_text
                    stall_counter = 0
                else:
                    stall_counter += 1

                # Проверяем, вернулась ли кнопка отправки (признак конца генерации)
                send_btn_visible = await self.page.locator(self.selectors["send_button"]).is_visible()
                
                # Если кнопка отправки есть И текст не меняется пару итераций -> готово
                if send_btn_visible and stall_counter > 2:
                    break
                
                if stall_counter > 100: # ~20 секунд тишины
                    if allow_incomplete and last_text: break
                    raise NetworkError("Генерация зависла.")

                await asyncio.sleep(0.2)
            
            self._debug_record_response(last_text, status="ok", extra={"mode": "browser"})
            return last_text

        except Exception as e:
            # При ошибке закрываем страницу, чтобы в следующий раз начать с чистого листа
            if self.page: 
                await self.page.close()
                self.page = await self.context.new_page()
            self._debug_record_response(str(e), status="error", extra={"mode": "browser"})
            raise NetworkError(f"Ошибка браузера: {e}")

    async def _close_thread_session_internal(self):
        if self.browser:
            await self.browser.close()
