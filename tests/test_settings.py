from bot.settings import (
    DEFAULT_FIELDS, UserSettings, get, render_summary, reset,
    toggle_field, toggle_files,
)

RESULT = {
    "summary": {
        "Модель смартфона": "Pixel 8 Pro",
        "Модуль камеры": "SuperEagle 50MP",
        "Зум": "2× (значение из метаданных)",
        "ISO": "3200",
        "Выдержка": "1/50 с",
        "Дата съёмки": "17.08.2024 10:20:30",
        "Год съёмки": "2024",
        "Имя файла": "IMG_0001.dng",
        "Формат": "DNG",
        "Размер изображения": "8192x6144",
        "Субсэмплирование": "4:4:4",
    }
}

def test_default_summary_is_compact():
    cfg = UserSettings()
    text = render_summary(RESULT, cfg)
    assert "Pixel 8 Pro" in text and "1/50" in text
    # Fields outside the default set stay hidden.
    assert "IMG_0001" not in text and "8192x6144" not in text
    assert len(text.splitlines()) == len(DEFAULT_FIELDS)

def test_undetermined_fields_are_skipped():
    cfg = UserSettings()
    sparse = {"summary": dict(RESULT["summary"], **{"Зум": "Не определено (поле зума отсутствует)"})}
    assert "Зум" not in render_summary(sparse, cfg)

def test_toggle_field_adds_and_removes():
    uid = 111
    reset(uid)
    assert toggle_field(uid, "filename") is True
    assert "IMG_0001" in render_summary(RESULT, get(uid))
    assert toggle_field(uid, "filename") is True
    assert "IMG_0001" not in render_summary(RESULT, get(uid))

def test_last_visible_field_cannot_be_removed():
    uid = 222
    reset(uid)
    cfg = get(uid)
    cfg.fields = {"iso"}
    assert toggle_field(uid, "iso") is False
    assert cfg.fields == {"iso"}

def test_unknown_field_is_rejected():
    reset(333)
    assert toggle_field(333, "nope") is False

def test_files_default_off_and_toggleable():
    uid = 444
    reset(uid)
    assert get(uid).files is False
    assert toggle_files(uid) is True
    assert get(uid).files is True
    assert toggle_files(uid) is False

def test_reset_restores_defaults():
    uid = 555
    cfg = get(uid)
    cfg.fields.add("size")
    cfg.files = True
    reset(uid)
    assert get(uid).fields == set(DEFAULT_FIELDS)
    assert get(uid).files is False

def test_placeholder_prose_is_hidden_in_card():
    from bot.settings import UserSettings, render_summary

    sparse = {"summary": {"Модуль камеры": "Название модуля отсутствует; фокусное: 8.7 mm",
                          "ISO": "1499"}}
    cfg = UserSettings(fields={"module", "iso"})
    text = render_summary(sparse, cfg)
    assert "1499" in text and "отсутствует" not in text
