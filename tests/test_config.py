from __future__ import annotations

from pathlib import Path

from ol_ce_sync.config import (
    DEFAULT_IGNORE_PATTERNS,
    LEGACY_AMBIGUOUS_IGNORE_PATTERNS,
    default_config,
    load_config,
    write_default_config,
)


def test_default_sync_rules_keep_pdf_source_assets(tmp_path: Path) -> None:
    config = default_config(tmp_path, project_id="project123")

    assert "*.pdf" not in config.ignore.patterns
    assert "*.bbl" not in config.ignore.patterns
    assert "*.ist" not in config.ignore.patterns


def test_config_writer_escapes_project_name(tmp_path: Path) -> None:
    config = default_config(
        tmp_path,
        project_id="project123",
        project_name='paper "quoted"\nname',
    )

    write_default_config(config)

    assert load_config(tmp_path).project.project_name == 'paper "quoted"\nname'


def test_load_migrates_untouched_legacy_default_ignores(tmp_path: Path) -> None:
    patterns = [*DEFAULT_IGNORE_PATTERNS, *LEGACY_AMBIGUOUS_IGNORE_PATTERNS]
    rendered = ", ".join(f'"{pattern}"' for pattern in patterns)
    config_dir = tmp_path / ".ol-sync"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[project]\nhost = "http://localhost"\nproject_id = "project123"\n'
        f"[ignore]\npatterns = [{rendered}]\n",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert "*.pdf" not in config.ignore.patterns
