# -*- coding: utf-8 -*-
"""
root-entry/design/9-os-patch-retry-logic-triplicat.

_patched_open, _patched_remove и _patched_rename (нативная ветка, реальные
пути) независимо реализовывали "терпеливый" ретрай на блокировку файла с
уже разошедшимися правилами классификации ошибки:

- _patched_open:   текст ("used by another process"/"sharing violation"/
                    "lock") ИЛИ errno == 13; плюс отдельный фатальный кейс
                    (PermissionError на директории — ретраить бесполезно).
- _patched_remove: НИКАКОЙ проверки — ретраил любой OSError/PermissionError.
- _patched_rename: текст ("used by another process"/"sharing violation")
                    ИЛИ errno in (13, 32).
- _patched_replace: (найдено ревью на раунде 2) четвёртая независимая копия
                    того же цикла — текст ("used by another process"/
                    "sharing violation") ИЛИ e.errno in (13, 32) напрямую
                    (без getattr), MAX_RETRIES=7, base_delay=0.25.

Канонической реализацией стали:
- os_patch._is_transient_io_error(exc)      — единый предикат классификации
  (объединение всех критериев, которые раньше проверялись хоть где-то:
  текст + errno in (13, 32, 16-EBUSY));
- os_patch._retry_on_transient_io_error(...) — общий цикл ретрая, вызываемый
  из всех ЧЕТЫРЁХ мест с их же исходными max_retries/base_delay.

behavior_choice: _patched_remove теперь тоже классифицирует ошибку (раньше
ретраил вообще всё, включая, например, FileNotFoundError — бессмысленное
ожидание при заведомо не-временной ошибке). Это единственное реальное
изменение поведения; оно зафиксировано тестом
test_patched_remove_now_classifies_error_like_open_and_rename ниже.

Раунд 2 (по замечаниям ревью):
- _patched_replace переведён на общий хелпер (был пропущен в раунде 1).
- Маркер "lock" в _is_transient_io_error сужен до целого слова
  ("lock"/"locked" по границе слова, не считая случай, когда слову
  предшествует точка — т.е. не путается с "*.lock" в пути к файлу) —
  чтобы не ловить "Block device required" и не путать имя файла с
  реальной блокировкой.
- errno 16 (EBUSY, "Device or resource busy") добавлен в список
  транзиентных — раньше _patched_remove его пережидал (ретраил всё без
  разбора), после унификации на предикат без EBUSY это поведение было бы
  потеряно молча.
- _retry_on_transient_io_error резолвит предикат по умолчанию через
  модульный глобал os_patch._is_transient_io_error в момент ВЫЗОВА
  (не в момент определения функции) — подмена глобала в тесте теперь
  реально влияет на поведение.

(а) Характеризационные тесты фиксируют поведение канонической реализации.
(б) Тест-маршрутизация (test_all_four_callsites_route_through_shared_retry_helper)
    проверяет, что все четыре места вызова реально идут через общий хелпер —
    до рефакторинга раунда 2 _patched_replace его не использовал вовсе.
"""

import pytest

import os_patch


# ---------------------------------------------------------------------------
# (а) Характеризационные тесты: _is_transient_io_error (единый предиктат)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message, errno_val",
    [
        ("File is used by another process", None),
        ("USED BY ANOTHER PROCESS (upper-case)", None),
        ("Sharing violation on file", None),
        ("SHARING VIOLATION", None),
        ("some generic lock detected", None),
        ("Permission denied", 13),
        ("[WinError 32] The process cannot access the file", 32),
        ("weird error with no matching text", 13),
        ("weird error with no matching text", 32),
    ],
)
def test_is_transient_io_error_true_for_locking_style_errors(message, errno_val):
    exc = OSError(message)
    if errno_val is not None:
        exc.errno = errno_val
    assert os_patch._is_transient_io_error(exc) is True


@pytest.mark.parametrize(
    "message, errno_val",
    [
        ("No such file or directory", 2),
        ("Is a directory", 21),
        ("completely unrelated failure", None),
    ],
)
def test_is_transient_io_error_false_for_non_locking_errors(message, errno_val):
    exc = OSError(message)
    if errno_val is not None:
        exc.errno = errno_val
    assert os_patch._is_transient_io_error(exc) is False


def test_is_transient_io_error_true_for_ebusy():
    """Ревью, раунд 2 (minor): errno 16 (EBUSY, 'Device or resource busy')
    раньше пережидался _patched_remove (он ретраил всё без разбора). После
    унификации на предикат EBUSY должен остаться транзиентным явно, а не
    потеряться молча."""
    exc = OSError("Device or resource busy")
    exc.errno = 16
    assert os_patch._is_transient_io_error(exc) is True


