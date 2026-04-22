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
    app_main.prepare_console_streams()
    sys.excepthook = app_main.global_excepthook

    import threading

    main_id = threading.get_ident()
    print(f"\n[SYSTEM] MAIN UI THREAD ID: {main_id}\n")
    app_main.os_patch.PatientLock.register_vip_thread(main_id)

    app = app_main.ApplicationWithContext(sys.argv)
    app_main.install_window_title_branding(app)
    app.setStyleSheet(app_main.DARK_STYLESHEET)

    if sys.platform == "win32":
        app_main.asyncio.set_event_loop_policy(app_main.asyncio.WindowsSelectorEventLoopPolicy())

    app_main.initialize_global_resources(app)
    app_main.os_patch.apply()
    app_main.api_config.initialize_configs()

    print("[INFO] Initializing translator-only application services...")

    app.event_bus = app_main.EventBus()
    app.initialize_managers()
    app.settings_manager = app.get_settings_manager()
    app_main.apply_saved_app_theme(app, app.settings_manager)
    app.task_manager = app_main.ChapterQueueManager(event_bus=app.event_bus)
    app.global_version = app_main.APP_VERSION
    app.proxy_controller = app_main.GlobalProxyController(app.event_bus)
    app.settings_manager.load_proxy_settings()

    temp_folder = os.path.join(os.path.expanduser("~"), ".epub_translator_temp")
    os.makedirs(temp_folder, exist_ok=True)
    app.context_manager = app_main.ContextManager(temp_folder)
    app.server_manager = app_main.ServerManager(app.event_bus)

    print("[INFO] Initializing TranslationEngine...")
    app.engine = app_main.TranslationEngine(task_manager=app.task_manager)
    app.engine_thread = app_main.QtCore.QThread(app)
    app.engine.moveToThread(app.engine_thread)
    app.engine_thread.finished.connect(app.engine.deleteLater)
    app.engine_thread.start()

    print("[OK] TranslationEngine started in background thread.")
    app_main.QtCore.QMetaObject.invokeMethod(
        app.engine,
        "log_thread_identity",
        app_main.QtCore.Qt.ConnectionType.QueuedConnection,
    )

    try:
        import jieba

        print("[INFO] Warming up jieba dictionary...")
        jieba.lcut("прогрев", cut_all=False)
    except (ImportError, Exception) as error:
        print(f"[WARN] Could not warm up jieba dictionary: {error}")

    return app


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
        if hasattr(app, "engine_thread") and app.engine_thread.isRunning():
            app.engine_thread.quit()
            app.engine_thread.wait()

    return 0


if __name__ == "__main__":
    sys.exit(run_translator_only())
