from pathlib import Path
import sys
import types


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_spec_hiddenimports(spec_name):
    captured = {}

    def analysis(*args, **kwargs):
        captured["hiddenimports"] = kwargs.get("hiddenimports", [])
        return types.SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])

    hooks_module = types.ModuleType("PyInstaller.utils.hooks")
    hooks_module.collect_data_files = lambda _package: []
    modules = {
        "PyInstaller": types.ModuleType("PyInstaller"),
        "PyInstaller.utils": types.ModuleType("PyInstaller.utils"),
        "PyInstaller.utils.hooks": hooks_module,
    }
    globals_dict = {
        "Analysis": analysis,
        "PYZ": lambda *_args, **_kwargs: object(),
        "EXE": lambda *_args, **_kwargs: object(),
        "COLLECT": lambda *_args, **_kwargs: object(),
        "BUNDLE": lambda *_args, **_kwargs: object(),
        "SPECPATH": str(PROJECT_ROOT),
    }

    previous = {name: sys.modules.get(name) for name in modules}
    previous_build_config = sys.modules.pop("pyinstaller_config", None)
    previous_sys_path = list(sys.path)
    try:
        sys.modules.update(modules)
        sys.path[:] = [
            entry
            for entry in sys.path
            if Path(entry or ".").resolve() != PROJECT_ROOT
        ]
        spec_path = PROJECT_ROOT / spec_name
        try:
            exec(
                compile(spec_path.read_text(encoding="utf-8"), spec_path, "exec"),
                globals_dict,
            )
        except ModuleNotFoundError as error:
            raise AssertionError(
                f"{spec_name} must load build helpers through SPECPATH: {error}"
            ) from error
    finally:
        sys.path[:] = previous_sys_path
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if previous_build_config is None:
            sys.modules.pop("pyinstaller_config", None)
        else:
            sys.modules["pyinstaller_config"] = previous_build_config

    return captured["hiddenimports"]


def test_dual_release_script_packages_onedir_outputs():
    script = (PROJECT_ROOT / "build_release_dual.bat").read_text(encoding="utf-8")

    assert 'call :finish_build "dist\\translatorFork-translator"' in script
    assert 'call :finish_build "dist\\translatorFork-full"' in script
    assert 'call :finish_build "dist\\translatorFork-translator.exe"' not in script
    assert 'call :finish_build "dist\\translatorFork-full.exe"' not in script

    assert "translatorFork-translator-v%RELEASE_VERSION%-windows.zip" in script
    assert "translatorFork-full-v%RELEASE_VERSION%-windows.zip" in script
    assert "translatorFork_MOD-source-v%RELEASE_VERSION%.zip" in script
    assert "SHA256SUMS.txt" in script
    assert "git archive --format=zip" in script


def test_release_specs_package_every_lazy_api_module():
    from gemini_translator.api import handlers, servers

    handler_modules = {
        f"{handlers.__name__}{module_path}"
        for module_path in handlers._LAZY_HANDLER_MODULES.values()
    }
    server_modules = {
        f"{servers.__name__}{module_path}"
        for module_path in servers._LAZY_SERVER_MODULES.values()
    }

    ci_release = set(_load_spec_hiddenimports("translatorFork_MOD.spec"))
    full = set(_load_spec_hiddenimports("translatorFork-full.spec"))
    translator_only = set(
        _load_spec_hiddenimports("translatorFork-translator-only.spec")
    )

    assert handler_modules <= translator_only
    assert handler_modules | server_modules <= full
    assert handler_modules | server_modules <= ci_release