@pytest.mark.parametrize(
    "message",
    [
        "Block device required",
        "BLOCK DEVICE REQUIRED",
        "[Errno 2] No such file or directory: 'archive.lock'",
        "No such file or directory: '/tmp/session.lock'",
    ],
)
def test_is_transient_io_error_false_for_lock_substring_false_positives(message):
    """Ревью, раунд 2 (minor): маркер 'lock' был голой подстрокой и ловил
    'Block device required' (подстрока 'lock' внутри 'Block') и сообщения
    об отсутствующем файле, чьё ИМЯ оканчивается на '.lock' (например,
    FileNotFoundError над сторожевым файлом *.lock) — то самое бессмысленное
    ожидание, которое behavior_choice декларировал как устранённое."""
    exc = OSError(message)
    assert os_patch._is_transient_io_error(exc) is False


@pytest.mark.parametrize(
    "message",
    [
        "some generic lock detected",
        "file is locked by another user",
        "Resource is locked",
        "lock held",
    ],
)
def test_is_transient_io_error_true_for_lock_as_whole_word(message):
    """Сужение маркера не должно ломать реальные сообщения о блокировке,
    где 'lock'/'locked' — самостоятельное слово, не суффикс имени файла."""
    exc = OSError(message)
    assert os_patch._is_transient_io_error(exc) is True


# ---------------------------------------------------------------------------
# (а) Характеризационные тесты: _retry_on_transient_io_error (общий цикл)
# ---------------------------------------------------------------------------

def test_retry_helper_returns_immediately_on_success():
    calls = []

    def func():
        calls.append(1)
        return "ok"

    result = os_patch._retry_on_transient_io_error(func, max_retries=5, base_delay=0.25)
    assert result == "ok"
    assert len(calls) == 1


def test_retry_helper_retries_transient_error_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    attempts = {"n": 0}

    def func():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("file used by another process")
        return "ok"

    result = os_patch._retry_on_transient_io_error(func, max_retries=5, base_delay=0.25)
    assert result == "ok"
    assert attempts["n"] == 3
    # Ждали перед 2-й и 3-й попыткой: 0.25*1, 0.25*2
    assert sleeps == [0.25, 0.5]


def test_retry_helper_raises_immediately_on_non_transient_error(monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    attempts = {"n": 0}

    def func():
        attempts["n"] += 1
        raise FileNotFoundError("No such file or directory")

    with pytest.raises(FileNotFoundError):
        os_patch._retry_on_transient_io_error(func, max_retries=5, base_delay=0.25)

    assert attempts["n"] == 1, "не-временная ошибка не должна ретраиться"
    assert sleeps == []


def test_retry_helper_gives_up_after_max_retries_even_if_transient(monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    attempts = {"n": 0}

    def func():
        attempts["n"] += 1
        raise OSError("sharing violation")

    with pytest.raises(OSError):
        os_patch._retry_on_transient_io_error(func, max_retries=3, base_delay=0.1)

    assert attempts["n"] == 3
    assert sleeps == [0.1, 0.2]


def test_retry_helper_invokes_on_retry_callback_with_attempt_and_wait_time(monkeypatch):
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: None)

    attempts = {"n": 0}
    seen = []

    def func():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise OSError("lock held")
        return "ok"

    def on_retry(attempt, wait_time, exc):
        seen.append((attempt, wait_time, str(exc)))

    os_patch._retry_on_transient_io_error(
        func, max_retries=5, base_delay=0.25, on_retry=on_retry
    )
    assert seen == [(0, 0.25, "lock held")]


def test_retry_helper_default_predicate_resolves_module_global_at_call_time(monkeypatch):
    """Ревью, раунд 2 (minor): `is_transient=_is_transient_io_error` в
    сигнатуре связывался в момент ОПРЕДЕЛЕНИЯ функции — подмена
    os_patch._is_transient_io_error (monkeypatch, как это принято делать в
    этом же файле) не должна была влиять на _retry_on_transient_io_error.
    После фикса предикат по умолчанию резолвится через модульный глобал
    в момент вызова."""
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: None)
    monkeypatch.setattr(os_patch, "_is_transient_io_error", lambda e: False)

    attempts = {"n": 0}

    def func():
        attempts["n"] += 1
        # Сообщение, которое реальный os_patch._is_transient_io_error
        # классифицировал бы как транзиентное.
        raise OSError("used by another process")

    with pytest.raises(OSError):
        os_patch._retry_on_transient_io_error(func, max_retries=5, base_delay=0.25)

    assert attempts["n"] == 1, (
        "подменённый модульный предикат должен был запретить ретрай, "
        "но хелпер продолжал использовать старую связанную ссылку"
    )


