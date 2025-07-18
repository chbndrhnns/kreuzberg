from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from PIL import Image

from kreuzberg import ExtractionConfig, GMFTConfig
from kreuzberg._gmft import extract_tables
from kreuzberg.exceptions import MissingDependencyError
from kreuzberg.extraction import extract_file

if TYPE_CHECKING:
    import anyio


@pytest.fixture
def mock_cropped_table() -> MagicMock:
    mock = MagicMock()
    mock.page.page_number = 1
    mock.image.return_value = Image.new("RGB", (100, 100))
    return mock


@pytest.fixture
def mock_formatted_table() -> MagicMock:
    mock = MagicMock()
    df = pd.DataFrame({"Column1": [1, 2, 3], "Column2": ["A", "B", "C"]})
    mock.df = AsyncMock(return_value=df)
    return mock


@pytest.mark.anyio
async def test_extract_tables_with_default_config(tiny_pdf_with_tables: Path) -> None:
    try:
        tables = await extract_tables(tiny_pdf_with_tables)

        assert tables
        assert isinstance(tables, list)
        assert all(isinstance(table, dict) for table in tables)

        for table in tables:
            assert "page_number" in table
            assert isinstance(table["page_number"], int)
            assert "text" in table
            assert isinstance(table["text"], str)
            assert "df" in table

            assert isinstance(table["df"], (pd.DataFrame, dict))
            assert "cropped_image" in table

            assert isinstance(table["cropped_image"], (Image.Image, type(None)))
    except MissingDependencyError:
        pytest.skip("GMFT dependency not installed")


@pytest.mark.anyio
async def test_gmft_integration_with_extraction_api(tiny_pdf_with_tables: Path) -> None:
    try:
        config = ExtractionConfig(
            extract_tables=True, gmft_config=GMFTConfig(detector_base_threshold=0.8, enable_multi_header=True)
        )

        result = await extract_file(tiny_pdf_with_tables, config=config)

        assert hasattr(result, "tables")
        assert result.tables
        assert isinstance(result.tables, list)

        for table in result.tables:
            assert "page_number" in table
            assert "text" in table
            assert "df" in table
            assert "cropped_image" in table

            assert "|" in table["text"]

            assert not table["df"].empty

    except MissingDependencyError:
        pytest.skip("GMFT dependency not installed")


@pytest.mark.anyio
async def test_extract_tables_with_custom_config(tiny_pdf_with_tables: Path) -> None:
    config = GMFTConfig(detector_base_threshold=0.85, remove_null_rows=True, enable_multi_header=True, verbosity=1)

    try:
        tables = await extract_tables(tiny_pdf_with_tables, config)

        assert tables
        assert isinstance(tables, list)
    except MissingDependencyError:
        pytest.skip("GMFT dependency not installed")


@pytest.mark.anyio
async def test_extract_tables_missing_dependency(tiny_pdf_with_tables: Path) -> None:
    if os.getenv("KREUZBERG_GMFT_ISOLATED", "true").lower() == "true":
        pytest.skip("Cannot test missing dependency with isolated process")

    with patch("kreuzberg._gmft.run_sync", side_effect=ImportError("No module named 'gmft'")):
        with pytest.raises(MissingDependencyError) as exc_info:
            await extract_tables(tiny_pdf_with_tables)

        assert "table extraction" in str(exc_info.value)
        assert "gmft" in str(exc_info.value)


