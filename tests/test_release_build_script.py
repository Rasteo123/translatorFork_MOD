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
    }

    previous = {name: sys.modules.get(name) for name in modules}
    try:
        sys.modules.update(modules)
        spec_path = PROJECT_ROOT / spec_name
        exec(compile(spec_path.read_text(encoding="utf-8"), spec_path, "exec"), globals_dict)
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

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


def test_release_specs_package_lazy_gemini_handler():
    module_name = "gemini_translator.api.handlers.gemini"

    assert module_name in _load_spec_hiddenimports("translatorFork-translator-only.spec")
    assert module_name in _load_spec_hiddenimports("translatorFork-full.spec")
