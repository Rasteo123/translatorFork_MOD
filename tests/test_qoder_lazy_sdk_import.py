import subprocess
import sys
import unittest


class QoderLazySdkImportTests(unittest.TestCase):
    def test_importing_handler_module_does_not_load_sdk(self):
        """qoder_agent_sdk тянет пакеты mcp и aiohttp (~250 мс при старте
        приложения); SDK должен импортироваться при первом обращении к
        Qoder-провайдеру, а не при импорте модуля обработчика."""
        code = (
            "import sys\n"
            "import gemini_translator.api.handlers.qoder\n"
            "print('qoder_agent_sdk' in sys.modules)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "False")

    def test_optional_sdk_exports_do_not_block_required_symbols(self):
        code = (
            "from gemini_translator.api.handlers import qoder\n"
            "qoder._ensure_sdk_imported()\n"
            "required = (qoder.QoderAgentOptions, qoder.query, qoder.access_token)\n"
            "print(all(value is not None for value in required))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "True")


if __name__ == "__main__":
    unittest.main()
