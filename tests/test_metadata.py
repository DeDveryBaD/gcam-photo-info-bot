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

def test_utc_exif_is_reconciled_with_filename():
    from bot.metadata import reconcile_capture_time

    display, year, note = reconcile_capture_time(
        "2026:10:20 14:57:17", {}, "IMG_20261020_195717_FCam_X.jpg")
    assert display.startswith("20.10.2026 19:57:17") and year == "2026"
    assert "+5" in note

def test_matching_times_get_no_note():
    from bot.metadata import reconcile_capture_time

    display, _, note = reconcile_capture_time(
        "2026:10:20 19:57:17", {}, "IMG_20261020_195717_FCam_X.jpg")
    assert note is None and display.endswith("19:57:17")

def test_fractional_drift_is_reported_not_silently_fixed():
    from bot.metadata import reconcile_capture_time

    _, _, note = reconcile_capture_time(
        "2026:10:20 14:20:00", {}, "IMG_20261020_195717_FCam_X.jpg")
    assert "не на целые часы" in note

def test_offset_tag_is_surfaced():
    from bot.metadata import reconcile_capture_time

    _, _, note = reconcile_capture_time(
        "2026:10:20 19:57:17", {"ExifIFD:OffsetTimeOriginal": "+05:00"}, "x.jpg")
    assert "+5" in note and "локальное" in note

def test_no_date_means_no_reconciliation():
    from bot.metadata import reconcile_capture_time

    display, year, note = reconcile_capture_time(None, {}, "IMG_20261020_195717.jpg")
    assert display == "Не определено" and note is None
