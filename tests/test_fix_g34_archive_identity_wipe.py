# -*- coding: utf-8 -*-
"""Регрессия для utils-infra/bugs/1-archive-updater-identity-wipe-.

Хелпер source-архива копирует identity.json в журнал ПОСЛЕ фазы backup()
всех entries/to_remove. Если backup() падает с OSError (диск, права,
антивирус) ДО этого копирования, restore() не находит journal/identity.json
и по ветке `elif os.path.isfile(identity_path): os.remove(identity_path)`
стирает ещё нетронутый валидный .translator-update.json — при том что ни
один файл проекта фактически не был заменён.
"""
import json
import os
import sys

import pytest

# Переиспользуем фикстуру-строитель source-корня/zip/хелпера из основного
# набора тестов апдейтера, а не дублируем её.
from test_update_installer import _archive_env, _OLD  # noqa: E402


@pytest.mark.skipif(os.name == "nt", reason="chmod-имитация недоступна на Windows")
def test_archive_helper_backup_failure_before_identity_journal_keeps_identity(tmp_path):
    ack_writer = ("import json, os\n"
                  "open(os.environ['GT_UPDATE_ACK_FILE'], 'w').write('{}')\n")
    root, staged, helper, ack, journal = _archive_env(tmp_path, ack_writer)

    # Делаем управляемый файл нечитаемым — backup() наткнётся на PermissionError
    # ещё в фазе резервного копирования entries, ДО того как identity.json
    # успеет попасть в журнал (этот шаг в коде идёт позже обоих backup-циклов).
    target = root / "changed.py"
    original_mode = target.stat().st_mode
    target.chmod(0)
    try:
        if os.access(target, os.R_OK):
            pytest.skip("окружение позволяет читать файл несмотря на chmod(0) (вероятно root)")

        import subprocess
        proc = subprocess.run([sys.executable, str(helper)], timeout=60,
                              capture_output=True, text=True)
    finally:
        target.chmod(original_mode)

    assert proc.returncode == 1, proc.stderr
    identity_path = root / ".translator-update.json"
    # Ключевая проверка: сбой на раннем этапе backup() не должен уничтожать
    # валидную идентичность — ни один файл проекта фактически не применён.
    assert identity_path.exists(), (
        "restore() удалил .translator-update.json при сбое ДО копирования "
        "identity.json в журнал: " + proc.stderr
    )
    identity = json.loads(identity_path.read_text())
    assert identity["commit"] == _OLD
    # Реальный откат: новые файлы не должны были появиться.
    assert not (root / "added.py").exists()