@pytest.mark.anyio
async def test_extract_tables_with_mocks(tiny_pdf_with_tables: Path) -> None:
    if os.getenv("KREUZBERG_GMFT_ISOLATED", "true").lower() == "true":
        pytest.skip("Cannot use mocks with isolated process")

    mock_path = MagicMock(spec=Path)

    mock_doc = MagicMock()
    mock_page = MagicMock()
    mock_doc.__iter__.return_value = [mock_page]
    mock_doc.close = MagicMock()

    mock_cropped_table = MagicMock()
    mock_cropped_table.page.page_number = 1
    mock_cropped_table.image.return_value = Image.new("RGB", (100, 100))

    mock_formatted_table = MagicMock()
    mock_df = pd.DataFrame({"Col1": [1, 2], "Col2": ["A", "B"]})
    mock_formatted_table.df = AsyncMock(return_value=mock_df)

    mock_auto = MagicMock()
    mock_auto.AutoTableDetector = MagicMock()
    mock_auto.AutoTableFormatter = MagicMock()
    mock_auto.TATRFormatConfig = MagicMock()
    mock_auto.CroppedTable = MagicMock()

    mock_detector_tatr = MagicMock()
    mock_detector_tatr.TATRDetectorConfig = MagicMock()

    mock_pdf_bindings = MagicMock()
    mock_pdf_bindings.PyPDFium2Document = MagicMock()

    module_patcher = patch.dict(
        "sys.modules",
        {
            "gmft": MagicMock(),
            "gmft.auto": mock_auto,
            "gmft.detectors.tatr": mock_detector_tatr,
            "gmft.pdf_bindings": mock_pdf_bindings,
        },
    )

    mock_detector = mock_auto.AutoTableDetector.return_value
    mock_detector.extract.return_value = [mock_cropped_table]

    mock_formatter = mock_auto.AutoTableFormatter.return_value
    mock_formatter.extract.return_value = mock_formatted_table

    with module_patcher, patch("kreuzberg._gmft.run_sync") as mock_run_sync:
        mock_run_sync.side_effect = [
            mock_doc,
            [mock_cropped_table],
            mock_formatted_table,
            mock_df,
            None,
        ]

        result = await extract_tables(mock_path)

        assert isinstance(result, list)
        assert len(result) == 1

        table_data = result[0]
        assert isinstance(table_data, dict)
        assert table_data["page_number"] == 1
        assert isinstance(table_data["df"], pd.DataFrame)
        assert isinstance(table_data["text"], str)
        assert isinstance(table_data["cropped_image"], Image.Image)

        mock_auto.AutoTableDetector.assert_called_once()
        mock_auto.AutoTableFormatter.assert_called_once()
        mock_detector_tatr.TATRDetectorConfig.assert_called_once()


@pytest.mark.anyio
async def test_gmft_config_default_values() -> None:
    config = GMFTConfig()

    assert config.verbosity == 0
    assert config.detector_base_threshold == 0.9
    assert config.formatter_base_threshold == 0.3
    assert config.remove_null_rows is True
    assert config.enable_multi_header is False
    assert config.semantic_spanning_cells is False
    assert config.semantic_hierarchical_left_fill == "algorithm"
    assert config.large_table_threshold == 10
    assert config.large_table_row_overlap_threshold == 0.2


@pytest.mark.anyio
async def test_gmft_config_custom_values() -> None:
    custom_confidence = {
        0: 0.4,
        1: 0.4,
        2: 0.4,
        3: 0.4,
        4: 0.6,
        5: 0.6,
        6: 99,
    }

    config = GMFTConfig(
        verbosity=2,
        formatter_base_threshold=0.5,
        cell_required_confidence=custom_confidence,  # type: ignore[arg-type]
        detector_base_threshold=0.8,
        remove_null_rows=False,
        enable_multi_header=True,
        semantic_spanning_cells=True,
        semantic_hierarchical_left_fill="deep",
        large_table_if_n_rows_removed=10,
        large_table_threshold=15,
        large_table_row_overlap_threshold=0.3,
        large_table_maximum_rows=1500,
        force_large_table_assumption=True,
    )

    assert config.verbosity == 2
    assert config.formatter_base_threshold == 0.5
    assert config.cell_required_confidence == custom_confidence
    assert config.detector_base_threshold == 0.8
    assert config.remove_null_rows is False
    assert config.enable_multi_header is True
    assert config.semantic_spanning_cells is True
    assert config.semantic_hierarchical_left_fill == "deep"
    assert config.large_table_if_n_rows_removed == 10
    assert config.large_table_threshold == 15
    assert config.large_table_row_overlap_threshold == 0.3
    assert config.large_table_maximum_rows == 1500
    assert config.force_large_table_assumption is True


def test_extract_tables_sync_with_tiny_pdf(tiny_pdf_with_tables: Path) -> None:
    """Test sync table extraction - covers lines 246-334."""
    try:
        from kreuzberg._gmft import extract_tables_sync

        tables = extract_tables_sync(tiny_pdf_with_tables)

        assert tables
        assert isinstance(tables, list)
        assert all(isinstance(table, dict) for table in tables)

        for table in tables:
            assert "page_number" in table
            assert isinstance(table["page_number"], int)
            assert "text" in table
            assert isinstance(table["text"], str)
            assert "df" in table
            assert isinstance(table["df"], (pd.DataFrame, dict))
            assert "cropped_image" in table
            assert isinstance(table["cropped_image"], (Image.Image, type(None)))
    except MissingDependencyError:
        pytest.skip("GMFT dependencies not available")


