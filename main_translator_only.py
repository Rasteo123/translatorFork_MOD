# -*- coding: utf-8 -*-
import os
import sys
import traceback


TRANSLATOR_ONLY_DISABLED_PROVIDER_IDS = (
    "workascii_chatgpt",
    "web_chatgpt_free",
    "web_perplexity",
)


def _merge_csv_env_values(env_name: str, values: tuple[str, ...]) -> None:
    existing_values = {
        item.strip()
        for item in str(os.environ.get(env_name, "") or "").split(",")
        if item.strip()
    }
    existing_values.update(values)
    os.environ[env_name] = ",".join(sorted(existing_values))


os.environ["GT_TRANSLATOR_ONLY_MODE"] = "1"
_merge_csv_env_values("GT_DISABLED_PROVIDER_IDS", TRANSLATOR_ONLY_DISABLED_PROVIDER_IDS)

import main as app_main  # noqa: E402


def _bootstrap_application():
    """Тонкая обёртка над общим bootstrap'ом main.py (translator-only режим).

    Оставлена как отдельное имя, потому что tests/test_main_translator_only_shutdown.py
    подменяет её через mock.patch.object.
    """
    return app_main.bootstrap_application(sys.argv, translator_only=True)


def _create_translator_window():
    return app_main.InitialSetupDialog()


def run_translator_only():
    app = _bootstrap_application()

    try:
        while True:
            main_window_to_run = None
            loading_dialog = app_main.LoadingDialog()

            try:
                main_window_to_run = _create_translator_window()
            except Exception as error:
                if loading_dialog.isVisible():
                    loading_dialog.close()

                tb_str = "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                )
                error_message = (
                    f"Произошла критическая ошибка при инициализации переводчика: "
                    f"{type(error).__name__}\n\n--- Полный Traceback ---\n{tb_str}"
                )
                print(f"[CRITICAL STARTUP ERROR]\n{error_message}")
                app_main.QtWidgets.QMessageBox.critical(
                    None, "Ошибка запуска", error_message
                )
                main_window_to_run = None

            if main_window_to_run:
                main_window_to_run.show()
                exit_code = app.exec()
                if exit_code != app_main.EXIT_CODE_REBOOT:
                    break
            else:
                break
    finally:
        print("[INFO] Translator-only application is shutting down.")
        if hasattr(app, "proxy_controller"):
            app.proxy_controller.shutdown()
        if hasattr(app, "engine_thread") and app.engine_thread.isRunning():
            if hasattr(app, "engine"):
                try:
                    app_main.QtCore.QMetaObject.invokeMethod(
                        app.engine,
                        "cleanup",
                        app_main.QtCore.Qt.ConnectionType.BlockingQueuedConnection,
                    )
                except Exception:
                    pass
            app.engine_thread.quit()
            app.engine_thread.wait()

    return 0


if __name__ == "__main__":
    sys.exit(run_translator_only())
