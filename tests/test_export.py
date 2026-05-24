"""Tests for the release-bundle exporter (slice 4c).

Covers: empty edited/, single-source happy path with image, multiple
sources, id collisions, minified-JS formatting, and stale-release cleanup.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated_root(monkeypatch, tmp_path):
    """Repoint storage.ROOT (and every derived path) at a clean tmp dir so
    each test gets its own edited/ + release/."""
    from app import export, storage

    edited = tmp_path / "edited"
    release = tmp_path / "release"
    edited.mkdir()

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    monkeypatch.setattr(storage, "EDITED_DIR", edited)
    monkeypatch.setattr(export, "RELEASE_DIR", release)
    monkeypatch.setattr(export, "RELEASE_JS", release / "manually_entered.js")
    monkeypatch.setattr(
        export, "RELEASE_IMAGES_DIR", release / "assets" / "monster_images"
    )
    return tmp_path


def _approve(root: Path, source: str, slug: str, record: dict, with_webp: bool = True) -> None:
    src_dir = root / "edited" / source
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / f"{slug}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )
    if with_webp:
        (src_dir / f"{slug}.webp").write_bytes(b"fake-webp-bytes")


def test_empty_edited_dir_writes_empty_array(isolated_root):
    from app import export

    result = export.export_all()

    assert result["record_count"] == 0
    assert result["slugs"] == []
    assert export.RELEASE_JS.read_text(encoding="utf-8") == "const MANUALLY_ENTERED = [];"


def test_single_record_minified_one_line(isolated_root):
    from app import export

    _approve(
        isolated_root,
        "ravenloft",
        "werebat",
        {"id": "werebat", "name": "Werebat", "cr": "3"},
    )
    result = export.export_all()

    text = export.RELEASE_JS.read_text(encoding="utf-8")
    assert text == 'const MANUALLY_ENTERED = [{"id":"werebat","name":"Werebat","cr":"3"}];'
    assert "\n" not in text
    assert result["record_count"] == 1
    assert result["slugs"] == ["werebat"]
    assert result["images_copied"] == ["werebat"]
    assert (export.RELEASE_IMAGES_DIR / "werebat.webp").exists()


def test_multiple_sources_concatenated_with_comma(isolated_root):
    from app import export

    _approve(isolated_root, "darksun", "b-rohg", {"id": "b-rohg", "name": "B'rohg"})
    _approve(isolated_root, "ravenloft", "werebat", {"id": "werebat", "name": "Werebat"})

    export.export_all()
    text = export.RELEASE_JS.read_text(encoding="utf-8")

    assert text.startswith("const MANUALLY_ENTERED = [{")
    assert text.endswith("}];")
    assert "},{" in text
    assert text.count("},{") == 1


def test_id_collision_across_sources_raises(isolated_root):
    from app import export

    _approve(isolated_root, "sourceA", "goblin", {"id": "goblin", "name": "Goblin"})
    _approve(isolated_root, "sourceB", "goblin", {"id": "goblin", "name": "Goblin (variant)"})

    with pytest.raises(export.IdCollisionError) as exc_info:
        export.export_all()
    assert "goblin" in str(exc_info.value)
    # Nothing should have been written.
    assert not export.RELEASE_JS.exists()


def test_missing_image_is_reported_but_does_not_fail(isolated_root):
    from app import export

    _approve(
        isolated_root, "ravenloft", "werebat",
        {"id": "werebat", "name": "Werebat"}, with_webp=False,
    )
    result = export.export_all()

    assert result["record_count"] == 1
    assert result["images_copied"] == []
    assert result["images_missing"] == ["werebat"]


def test_unicode_preserved_literally(isolated_root):
    from app import export

    _approve(
        isolated_root, "darksun", "naga",
        {"id": "naga", "name": "Naga — Spirit", "trait": "café"},
    )
    export.export_all()
    text = export.RELEASE_JS.read_text(encoding="utf-8")
    assert "Naga — Spirit" in text
    assert "café" in text
    assert "\\u" not in text


def test_stale_release_is_wiped_on_re_export(isolated_root):
    from app import export

    # First export with two creatures + images.
    _approve(isolated_root, "darksun", "b-rohg", {"id": "b-rohg", "name": "B'rohg"})
    _approve(isolated_root, "ravenloft", "werebat", {"id": "werebat", "name": "Werebat"})
    export.export_all()
    assert (export.RELEASE_IMAGES_DIR / "b-rohg.webp").exists()
    assert (export.RELEASE_IMAGES_DIR / "werebat.webp").exists()

    # Remove one approved creature, re-export.
    (isolated_root / "edited" / "darksun" / "b-rohg.json").unlink()
    (isolated_root / "edited" / "darksun" / "b-rohg.webp").unlink()
    result = export.export_all()

    assert result["slugs"] == ["werebat"]
    assert not (export.RELEASE_IMAGES_DIR / "b-rohg.webp").exists()
    text = export.RELEASE_JS.read_text(encoding="utf-8")
    assert "b-rohg" not in text


def test_records_sorted_by_source_then_filename(isolated_root):
    """Stable ordering keeps the rendered file diff-friendly across exports."""
    from app import export

    _approve(isolated_root, "zeta-source", "aardvark", {"id": "aardvark", "name": "Aardvark"})
    _approve(isolated_root, "alpha-source", "zebra", {"id": "zebra", "name": "Zebra"})
    _approve(isolated_root, "alpha-source", "wolf", {"id": "wolf", "name": "Wolf"})

    result = export.export_all()
    assert result["slugs"] == ["wolf", "zebra", "aardvark"]


def test_malformed_json_raises_value_error(isolated_root):
    from app import export

    src_dir = isolated_root / "edited" / "broken"
    src_dir.mkdir(parents=True)
    (src_dir / "bad.json").write_text("{not valid", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        export.collect_records()


def test_record_missing_id_raises_value_error(isolated_root):
    from app import export

    src_dir = isolated_root / "edited" / "sourceA"
    src_dir.mkdir(parents=True)
    (src_dir / "anon.json").write_text(json.dumps({"name": "No ID"}), encoding="utf-8")

    with pytest.raises(ValueError, match="missing an 'id'"):
        export.collect_records()
