# -*- coding: utf-8 -*-
"""按 docs/FORMAT_SPEC.md 逐条编写的验收测试。

这些测试在修复前的 forge/reader.py 上必须全部失败（或报错），
修复后必须全部通过。不允许修改 data/ 下的样例文件。
"""

import csv
import ctypes
import ctypes.wintypes
import gc
import os
import time
from datetime import date

import pytest

from forge import reader as reader_mod
from forge.reader import DecodeError, FormatError, read_csv

# 当前实现还没有 iter_csv，用 getattr 保证测试模块能被收集，
# 由具体用例在调用时失败（修复后该名字即为真正的生成器函数）。
iter_csv = getattr(reader_mod, "iter_csv", None)


# --------------------------------------------------------------------------
# 辅助：在临时目录里按指定字节内容造文件
# --------------------------------------------------------------------------
@pytest.fixture()
def write_bytes(tmp_path):
    def _write(name: str, raw: bytes) -> str:
        p = tmp_path / name
        p.write_bytes(raw)
        return str(p)

    return _write


# ==========================================================================
# 第 1 条：编码识别
# ==========================================================================
class TestEncoding:
    def test_utf8_bom_sample(self):
        # data/bom.csv：带 BOM 的 UTF-8，表头不得残留 BOM 字符
        rows = read_csv("data/bom.csv")
        assert list(rows[0].keys()) == ["id", "name"]
        assert rows[0]["name"] == "张三"
        assert rows[1]["name"] == "李四"

    def test_utf8_plain(self, write_bytes):
        p = write_bytes("u8.csv", "id,name\n1,张三\n".encode("utf-8"))
        rows = read_csv(p)
        assert rows[0]["name"] == "张三"

    def test_utf16_le_with_bom_sample(self):
        # data/utf16.csv：UTF-16 LE 带 BOM
        rows = read_csv("data/utf16.csv")
        assert list(rows[0].keys()) == ["id", "name"]
        assert rows[0] == {"id": 1, "name": "王五"}
        assert rows[1] == {"id": 2, "name": "赵六"}

    def test_utf16_be_with_bom(self, write_bytes):
        raw = "id,name\n1,甲\n".encode("utf-16-be")
        raw = b"\xfe\xff" + raw
        p = write_bytes("u16be.csv", raw)
        rows = read_csv(p)
        assert rows[0] == {"id": 1, "name": "甲"}

    def test_gbk_sample(self):
        # data/gbk.csv：GBK 编码中文
        rows = read_csv("data/gbk.csv")
        assert rows[0] == {"id": 1, "name": "张三"}
        assert rows[1] == {"id": 2, "name": "李四"}

    def test_unknown_encoding_raises_decode_error(self, write_bytes):
        # 既非合法 UTF-8 也非合法 GBK 的字节流
        p = write_bytes("bad.csv", b"a,b\n1,\xff\xfe\xfd\n")
        with pytest.raises(DecodeError):
            read_csv(p)


# ==========================================================================
# 第 2 条：字段解析（引号、转义、行结束符、无末尾换行）
# ==========================================================================
class TestFieldParsing:
    def test_quoted_comma_does_not_split_columns(self):
        # data/quoted.csv：客户名里带逗号
        rows = read_csv("data/quoted.csv")
        assert rows[0]["name"] == "Smith, John"
        assert len(rows) == 3

    def test_quoted_newline_is_field_content(self):
        rows = read_csv("data/quoted.csv")
        assert rows[0]["note"] == "line1\r\nline2"

    def test_escaped_quote_inside_field(self):
        rows = read_csv("data/quoted.csv")
        assert rows[1]["note"] == 'say "hi"'
        assert rows[1]["name"] == "Doe, Jane"

    def test_file_without_trailing_newline(self):
        # data/quoted.csv 末尾没有换行，最后一行必须读出来
        rows = read_csv("data/quoted.csv")
        assert rows[2] == {"name": "Plain", "note": "ok"}

    def test_mixed_line_endings(self, write_bytes):
        p = write_bytes("mix.csv", b"a,b\r\n1,x\n2,y\r\n3,z")
        rows = read_csv(p)
        assert [r["a"] for r in rows] == [1, 2, 3]

    def test_lone_cr_as_line_ending(self, write_bytes):
        p = write_bytes("cr.csv", b"a,b\r1,x\r2,y\r")
        rows = read_csv(p)
        assert rows == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]

    def test_unterminated_quote_raises_format_error(self, write_bytes):
        p = write_bytes("unterminated.csv", b'a,b\n1,"oops\n')
        with pytest.raises(FormatError):
            read_csv(p)


