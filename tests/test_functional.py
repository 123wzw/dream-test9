"""CSV 读取器功能测试（基础场景）。"""

import pytest

from forge.reader import read_csv


def test_reads_simple_file():
    rows = read_csv("data/basic.csv")
    assert len(rows) == 3
    assert rows[0]["id"] == 1
    assert rows[0]["name"] == "Alice"


def test_infers_numbers():
    rows = read_csv("data/basic.csv")
    assert isinstance(rows[1]["score"], float)


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        read_csv("data/nope.csv")


def test_empty_file_returns_empty_list():
    assert read_csv("data/empty.csv") == []
