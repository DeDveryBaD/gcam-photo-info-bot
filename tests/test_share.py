from pathlib import Path

import pytest

from bot.preview import build_preview
from bot.share import (
    Draft, card_caption, clean_model, parse_filename, publish_caption,
)

def test_mod_and_config_split_on_first_underscore():
    mod, cfg, edited = parse_filename("IMG_20261020_195717_FCam_VIBeX6_TEST.jpg")
    assert (mod, cfg, edited) == ("FCam", "VIBeX6 TEST", False)

def test_multiword_mod_keeps_rest_as_config():
    mod, cfg, _ = parse_filename("IMG_20261020_195717_Arnova8G2_UltraMF.config_v2.dng")
    assert mod == "Arnova8G2" and cfg == "UltraMF.config v2"

def test_plain_gcam_name_has_no_config():
    mod, cfg, edited = parse_filename("IMG_20261020_195717_BSG.jpg")
    assert mod == "BSG" and cfg is None and edited is False

def test_edited_marker_is_detected():
    _, _, edited = parse_filename("IMG_20261020_195717_FCam_VIBeX6_EDIT.jpg")
    assert edited is True

def test_unparsable_name_is_not_guessed():
    mod, cfg, edited = parse_filename("IMG_20240817_102030.jpg")
    assert mod is None and cfg is None and edited is False

def test_empty_filename_safe():
    assert parse_filename("") == (None, None, False)

def test_brand_stripped_from_model():
    assert clean_model("Xiaomi Mi 10T Pro") == "Mi 10T Pro"
    assert clean_model("Pixel 8 Pro") == "Pixel 8 Pro"

def test_card_caption_four_lines():
    draft = Draft(device="Mi 10T Pro", mod="FCam", config="VIBeX6 TEST", edited=False)
    text = card_caption(draft)
    assert "Mi 10T Pro" in text and "FCam" in text and "No edit" in text
    assert "Edit" not in text.replace("No edit", "")

def test_publish_caption_has_no_counters():
    draft = Draft(device="Mi 10T Pro", mod="FCam", config="VIBeX6 TEST", edited=True)
    text = publish_caption(draft)
    assert text.count("\n") == 3 and "Edit" in text


@pytest.mark.parametrize("suffix,factory", [
    (".jpg", "jpeg"), (".png", "png"), (".webp", "webp"),
])
def test_preview_downscales_raster(tmp_path: Path, suffix: str, factory: str):
    from PIL import Image

    src = tmp_path / f"big{suffix}"
    Image.new("RGB", (4000, 3000), "steelblue").save(src, format=factory.upper())
    dst = tmp_path / "out.jpg"
    assert build_preview(src, dst) == dst
    with Image.open(dst) as img:
        assert max(img.size) <= 1280

def test_preview_rejects_garbage(tmp_path: Path):
    src = tmp_path / "junk.jpg"
    src.write_bytes(b"not an image at all")
    dst = tmp_path / "out.jpg"
    assert build_preview(src, dst) is None
    assert not dst.exists()

def test_preview_raw_without_embedded_thumb(tmp_path: Path):
    src = tmp_path / "clip.dng"
    src.write_bytes(b"\x00" * 1024)
    assert build_preview(src, tmp_path / "out.jpg") is None