# ==========================================================================
# 第 3 条：空值与 NULL
# ==========================================================================
class TestNulls:
    def test_empty_field_is_none(self):
        rows = read_csv("data/nulls.csv")
        assert rows[0]["amount"] is None  # 完全空字段 ,,

    def test_null_token_is_none_case_insensitive(self):
        rows = read_csv("data/nulls.csv")
        assert rows[1]["amount"] is None  # NULL
        assert rows[2]["active"] is None  # 末尾空字段

    def test_quoted_empty_is_empty_string(self):
        rows = read_csv("data/nulls.csv")
        # 第四行："","",2024-03-04,false
        assert rows[3]["code"] == ""
        assert rows[3]["amount"] == ""
        assert isinstance(rows[3]["code"], str)

    def test_quoted_null_is_plain_string(self, write_bytes):
        p = write_bytes("qnull.csv", b'v\n"NULL"\n')
        rows = read_csv(p)
        assert rows[0]["v"] == "NULL"


# ==========================================================================
# 第 4 条：类型推断
# ==========================================================================
class TestTypeInference:
    def test_leading_zeros_kept_as_string(self):
        # 工号 007 不得被吞成 7
        rows = read_csv("data/nulls.csv")
        assert rows[0]["code"] == "007"
        assert isinstance(rows[0]["code"], str)
        assert rows[1]["code"] == "0123"
        assert isinstance(rows[1]["code"], str)

    def test_int_inference(self, write_bytes):
        p = write_bytes("i.csv", b"v\n123\n-7\n0\n")
        rows = read_csv(p)
        assert rows[0]["v"] == 123 and isinstance(rows[0]["v"], int)
        assert rows[1]["v"] == -7 and isinstance(rows[1]["v"], int)
        assert rows[2]["v"] == 0 and isinstance(rows[2]["v"], int)

    def test_float_inference(self, write_bytes):
        p = write_bytes("f.csv", b"v\n3.14\n-0.5\n85.0\n")
        rows = read_csv(p)
        assert rows[0]["v"] == pytest.approx(3.14)
        assert isinstance(rows[0]["v"], float)
        assert isinstance(rows[1]["v"], float)
        assert rows[2]["v"] == pytest.approx(85.0)

    def test_date_inference(self):
        # 日期字段导入后必须是 date，而不是字符串
        rows = read_csv("data/nulls.csv")
        for row in rows:
            assert isinstance(row["date"], date)
        assert rows[0]["date"] == date(2024, 3, 1)

    def test_bool_inference_case_insensitive(self):
        rows = read_csv("data/nulls.csv")
        assert rows[0]["active"] is True
        assert rows[1]["active"] is False
        assert rows[3]["active"] is False
        assert isinstance(rows[0]["active"], bool)

    def test_scientific_notation_kept_as_string(self, write_bytes):
        p = write_bytes("sci.csv", b"v\n1e5\n")
        rows = read_csv(p)
        assert rows[0]["v"] == "1e5"
        assert isinstance(rows[0]["v"], str)

    def test_plain_string_preserved_without_stripping(self, write_bytes):
        p = write_bytes("s.csv", b"v\n  spaced  \n")
        rows = read_csv(p)
        assert rows[0]["v"] == "  spaced  "

    def test_header_not_stripped(self, write_bytes):
        p = write_bytes("h.csv", b" id , v \n1,2\n")
        rows = read_csv(p)
        assert " id " in rows[0]
        assert " v " in rows[0]

    def test_inf_nan_not_inferred_as_float(self, write_bytes):
        p = write_bytes("inf.csv", b"v\ninf\nnan\n")
        rows = read_csv(p)
        assert rows[0]["v"] == "inf"
        assert rows[1]["v"] == "nan"


# ==========================================================================
# 第 5 条：结构校验
# ==========================================================================
class TestStructure:
    def test_column_mismatch_raises_with_line_number(self):
        # data/badcols.csv：表头第 1 行，坏数据在第 2 行
        with pytest.raises(FormatError) as exc:
            read_csv("data/badcols.csv")
        assert "2" in str(exc.value)

    def test_output_is_list_of_dicts_in_header_order(self, write_bytes):
        p = write_bytes("order.csv", b"z,a,m\n1,2,3\n")
        rows = read_csv(p)
        assert isinstance(rows, list)
        assert list(rows[0].keys()) == ["z", "a", "m"]
        assert rows[0] == {"z": 1, "a": 2, "m": 3}


