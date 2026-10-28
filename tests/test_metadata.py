from bot.metadata import _display_zoom, _normalise_date, _human_shutter, _module

def test_date_extracts_year():
    assert _normalise_date("2024:08:17 10:20:30")[1] == "2024"

def test_missing_date_is_explicit():
    assert _normalise_date(None) == ("Не определено", "Не определён")

def test_zoom_not_guessed_from_focal_length():
    value = _display_zoom({"EXIF:FocalLength": "6.9 mm"})
    assert "не записан" in value.lower()

def test_explicit_zoom_is_reported():
    assert "2×" in _display_zoom({"EXIF:DigitalZoomRatio": 2})

def test_module_not_guessed():
    assert "Не определено" in _module({})

def test_shutter_readable():
    assert "1/250" in _human_shutter("1/250", {})