def test_extract_tables_sync_missing_dependency(tiny_pdf_with_tables: Path) -> None:
    """Test sync extraction with missing dependency - covers lines 259-260, 277."""
    if os.getenv("KREUZBERG_GMFT_ISOLATED", "true").lower() == "true":
        pytest.skip("Cannot test missing dependency with isolated process")

    from kreuzberg._gmft import extract_tables_sync

    with patch.dict("sys.modules", {"gmft": None, "gmft.auto": None}):
        with pytest.raises(MissingDependencyError) as exc_info:
            extract_tables_sync(tiny_pdf_with_tables)

        assert "table extraction" in str(exc_info.value)
        assert "gmft" in str(exc_info.value)


def test_extract_tables_sync_os_error(tmp_path: Path) -> None:
    """Test sync extraction with OS error when reading file stats - covers lines 259-264."""
    if os.getenv("KREUZBERG_GMFT_ISOLATED", "true").lower() == "true":
        pytest.skip("File errors handled differently in isolated process")

    from kreuzberg._gmft import extract_tables_sync

    fake_file = tmp_path / "nonexistent.pdf"

    with patch.dict("sys.modules", {"gmft": None, "gmft.auto": None}), pytest.raises(MissingDependencyError):
        extract_tables_sync(fake_file)


@pytest.mark.anyio
async def test_extract_tables_cache_processing_coordination(tiny_pdf_with_tables: Path) -> None:
    """Test cache processing coordination - covers lines 160-168."""
    import anyio

    from kreuzberg._gmft import extract_tables
    from kreuzberg._utils._cache import get_table_cache

    cache = get_table_cache()

    file_stat = tiny_pdf_with_tables.stat()
    file_info = str(
        sorted(
            {
                "path": str(tiny_pdf_with_tables.resolve()),
                "size": file_stat.st_size,
                "mtime": file_stat.st_mtime,
            }.items()
        )
    )
    config_str = str(sorted(asdict(GMFTConfig()).items()))

    cache.mark_processing(
        file_info=file_info,
        extractor="gmft",
        config=config_str,
    )

    async def complete_processing(event: anyio.Event) -> None:
        await anyio.sleep(0.1)
        cache.mark_complete(
            file_info=file_info,
            extractor="gmft",
            config=config_str,
        )

        cache.set(
            [],
            file_info=file_info,
            extractor="gmft",
            config=config_str,
        )
        event.set()

    async with anyio.create_task_group() as nursery:
        completion_event = anyio.Event()
        nursery.start_soon(complete_processing, completion_event)

        await anyio.sleep(0.2)

        result = await extract_tables(tiny_pdf_with_tables)
        assert result == []

        await completion_event.wait()


@pytest.mark.anyio
async def test_extract_tables_cache_hit(tiny_pdf_with_tables: Path) -> None:
    """Test cache hit path - should return cached result without processing."""
    from kreuzberg._gmft import extract_tables
    from kreuzberg._utils._cache import get_table_cache

    cache = get_table_cache()
    import pandas as pd

    cached_tables = [
        {
            "page_number": 1,
            "text": "cached table",
            "df": pd.DataFrame({"col": [1, 2]}),
            "cropped_image": Image.new("RGB", (10, 10), color="white"),
        }
    ]

    file_stat = tiny_pdf_with_tables.stat()
    cache_kwargs = {
        "file_info": str(
            sorted(
                {
                    "path": str(tiny_pdf_with_tables.resolve()),
                    "size": file_stat.st_size,
                    "mtime": file_stat.st_mtime,
                }.items()
            )
        ),
        "extractor": "gmft",
        "config": str(sorted(asdict(GMFTConfig()).items())),
    }

    await cache.aset(cached_tables, **cache_kwargs)

    result = await extract_tables(tiny_pdf_with_tables)

    assert len(result) == len(cached_tables)
    assert result[0]["page_number"] == cached_tables[0]["page_number"]
    assert result[0]["text"] == cached_tables[0]["text"]
    assert result[0]["df"].equals(cached_tables[0]["df"])
    assert len(result) == 1
    assert result[0]["text"] == "cached table"
