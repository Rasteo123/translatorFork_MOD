import asyncio
import json
import os
import shutil
import time
from collections import deque
from pathlib import Path

from .. import config as api_config
from ..base import BaseApiHandler
from ..errors import (
    LocationBlockedError,
    ModelNotFoundError,
    NetworkError,
    TemporaryRateLimitError,
    ValidationFailedError,
)


class WorkAsciiChatGptApiHandler(BaseApiHandler):
    def __init__(self, worker):
        super().__init__(worker)
        self._bridge_process = None
        self._bridge_state_lock = asyncio.Lock()
        self._bridge_write_lock = asyncio.Lock()
        self._stdout_task = None
        self._stderr_task = None
        self._stderr_tail = deque(maxlen=40)
        self._pending_commands = {}
        self._next_command_id = 0

        self.workascii_root = None
        self.profile_dir = None
        self.node_path = None
        self.bridge_script_path = None
        self.playwright_package_root = None
        self.playwright_browsers_path = None
        self.execution_cwd = None
        self.workspace_name = ""
        self.workspace_index = 1
        self.headless = False
        self.timeout_sec = 1800
        self.parallel_requests = 1
        self.profile_template_dir = None
        self.refresh_every_requests = 0

        self._bridge_request_gate = asyncio.Condition()
        self._active_bridge_calls = 0
        self._successful_calls_since_refresh = 0
        self._bridge_restart_pending = False

    def setup_client(self, client_override=None, proxy_settings=None):
        super().setup_client(client_override, proxy_settings)

        resolved_root = api_config.default_workascii_runtime_root()
        resolved_profile = api_config.default_workascii_profile_dir(resolved_root)
        resolved_node = api_config.find_node_executable(resolved_root)

        self.workascii_root = resolved_root
        self.profile_dir = resolved_profile
        self.node_path = resolved_node
        self.bridge_script_path = api_config.get_resource_path(
            "gemini_translator/scripts/chatgpt_workascii_bridge.cjs"
        )
        self.playwright_package_root = api_config.find_playwright_package_root(resolved_root)
        self.playwright_browsers_path = api_config.find_playwright_browsers_path(resolved_root)
        self.execution_cwd = resolved_root or api_config.default_workascii_runtime_root()
        self.workspace_name = str(getattr(self.worker, "workascii_workspace_name", "") or "").strip()
        template_dir = str(getattr(self.worker, "workascii_profile_template_dir", "") or "").strip()
        self.profile_template_dir = Path(template_dir).expanduser() if template_dir else None

        try:
            workspace_index = int(getattr(self.worker, "workascii_workspace_index", 1) or 1)
        except (TypeError, ValueError):
            workspace_index = 1
        self.workspace_index = max(1, workspace_index)

        try:
            timeout_sec = int(getattr(self.worker, "workascii_timeout_sec", 1800) or 1800)
        except (TypeError, ValueError):
            timeout_sec = 1800
        self.timeout_sec = max(60, timeout_sec)
        self.headless = bool(getattr(self.worker, "workascii_headless", False))
        try:
            refresh_every_requests = int(getattr(self.worker, "workascii_refresh_every_requests", 0) or 0)
        except (TypeError, ValueError):
            refresh_every_requests = 0
        self.refresh_every_requests = max(0, refresh_every_requests)

        raw_parallel_requests = getattr(self.worker, "max_concurrent_requests", None)
        if not raw_parallel_requests:
            model_config = getattr(self.worker, "model_config", None) or {}
            if isinstance(model_config, dict):
                raw_parallel_requests = model_config.get("max_concurrent_requests", 1)
        try:
            parallel_requests = int(raw_parallel_requests or 1)
        except (TypeError, ValueError):
            parallel_requests = 1
        self.parallel_requests = max(1, parallel_requests)
        self._active_bridge_calls = 0
        self._successful_calls_since_refresh = 0
        self._bridge_restart_pending = False
        return True

    async def call_api(
        self,
        prompt,
        log_prefix,
        allow_incomplete=False,
        use_stream=True,
        debug=False,
        max_output_tokens=None,
    ):
        system_instruction = (self.worker.prompt_builder.system_instruction or "").strip() or None

        try:
            await self._acquire_bridge_request_slot()
            await self._ensure_bridge_ready()
            command_payload = {
                "type": "translate",
                "prompt": prompt,
                "system_instruction": system_instruction,
            }
            self._debug_record_request(
                {
                    "bridge": str(self.bridge_script_path),
                    "cwd": str(self.execution_cwd),
                    "playwright_package_root": str(self.playwright_package_root) if self.playwright_package_root else "",
                    "playwright_browsers_path": str(self.playwright_browsers_path) if self.playwright_browsers_path else "",
                    "payload": command_payload,
                },
                extra={
                    "timeout_sec": self.timeout_sec,
                    "parallel_requests": self.parallel_requests,
                    "profile_template_dir": str(self.profile_template_dir) if self.profile_template_dir else "",
                    "refresh_every_requests": self.refresh_every_requests,
                },
            )
            response = await self._send_command(
                command_payload,
                timeout=max(
                    self.timeout_sec + 120,
                    int(self.worker.provider_config.get("base_timeout", self.timeout_sec + 120)),
                ),
            )
        except asyncio.CancelledError:
            await self._terminate_bridge()
            raise
        except Exception:
            await self._release_bridge_request_slot(success=False)
            raise

        if response.get("ok"):
            self._debug_record_response(response, status="ok", extra={"mode": "bridge"})
            translated_text = str(response.get("text", "") or "")
            if translated_text.strip():
                await self._release_bridge_request_slot(success=True)
                return translated_text
            await self._release_bridge_request_slot(success=False)
            raise NetworkError("ChatGPT Web returned an empty response.", delay_seconds=20)

        self._debug_record_response(response, status=str(response.get("code") or "error"), extra={"mode": "bridge"})
        await self._release_bridge_request_slot(success=False)
        self._raise_bridge_error(response)

    async def _acquire_bridge_request_slot(self):
        while True:
            should_restart = False

            async with self._bridge_request_gate:
                if self._bridge_restart_pending:
                    if self._active_bridge_calls == 0:
                        self._bridge_restart_pending = False
                        should_restart = True
                    else:
                        await self._bridge_request_gate.wait()
                        continue
                else:
                    self._active_bridge_calls += 1
                    return

            if should_restart:
                if self.refresh_every_requests > 0:
                    self.worker._post_event(
                        "log_message",
                        {
                            "message": (
                                "[work_ascii] Перезапуск ChatGPT Web для очистки браузерного состояния "
                                f"после {self.refresh_every_requests} успешных запросов."
                            )
                        },
                    )
                await self._terminate_bridge()

    async def _release_bridge_request_slot(self, success: bool):
        async with self._bridge_request_gate:
            if success and self.refresh_every_requests > 0 and not self._bridge_restart_pending:
                self._successful_calls_since_refresh += 1
                if self._successful_calls_since_refresh >= self.refresh_every_requests:
                    self._successful_calls_since_refresh = 0
                    self._bridge_restart_pending = True

            if self._active_bridge_calls > 0:
                self._active_bridge_calls -= 1
            self._bridge_request_gate.notify_all()

    async def _ensure_bridge_ready(self):
        if self._bridge_process and self._bridge_process.returncode is None:
            return

        async with self._bridge_state_lock:
            if self._bridge_process and self._bridge_process.returncode is None:
                return

            await self._terminate_bridge_locked()

            if not self.profile_dir:
                raise ModelNotFoundError("Unable to resolve the saved ChatGPT profile directory.")
            if not self.execution_cwd:
                raise ModelNotFoundError("Unable to resolve the ChatGPT Web bridge working directory.")
            if not self.node_path or not self.node_path.exists():
                raise ModelNotFoundError(
                    "Bundled node runtime for ChatGPT Web was not found. Rebuild the app or install the Playwright runtime."
                )
            if (
                not self.playwright_package_root
                or not self.playwright_package_root.exists()
                or not (self.playwright_package_root / "package.json").exists()
            ):
                raise ModelNotFoundError(
                    "Bundled Playwright runtime was not found. Rebuild the app or install the Playwright runtime."
                )
            if not self.bridge_script_path.exists():
                raise ModelNotFoundError(f"Bridge script not found: {self.bridge_script_path}")

            await self._prepare_profile_dir_for_launch()
            self.execution_cwd.mkdir(parents=True, exist_ok=True)
            self._stderr_tail.clear()
            process_env = os.environ.copy()
            if self.playwright_browsers_path and self.playwright_browsers_path.exists():
                process_env["PLAYWRIGHT_BROWSERS_PATH"] = str(self.playwright_browsers_path)

            try:
                self._bridge_process = await asyncio.create_subprocess_exec(
                    str(self.node_path),
                    str(self.bridge_script_path),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.execution_cwd),
                    env=process_env,
                )
            except NotImplementedError as exc:
                raise RuntimeError(
                    "The current asyncio event loop does not support subprocesses required by the ChatGPT Web bridge. "
                    "Windows requires a Proactor event loop."
                ) from exc

            self._stdout_task = asyncio.create_task(self._drain_stdout())
            self._stderr_task = asyncio.create_task(self._drain_stderr())

            init_response = await self._send_command(
                {
                    "type": "init",
                    "config": {
                        "workascii_root": str(self.workascii_root or self.execution_cwd),
                        "profile_dir": str(self.profile_dir),
                        "playwright_package_root": str(self.playwright_package_root),
                        "browsers_path": str(self.playwright_browsers_path) if self.playwright_browsers_path else "",
                        "workspace_name": self.workspace_name,
                        "workspace_index": self.workspace_index,
                        "headless": self.headless,
                        "timeout_sec": self.timeout_sec,
                        "parallel_requests": self.parallel_requests,
                    },
                },
                timeout=self._get_bridge_init_timeout(),
            )
            if not init_response.get("ok"):
                await self._terminate_bridge_locked()
                self._raise_bridge_error(init_response, during_init=True)

    def _get_bridge_init_timeout(self) -> int:
        return self.timeout_sec if not self.headless else min(self.timeout_sec, 120)

    async def _prepare_profile_dir_for_launch(self):
        if not self.profile_dir:
            return
        await asyncio.to_thread(self._prepare_profile_dir_for_launch_sync)

    def _prepare_profile_dir_for_launch_sync(self):
        target_dir = Path(self.profile_dir)
        if target_dir.exists() and not target_dir.is_dir():
            raise ModelNotFoundError(f"ChatGPT runtime profile path is not a directory: {target_dir}")

        target_dir.parent.mkdir(parents=True, exist_ok=True)

        if not self.profile_template_dir:
            target_dir.mkdir(parents=True, exist_ok=True)
            return

        template_dir = Path(self.profile_template_dir).expanduser()
        if not template_dir.exists() or not template_dir.is_dir():
            raise ModelNotFoundError(f"ChatGPT profile template directory was not found: {template_dir}")

        template_resolved = template_dir.resolve(strict=True)
        target_resolved = target_dir.resolve(strict=False)
        if str(template_resolved).lower() == str(target_resolved).lower():
            raise ModelNotFoundError(
                "ChatGPT profile template directory must be different from the runtime profile directory."
            )

        if target_resolved == Path(target_resolved.anchor):
            raise ModelNotFoundError(
                f"Refusing to reset the ChatGPT runtime profile because the target path resolved to a drive root: {target_resolved}"
            )

        self._replace_profile_dir_from_template(template_resolved, target_resolved)

    def _replace_profile_dir_from_template(self, template_dir: Path, target_dir: Path):
        last_error = None
        for attempt in range(3):
            try:
                if target_dir.exists():
                    if target_dir.is_dir():
                        shutil.rmtree(target_dir)
                    else:
                        target_dir.unlink()
                shutil.copytree(template_dir, target_dir)
                self._cleanup_profile_lock_files(target_dir)
                return
            except OSError as exc:
                last_error = exc
                if attempt >= 2:
                    break
                time.sleep(0.5 * (attempt + 1))

        raise ModelNotFoundError(
            f"Unable to rebuild the ChatGPT runtime profile from template {template_dir}: {last_error}"
        )

    def _cleanup_profile_lock_files(self, profile_dir: Path):
        transient_paths = [
            profile_dir / "SingletonCookie",
            profile_dir / "SingletonLock",
            profile_dir / "SingletonSocket",
            profile_dir / "lockfile",
            profile_dir / "Default" / "LockFile",
            profile_dir / "Default" / "LOCK",
        ]
        for transient_path in transient_paths:
            try:
                if transient_path.is_dir():
                    shutil.rmtree(transient_path, ignore_errors=True)
                else:
                    transient_path.unlink(missing_ok=True)
            except Exception:
                pass

    async def _send_command(self, payload: dict, timeout: int):
        if not self._bridge_process or self._bridge_process.returncode is not None:
            raise self._bridge_error("Browser bridge is not running or has already exited.", delay_seconds=20)

        future = asyncio.get_running_loop().create_future()
        command_id = None

        async with self._bridge_write_lock:
            if not self._bridge_process or self._bridge_process.returncode is not None:
                raise self._bridge_error("Browser bridge is not running or has already exited.", delay_seconds=20)

            self._next_command_id += 1
            command_id = f"cmd-{self._next_command_id}"
            command_payload = dict(payload)
            command_payload["id"] = command_id
            self._pending_commands[command_id] = future

            try:
                data = (json.dumps(command_payload, ensure_ascii=False) + "\n").encode("utf-8")
                self._bridge_process.stdin.write(data)
                await self._bridge_process.stdin.drain()
            except Exception:
                self._pending_commands.pop(command_id, None)
                if not future.done():
                    future.cancel()
                raise

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending_commands.pop(command_id, None)
            if not future.done():
                future.cancel()
            await self._terminate_bridge()
            raise NetworkError(f"ChatGPT Web bridge did not respond within {timeout} seconds.", delay_seconds=30) from exc
        except asyncio.CancelledError:
            self._pending_commands.pop(command_id, None)
            if not future.done():
                future.cancel()
            raise
        except Exception:
            self._pending_commands.pop(command_id, None)
            if self._bridge_process and self._bridge_process.returncode is not None:
                await self._terminate_bridge()
            raise

    async def _drain_stdout(self):
        if not self._bridge_process or not self._bridge_process.stdout:
            return

        process = self._bridge_process
        protocol_error = False

        try:
            while True:
                raw_line = await process.stdout.readline()
                if not raw_line:
                    break

                try:
                    payload = json.loads(raw_line.decode("utf-8").strip())
                except json.JSONDecodeError:
                    protocol_error = True
                    if process.returncode is None:
                        process.kill()
                    break

                command_id = str(payload.get("id", "") or "").strip()
                future = self._pending_commands.pop(command_id, None) if command_id else None
                if future and not future.done():
                    future.set_result(payload)
        except asyncio.CancelledError:
            return
        finally:
            if protocol_error:
                error_message = "Browser bridge returned invalid JSON."
            else:
                return_code = process.returncode
                error_message = "Browser bridge closed stdout without a response."
                if return_code is not None:
                    error_message += f" Exit code: {return_code}."

            pending = list(self._pending_commands.values())
            self._pending_commands.clear()
            for future in pending:
                if not future.done():
                    future.set_exception(self._bridge_error(error_message, delay_seconds=30))

    async def _drain_stderr(self):
        if not self._bridge_process or not self._bridge_process.stderr:
            return

        try:
            while True:
                line = await self._bridge_process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._stderr_tail.append(text)
        except asyncio.CancelledError:
            return

    def _format_stderr_tail(self) -> str:
        if not self._stderr_tail:
            return ""
        return "Bridge stderr:\n" + "\n".join(self._stderr_tail)

    def _bridge_error(self, message: str, delay_seconds: int = 30) -> NetworkError:
        stderr_tail = self._format_stderr_tail()
        if stderr_tail:
            message = f"{message}\n{stderr_tail}"
        return NetworkError(message, delay_seconds=delay_seconds)

    def _raise_bridge_error(self, response: dict, during_init: bool = False):
        code = str(response.get("code", "") or "").strip().lower()
        message = str(response.get("error", "") or "Unknown browser bridge error.").strip()
        stderr_tail = self._format_stderr_tail()
        if stderr_tail:
            message = f"{message}\n{stderr_tail}"

        if code in {"blocked", "cloudflare", "geoblock"}:
            raise LocationBlockedError(message)
        if code in {"rate_limit", "temporary_limit"}:
            raise TemporaryRateLimitError(message, delay_seconds=120)
        if code in {"login_required", "config_error", "init_failed"}:
            raise ModelNotFoundError(message)
        if code in {"echoed_prompt", "invalid_response"}:
            raise ValidationFailedError(message)

        delay_seconds = 20 if during_init else 30
        raise NetworkError(message, delay_seconds=delay_seconds)

    async def _terminate_bridge(self):
        async with self._bridge_state_lock:
            await self._terminate_bridge_locked()

    async def _terminate_bridge_locked(self):
        process = self._bridge_process
        self._bridge_process = None

        stdout_task = self._stdout_task
        self._stdout_task = None

        stderr_task = self._stderr_task
        self._stderr_task = None

        pending = list(self._pending_commands.values())
        self._pending_commands.clear()
        for future in pending:
            if not future.done():
                future.set_exception(NetworkError("Browser bridge terminated.", delay_seconds=20))

        if process and process.returncode is None:
            try:
                if process.stdin and not process.stdin.is_closing():
                    self._next_command_id += 1
                    shutdown_payload = {
                        "type": "shutdown",
                        "id": f"cmd-{self._next_command_id}",
                    }
                    process.stdin.write((json.dumps(shutdown_payload, ensure_ascii=False) + "\n").encode("utf-8"))
                    await process.stdin.drain()
            except Exception:
                pass

            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

        if stdout_task:
            stdout_task.cancel()
            try:
                await stdout_task
            except asyncio.CancelledError:
                pass

        if stderr_task:
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass

    async def _close_thread_session_internal(self):
        await self._terminate_bridge()
