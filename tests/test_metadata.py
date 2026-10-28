import pytest

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

def test_lone_offset_is_not_reported_as_discrepancy():
    """OffsetTime confirming local time is the normal case: silence, not a note."""
    from bot.metadata import reconcile_capture_time

    _, _, note = reconcile_capture_time(
        "2026:10:20 19:57:17", {"ExifIFD:OffsetTimeOriginal": "+05:00"}, "x.jpg")
    assert note is None

def test_no_date_means_no_reconciliation():
    from bot.metadata import reconcile_capture_time

    display, year, note = reconcile_capture_time(None, {}, "IMG_20261020_195717.jpg")
    assert display == "Не определено" and note is None


def test_system_file_date_never_wins_over_exif():
    """Regression: the 5-hour bug. exiftool prints System:FileModifyDate first,
    and an unordered scan returned the server download time instead of capture."""
    from bot.metadata import _lookup, DATE_TAGS

    tags = {
        "System:FileModifyDate": "2026:10:28 14:14:16",
        "IFD0:ModifyDate": "2026:10:28 19:13:37",
        "ExifIFD:DateTimeOriginal": "2026:10:28 19:13:37",
    }
    value, source = _lookup(tags, DATE_TAGS)
    assert source == "ExifIFD:DateTimeOriginal"
    assert value == "2026:10:28 19:13:37"

def test_spoofed_make_is_not_merged_into_model():
    from bot.metadata import _model

    display, note = _model({"IFD0:Make": "Google", "IFD0:Model": "V2366GA"})
    assert display == "V2366GA"
    assert "подменяет" in note

def test_google_make_with_pixel_model_is_clean():
    from bot.metadata import _model

    display, note = _model({"IFD0:Make": "Google", "IFD0:Model": "Pixel 8 Pro"})
    assert display == "Pixel 8 Pro" and note is None

def test_honest_make_is_still_merged():
    from bot.metadata import _model

    display, note = _model({"IFD0:Make": "vivo", "IFD0:Model": "V2366GA"})
    assert display == "vivo V2366GA" and note is None

def test_composite_imagesize_preferred_over_width():
    from bot.metadata import _lookup

    tags = {"ExifIFD:ImageWidth": 2304, "Composite:ImageSize": "2304x4096"}
    value, source = _lookup(tags, ("ImageSize",), exclude_groups=())
    assert value == "2304x4096"

@pytest.mark.parametrize("software,expected", [
    ("HDR+ 1.0.388784762zd", "HDR+"),
    ("Arnova8G2 8.8.0", "Arnova8G2"),
    ("GCam 9.2", "GCam"),
    ("Google", None),
    ("2.1.0", None),
])
def test_mod_extracted_from_software_tag(software, expected):
    from bot.metadata import mod_from_software

    assert mod_from_software({"IFD0:Software": software}) == expected

def test_subsampling_from_ycbcr_tag():
    from bot.metadata import _subsampling

    assert _subsampling({"File:YCbCrSubSampling": "YCbCr4:4:4 (1 1)"}) == "4:4:4"
    assert _subsampling({"File:YCbCrSubSampling": "YCbCr4:2:0 (2 1)"}) == "4:2:0"

def test_subsampling_falls_back_to_image_description():
    from bot.metadata import _subsampling

    tags = {"IFD0:ImageDescription": "sRGB.YCbCr 4:4:4.Zoom: 1.05x"}
    assert _subsampling(tags) == "4:4:4"

def test_subsampling_undefined_for_raw():
    from bot.metadata import _subsampling

    assert _subsampling({"ExifIFD:Compression": "Lossless (1)"}).startswith("Не определено")

def test_subsampling_space_separated_form():
    from bot.metadata import _subsampling

    assert _subsampling({"ExifIFD:YCbCrSubSampling": "4 2 0"}) == "4:2:0"