def test_retry_helper_custom_is_transient_predicate_overrides_default(monkeypatch):
    """_patched_open использует именно это: свой предикат, который сверху
    добавляет фатальный кейс "PermissionError на директории"."""
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: None)

    attempts = {"n": 0}

    def func():
        attempts["n"] += 1
        raise PermissionError("denied")

    with pytest.raises(PermissionError):
        os_patch._retry_on_transient_io_error(
            func, max_retries=5, base_delay=0.25, is_transient=lambda e: False
        )
    assert attempts["n"] == 1


# ---------------------------------------------------------------------------
# (а) Характеризационные тесты трёх мест вызова через реальные функции
# ---------------------------------------------------------------------------

def test_patched_open_retries_locking_error_then_succeeds(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    target = tmp_path / "file.txt"
    target.write_text("hello", encoding="utf-8")

    calls = {"n": 0}
    real_open = os_patch._original["open"]

    def flaky_open(path, mode="r", *args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("[Errno 13] used by another process")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setitem(os_patch._original, "open", flaky_open)

    f = os_patch._patched_open(str(target), "r")
    try:
        assert f.read() == "hello"
    finally:
        f.close()
    assert calls["n"] == 3
    assert sleeps == [0.25, 0.5]


def test_patched_open_permission_denied_on_directory_is_fatal_not_retried(tmp_path, monkeypatch):
    """Открытие директории как файла — фатальная ошибка, ретраить бесполезно
    (это правило появилось только в _patched_open и должно сохраниться)."""
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    a_directory = tmp_path / "some_dir"
    a_directory.mkdir()

    calls = {"n": 0}

    def always_fails(path, mode="r", *args, **kwargs):
        calls["n"] += 1
        raise PermissionError("denied")

    monkeypatch.setitem(os_patch._original, "open", always_fails)

    with pytest.raises(PermissionError):
        os_patch._patched_open(str(a_directory), "r")

    assert calls["n"] == 1, "не должно быть ретраев для директории"
    assert sleeps == []


def test_patched_open_max_retries_is_5_with_step_0_25(monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    def always_fails(path, mode="r", *args, **kwargs):
        raise OSError("used by another process")

    monkeypatch.setitem(os_patch._original, "open", always_fails)

    with pytest.raises(OSError):
        os_patch._patched_open("some/fake/path.txt", "r")

    assert sleeps == [0.25, 0.5, 0.75, 1.0]


def test_patched_remove_retries_locking_error_then_succeeds(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")

    calls = {"n": 0}
    real_remove = os_patch._original["remove"]

    def flaky_remove(path):
        calls["n"] += 1
        if calls["n"] < 2:
            raise OSError("sharing violation")
        return real_remove(path)

    monkeypatch.setitem(os_patch._original, "remove", flaky_remove)

    os_patch._patched_remove(str(target))
    assert not target.exists()
    assert calls["n"] == 2
    assert sleeps == [0.2]


def test_patched_remove_now_classifies_error_like_open_and_rename(monkeypatch):
    """behavior_choice: раньше _patched_remove ретраил ЛЮБОЙ OSError без
    проверки текста/errno. После унификации на общий предикат не-временная
    ошибка (например FileNotFoundError) пробрасывается немедленно, без
    бессмысленного ожидания."""
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def always_not_found(path):
        calls["n"] += 1
        raise FileNotFoundError("No such file or directory")

    monkeypatch.setitem(os_patch._original, "remove", always_not_found)

    with pytest.raises(FileNotFoundError):
        os_patch._patched_remove("does/not/exist.txt")

    assert calls["n"] == 1, "не-временная ошибка не должна ретраиться"
    assert sleeps == []


def test_patched_remove_max_retries_is_5_with_step_0_2(monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    def always_fails(path):
        raise OSError("used by another process")

    monkeypatch.setitem(os_patch._original, "remove", always_fails)

    with pytest.raises(OSError):
        os_patch._patched_remove("some/fake/path.txt")

    assert sleeps == pytest.approx([0.2, 0.4, 0.6, 0.8])


def test_patched_rename_retries_errno_32_then_succeeds(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("x", encoding="utf-8")

    calls = {"n": 0}
    real_rename = os_patch._original["rename"]

    def flaky_rename(s, d):
        calls["n"] += 1
        if calls["n"] < 2:
            err = OSError("some exotic message")
            err.errno = 32
            raise err
        return real_rename(s, d)

    monkeypatch.setitem(os_patch._original, "rename", flaky_rename)

    os_patch._patched_rename(str(src), str(dst))
    assert dst.exists()
    assert calls["n"] == 2
    assert sleeps == [0.25]


def test_patched_rename_max_retries_is_7_with_step_0_25(monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    def always_fails(s, d):
        raise OSError("sharing violation")

    monkeypatch.setitem(os_patch._original, "rename", always_fails)

    with pytest.raises(OSError):
        os_patch._patched_rename("a.txt", "b.txt")

    assert sleeps == [0.25, 0.5, 0.75, 1.0, 1.25, 1.5]


def test_patched_rename_non_transient_error_raised_immediately(monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def always_fails(s, d):
        calls["n"] += 1
        err = OSError("some totally unrelated failure")
        err.errno = 5  # EIO, не в списке транзиентных
        raise err

    monkeypatch.setitem(os_patch._original, "rename", always_fails)

    with pytest.raises(OSError):
        os_patch._patched_rename("a.txt", "b.txt")

    assert calls["n"] == 1
    assert sleeps == []


def test_patched_replace_retries_locking_error_then_succeeds(tmp_path, monkeypatch):
    """Ревью, раунд 2 (major): _patched_replace был четвёртой независимой
    копией того же цикла ретрая — теперь должен идти через тот же общий
    хелпер, что и open/remove/rename."""
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("x", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")

    calls = {"n": 0}
    real_replace = os_patch._original["replace"]

    def flaky_replace(s, d):
        calls["n"] += 1
        if calls["n"] < 2:
            raise OSError("sharing violation")
        return real_replace(s, d)

    monkeypatch.setitem(os_patch._original, "replace", flaky_replace)

    os_patch._patched_replace(str(src), str(dst))
    assert dst.read_text(encoding="utf-8") == "x"
    assert calls["n"] == 2
    assert sleeps == [0.25]


def test_patched_replace_max_retries_is_7_with_step_0_25(monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    def always_fails(s, d):
        raise OSError("used by another process")

    monkeypatch.setitem(os_patch._original, "replace", always_fails)

    with pytest.raises(OSError):
        os_patch._patched_replace("a.txt", "b.txt")

    assert sleeps == [0.25, 0.5, 0.75, 1.0, 1.25, 1.5]


def test_patched_replace_non_transient_error_raised_immediately(monkeypatch):
    sleeps = []
    monkeypatch.setattr(os_patch.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def always_fails(s, d):
        calls["n"] += 1
        err = OSError("some totally unrelated failure")
        err.errno = 5  # EIO, не в списке транзиентных
        raise err

    monkeypatch.setitem(os_patch._original, "replace", always_fails)

    with pytest.raises(OSError):
        os_patch._patched_replace("a.txt", "b.txt")

    assert calls["n"] == 1
    assert sleeps == []


# ---------------------------------------------------------------------------
# (б) Тест-маршрутизация: все четыре места вызова идут через общий хелпер
# ---------------------------------------------------------------------------

def test_all_four_callsites_route_through_shared_retry_helper(tmp_path, monkeypatch):
    """Подменяет os_patch._retry_on_transient_io_error и проверяет, что
    _patched_open, _patched_remove, _patched_rename и _patched_replace
    реально его вызывают (со своими исходными max_retries/base_delay), а не
    крутят свою копию цикла. ДО рефакторинга раунда 2 _patched_replace его
    не вызывал вовсе."""
    calls = []

    def fake_retry(func, *, max_retries, base_delay, is_transient=None, on_retry=None):
        calls.append({"max_retries": max_retries, "base_delay": base_delay})
        return func()

    monkeypatch.setattr(
        os_patch, "_retry_on_transient_io_error", fake_retry, raising=False
    )

    target = tmp_path / "a.txt"
    target.write_text("hi", encoding="utf-8")
    with os_patch._patched_open(str(target), "r") as f:
        f.read()

    dst = tmp_path / "b.txt"
    os_patch._patched_rename(str(target), str(dst))

    dst2 = tmp_path / "c.txt"
    dst2.write_text("old", encoding="utf-8")
    os_patch._patched_replace(str(dst), str(dst2))

    os_patch._patched_remove(str(dst2))

    assert len(calls) == 4, (
        "ожидались вызовы общего хелпера из _patched_open, _patched_rename, "
        f"_patched_replace и _patched_remove, а зафиксировано: {calls}"
    )
    by_max_retries = sorted(c["max_retries"] for c in calls)
    assert by_max_retries == [5, 5, 7, 7], calls

    open_call = next(c for c in calls if c["max_retries"] == 5 and c["base_delay"] == 0.25)
    remove_call = next(c for c in calls if c["max_retries"] == 5 and c["base_delay"] == 0.2)
    seven_calls = [c for c in calls if c["max_retries"] == 7]
    assert open_call is not None
    assert remove_call is not None
    assert len(seven_calls) == 2
    assert all(c["base_delay"] == 0.25 for c in seven_calls)