# ==========================================================================
# 第 6 条：流式与资源
# ==========================================================================
class TestStreaming:
    def test_iter_csv_yields_dicts_lazily(self, write_bytes):
        p = write_bytes("it.csv", b"a,b\n1,x\n2,y\n")
        it = iter_csv(p)
        first = next(it)
        assert first == {"a": 1, "b": "x"}
        assert list(it) == [{"a": 2, "b": "y"}]

    def test_iter_csv_works_with_quoted_multiline(self):
        rows = list(iter_csv("data/quoted.csv"))
        assert rows[0]["note"] == "line1\r\nline2"
        assert len(rows) == 3

    @pytest.fixture(scope="class")
    def huge_file(self, tmp_path_factory):
        """生成约 100MB、100 万行、8 列的 UTF-8 文件（只生成一次）。"""
        path = tmp_path_factory.mktemp("big") / "huge.csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "name", "city", "dept", "code", "score", "date", "active"])
            for i in range(1_000_000):
                w.writerow([
                    i,
                    f"user_{i:07d}_padding_0123456789",
                    "Shanghai_municipality",
                    "engineering_department",
                    42,
                    3.14,
                    "2024-03-01",
                    "true",
                ])
        size = os.path.getsize(path)
        assert size >= 90 * 1024 * 1024  # 确认文件确实是 ~100MB 量级
        return str(path)

    @staticmethod
    def _peak_rss_mb():
        """当前进程工作集（Windows）或 RSS（POSIX），单位 MB。"""
        if os.name == "nt":
            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.wintypes.DWORD),
                    ("PageFaultCount", ctypes.wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            psapi = ctypes.WinDLL("psapi")
            psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(counters),
                counters.cb,
            )
            return counters.WorkingSetSize / (1024 * 1024)
        # POSIX 回退：resource.ru_maxrss
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    def test_iter_csv_memory_under_128mb_on_100mb_file(self, huge_file):
        gc.collect()
        baseline = self._peak_rss_mb()
        count = 0
        for row in iter_csv(huge_file):
            count += 1
            if count % 100_000 == 0:
                # 流式过程中持续检查，而不是只看结束时
                assert self._peak_rss_mb() - baseline < 128
        assert count == 1_000_000
        assert self._peak_rss_mb() - baseline < 128

    def test_parse_1m_rows_8_cols_within_8_seconds(self, huge_file):
        start = time.perf_counter()
        count = 0
        for _ in iter_csv(huge_file):
            count += 1
        elapsed = time.perf_counter() - start
        assert count == 1_000_000
        assert elapsed < 8.0, f"解析耗时 {elapsed:.2f}s 超过 8s 预算"


# ==========================================================================
# 第 7 条：错误恢复
# ==========================================================================
class TestErrorRecovery:
    def test_strict_mode_is_default(self, write_bytes):
        p = write_bytes("strict.csv", b"a,b\n1,2,3\n")
        with pytest.raises(FormatError):
            read_csv(p)

    def test_skip_bad_returns_rows_and_stats(self, write_bytes):
        p = write_bytes(
            "skip.csv",
            b"a,b\n1,x\n2,y,z\n3,w\n",  # 第 3 行是坏行
        )
        rows, stats = read_csv(p, skip_bad=True)
        assert rows == [{"a": 1, "b": "x"}, {"a": 3, "b": "w"}]
        assert stats["total"] == 3
        assert stats["skipped"] == 1
        assert len(stats["errors"]) == 1
        line_no, reason = stats["errors"][0]
        assert line_no == 3
        assert isinstance(reason, str) and reason

    def test_skip_bad_keeps_rows_after_error(self, write_bytes):
        # 容错模式不得因单行错误丢失后续所有行
        p = write_bytes(
            "keep.csv",
            b"a,b\n1,x\nbad,row,extra\n2,y\n3,z\n",
        )
        rows, stats = read_csv(p, skip_bad=True)
        assert [r["a"] for r in rows] == [1, 2, 3]
        assert stats["skipped"] == 1
        assert stats["errors"][0][0] == 3

    def test_skip_bad_on_sample_badcols(self):
        rows, stats = read_csv("data/badcols.csv", skip_bad=True)
        assert rows == []
        assert stats["total"] == 1
        assert stats["skipped"] == 1
        assert stats["errors"][0][0] == 2


# ==========================================================================
# 第 8 条：data/ 全部样例整体验收
# ==========================================================================
def test_all_samples_end_to_end():
    basic = read_csv("data/basic.csv")
    assert basic[0] == {"id": 1, "name": "Alice", "score": 90.5}
    assert isinstance(basic[1]["score"], float)

    bom = read_csv("data/bom.csv")
    assert bom[0]["name"] == "张三"

    gbk = read_csv("data/gbk.csv")
    assert gbk[1]["name"] == "李四"

    utf16 = read_csv("data/utf16.csv")
    assert utf16[0]["name"] == "王五"
    assert utf16[1]["name"] == "赵六"

    quoted = read_csv("data/quoted.csv")
    assert len(quoted) == 3
    assert quoted[0]["name"] == "Smith, John"
    assert quoted[0]["note"] == "line1\r\nline2"
    assert quoted[1]["note"] == 'say "hi"'

    nulls = read_csv("data/nulls.csv")
    assert nulls[0]["code"] == "007"
    assert nulls[0]["amount"] is None
    assert nulls[1]["amount"] is None
    assert nulls[3]["code"] == ""
    assert nulls[0]["date"] == date(2024, 3, 1)
    assert nulls[0]["active"] is True

    assert read_csv("data/empty.csv") == []
