"""Tests for the release-bundle exporter (slice 4c).

Covers: empty edited/, single-source happy path with image, multi-source
splitting into one file per source, id collisions, image bookkeeping,
the exact JS envelope DM CM relies on, and stale-release cleanup.
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


# ---- Envelope shape -------------------------------------------------------

def test_empty_edited_dir_emits_no_manual_files(isolated_root):
    from app import export

    result = export.export_all()

    assert result["total_records"] == 0
    assert result["manuals"] == []
    assert list(export.RELEASE_DIR.iterdir()) == [export.RELEASE_IMAGES_DIR.parent]


def test_single_record_uses_push_envelope(isolated_root):
    from app import export

    _approve(
        isolated_root,
        "ravenloft",
        "werebat",
        {"id": "werebat", "name": "Werebat", "cr": "3"},
    )
    result = export.export_all()

    expected = (
        "(window.MONSTER_MANUALS_DATA = window.MONSTER_MANUALS_DATA || []).push(\n"
        '{"id":"werebat","name":"Werebat","cr":"3"}\n'
        ");\n"
    )
    manual = export.RELEASE_DIR / "monster_manual_ravenloft.js"
    assert manual.read_text(encoding="utf-8") == expected
    assert result["manuals"][0]["file_name"] == "monster_manual_ravenloft.js"
    assert result["manuals"][0]["slugs"] == ["werebat"]
    assert result["images_copied"] == ["werebat"]
    assert (export.RELEASE_IMAGES_DIR / "werebat.webp").exists()


def test_one_file_per_source(isolated_root):
    from app import export

    _approve(isolated_root, "darksun", "b-rohg", {"id": "b-rohg", "name": "B'rohg"})
    _approve(isolated_root, "ravenloft", "werebat", {"id": "werebat", "name": "Werebat"})

    result = export.export_all()

    files = sorted(p.name for p in export.RELEASE_DIR.glob("monster_manual_*.js"))
    assert files == ["monster_manual_darksun.js", "monster_manual_ravenloft.js"]
    assert result["total_records"] == 2
    assert {m["source_slug"] for m in result["manuals"]} == {"darksun", "ravenloft"}
    # Each file holds only the records from its source.
    darksun_text = (export.RELEASE_DIR / "monster_manual_darksun.js").read_text(encoding="utf-8")
    assert "b-rohg" in darksun_text
    assert "werebat" not in darksun_text


def test_multiple_records_in_one_source_one_per_line(isolated_root):
    """Two records in the same source share a file, comma-separated, one per line."""
    from app import export

    _approve(isolated_root, "darksun", "b-rohg", {"id": "b-rohg", "name": "B'rohg"})
    _approve(isolated_root, "darksun", "thri-kreen", {"id": "thri-kreen", "name": "Thri-kreen"})

    export.export_all()
    text = (export.RELEASE_DIR / "monster_manual_darksun.js").read_text(encoding="utf-8")

    lines = text.splitlines()
    assert lines[0] == "(window.MONSTER_MANUALS_DATA = window.MONSTER_MANUALS_DATA || []).push("
    assert lines[-1] == ");"
    # Two record lines (sorted by filename: b-rohg before thri-kreen), first has
    # trailing comma, last does not.
    assert lines[1] == '{"id":"b-rohg","name":"B\'rohg"},'
    assert lines[2] == '{"id":"thri-kreen","name":"Thri-kreen"}'


# ---- Collisions, errors --------------------------------------------------

def test_id_collision_across_sources_raises(isolated_root):
    from app import export

    _approve(isolated_root, "sourceA", "goblin", {"id": "goblin", "name": "Goblin"})
    _approve(isolated_root, "sourceB", "goblin", {"id": "goblin", "name": "Goblin (variant)"})

    with pytest.raises(export.IdCollisionError) as exc_info:
        export.export_all()
    assert "goblin" in str(exc_info.value)
    # Nothing should have been written.
    assert not any(export.RELEASE_DIR.glob("monster_manual_*.js"))


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


# ---- Image bookkeeping ---------------------------------------------------

def test_missing_image_is_reported_but_does_not_fail(isolated_root):
    from app import export

    _approve(
        isolated_root, "ravenloft", "werebat",
        {"id": "werebat", "name": "Werebat"}, with_webp=False,
    )
    result = export.export_all()

    assert result["total_records"] == 1
    assert result["images_copied"] == []
    assert result["images_missing"] == ["werebat"]


def test_images_shared_across_manuals_go_to_one_folder(isolated_root):
    from app import export

    _approve(isolated_root, "darksun", "b-rohg", {"id": "b-rohg", "name": "B'rohg"})
    _approve(isolated_root, "ravenloft", "werebat", {"id": "werebat", "name": "Werebat"})

    export.export_all()

    assert (export.RELEASE_IMAGES_DIR / "b-rohg.webp").exists()
    assert (export.RELEASE_IMAGES_DIR / "werebat.webp").exists()


# ---- Content correctness -------------------------------------------------

def test_unicode_preserved_literally(isolated_root):
    from app import export

    _approve(
        isolated_root, "darksun", "naga",
        {"id": "naga", "name": "Naga — Spirit", "trait": "café"},
    )
    export.export_all()
    text = (export.RELEASE_DIR / "monster_manual_darksun.js").read_text(encoding="utf-8")
    assert "Naga — Spirit" in text
    assert "café" in text
    assert "\\u" not in text


def test_records_within_a_manual_sorted_by_filename(isolated_root):
    """Stable ordering keeps each manual file diff-friendly across exports."""
    from app import export

    _approve(isolated_root, "alpha-source", "zebra", {"id": "zebra", "name": "Zebra"})
    _approve(isolated_root, "alpha-source", "wolf", {"id": "wolf", "name": "Wolf"})

    result = export.export_all()
    [manual] = result["manuals"]
    assert manual["slugs"] == ["wolf", "zebra"]


# ---- Lifecycle -----------------------------------------------------------

def test_stale_release_is_wiped_on_re_export(isolated_root):
    """A previously-exported source whose approvals have been removed must
    not leave behind a stale manual file."""
    from app import export

    _approve(isolated_root, "darksun", "b-rohg", {"id": "b-rohg", "name": "B'rohg"})
    _approve(isolated_root, "ravenloft", "werebat", {"id": "werebat", "name": "Werebat"})
    export.export_all()
    assert (export.RELEASE_DIR / "monster_manual_darksun.js").exists()
    assert (export.RELEASE_IMAGES_DIR / "b-rohg.webp").exists()

    # Drop the entire darksun source.
    import shutil
    shutil.rmtree(isolated_root / "edited" / "darksun")
    result = export.export_all()

    assert [m["source_slug"] for m in result["manuals"]] == ["ravenloft"]
    assert not (export.RELEASE_DIR / "monster_manual_darksun.js").exists()
    assert not (export.RELEASE_IMAGES_DIR / "b-rohg.webp").exists()


# ---- render_manual_js helper --------------------------------------------

def test_render_manual_js_uses_exact_required_opening_and_closing():
    """The opening + closing lines are load-bearing — DM CM relies on them
    verbatim."""
    from app import export

    out = export.render_manual_js([{"id": "x", "name": "X"}])
    assert out.startswith("(window.MONSTER_MANUALS_DATA = window.MONSTER_MANUALS_DATA || []).push(\n")
    assert out.rstrip("\n").endswith(");")
