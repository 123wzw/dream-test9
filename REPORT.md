# csv-forge 修复报告

对照 `docs/FORMAT_SPEC.md` 逐条排查 `forge/reader.py` 后确认的不满足项、
修复方式与对应测试。所有问题均先在 `tests/test_spec.py` 写出复现测试
（在修复前代码上运行：23 个失败、12 个侥幸通过），修复后全量 **41 个用例通过**
（含 `tests/test_functional.py` 原有 4 个功能用例，断言未改动）。

- 仅修改：`forge/reader.py`
- 仅新增：`tests/test_spec.py`、本报告
- 未改动：`docs/FORMAT_SPEC.md`、`data/` 下全部样例、`tests/test_functional.py`、运行配置
- 依赖：仅标准库（`io`、`re`、`datetime`、`pathlib`，测试另用 `csv/ctypes/gc/time` 等标准库）

“代码位置”中的 **旧位置** 指修复前 `forge/reader.py` 的行号，
**新位置** 指修复后文件中的实现位置。

---

## 问题 1：编码写死 UTF-8，BOM/UTF-16/GBK 全部不支持

- **规范条款**：第 1 条 编码识别（UTF-8 含/不含 BOM、UTF-16 LE/BE 带 BOM、GBK；识别失败抛 `DecodeError`）
- **代码位置**：旧 `forge/reader.py:33`（`Path(path).read_text(encoding="utf-8")`）
- **当前行为**：
  - `data/gbk.csv`（GBK）直接抛 `UnicodeDecodeError`，运营中文表格崩溃；
  - `data/utf16.csv`（UTF-16 LE BOM）按 UTF-8 解码失败；
  - `data/bom.csv` 解出后表头残留 BOM 字符 `﻿id`；
  - 无法识别的编码没有统一的 `DecodeError`。
- **正确行为**：自动嗅探 BOM 与内容，无需调用方指定编码；都识别不了时抛 `DecodeError`，不返回乱码。
- **修复方式**：新增 `_open_text()`，只读文件头 64 KiB 嗅探——
  BOM 判定 UTF-16 LE/BE（手工跳过 BOM）、UTF-8 BOM（`utf-8-sig`），
  无 BOM 时先严格试 UTF-8 再试 GBK，均失败抛 `DecodeError`；
  返回 `io.TextIOWrapper` 增量解码，不整文件读入。
- **测试用例**：
  `TestEncoding::test_utf8_bom_sample`、`test_utf8_plain`、
  `test_utf16_le_with_bom_sample`、`test_utf16_be_with_bom`、
  `test_gbk_sample`、`test_unknown_encoding_raises_decode_error`

## 问题 2：用 `split(",")` 切列，引号规则完全缺失

- **规范条款**：第 2 条 字段解析（引号内逗号/换行/回车为字段内容、`""` 转义、无末尾换行也要读出）
- **代码位置**：旧 `forge/reader.py:42`（`cells = line.split(",")`）、旧 `:34`（`splitlines()`）
- **当前行为**：
  - `data/quoted.csv` 中 `"Smith, John"` 被逗号切成两列，列数对不上直接报 `FormatError`，客户名冲错位；
  - 引号内换行的记录被 `splitlines()` 物理拆断；`"say ""hi"""` 无法还原成 `say "hi"`；
  - 末行无换行时依赖 `splitlines` 侥幸可读，但与引号组合后行为不正确。
- **正确行为**：按 RFC 风格状态机解析引号字段，引号内逗号/`\r`/`\n` 均为内容，`""` 转义为单个 `"`，末行无换行照常读出。
- **修复方式**：
  - `_iter_physical_lines()`：按块（64 KiB）用 `str.find` 做 C 级行扫描，只认 `\r`/`\n`（不把 `\v`、`\x85` 当换行），正确处理块边界 `\r\n`；
  - 含引号的记录走 `_parse_quoted_record()` 状态机，支持跨行引号字段并把 `\r\n`/`\r`/`\n` 原样保留为字段内容，`""` 还原为 `"`，引号未闭合/闭合后出现多余字符抛带行号的 `FormatError`；
  - 无引号记录走 `str.split(",")` 快路径（兼顾性能，见问题 8）。
- **测试用例**：
  `TestFieldParsing::test_quoted_comma_does_not_split_columns`、
  `test_quoted_newline_is_field_content`、`test_escaped_quote_inside_field`、
  `test_file_without_trailing_newline`、`test_mixed_line_endings`、
  `test_lone_cr_as_line_ending`、`test_unterminated_quote_raises_format_error`

## 问题 3：空字段 / NULL / 引号空串三者不区分

- **规范条款**：第 3 条 空值与 NULL（`,,` → `None`；裸 `NULL` 大小写不敏感 → `None`；`""` → 空字符串）
- **代码位置**：旧 `forge/reader.py:15-18`（`_infer` 对空串返回 `None`、对 `NULL` 用 `strip().upper()` 判断）
- **当前行为**：`data/nulls.csv` 第 4 行的 `""` 被当成 `None`，无法与“真空字段”区分；`"NULL"`（带引号）也会被错误转成 `None`。
- **正确行为**：是否加引号是显式信号——未引号的空字段与裸 `NULL` → `None`；引号包裹的 `""` → `""`，引号内的 `NULL` 保持字符串。
- **修复方式**：解析阶段为每个字段保留 `quoted` 标记并一路传入 `_infer()`；`_infer` 按 `quoted` 分支处理三种形态，且 NULL 判断改为不依赖 `strip()`（不再误吃空格）。
- **测试用例**：
  `TestNulls::test_empty_field_is_none`、`test_null_token_is_none_case_insensitive`、
  `test_quoted_empty_is_empty_string`、`test_quoted_null_is_plain_string`

## 问题 4：前导零被吞、科学计数法被误转、字符串被去空格

- **规范条款**：第 4 条 类型推断（`007`/`0123` 保留 `str`；`1e5` 视为 `str`；其他字符串保持原样不去空格）
- **代码位置**：旧 `forge/reader.py:19-21`（先 `int(text)` 后 `float(text)`）、旧 `:37`（表头 `h.strip()`）
- **当前行为**：工号 `007` 被 `int()` 转成 `7`；`1e5` 被 `float()` 转成 `100000.0`；`inf`/`nan` 也会被 `float()` 接受；表头与字段两侧空格被 `strip()` 抹掉。
- **正确行为**：仅 `-?\d+` 形态转 `int`，且含前导零（多于一位且首位为 0）时保留字符串；仅 `-?\d+\.\d+` 形态转 `float`；科学计数法、`inf`、`nan` 一律保持字符串；普通字符串与表头原样保留。
- **修复方式**：`_infer()` 改为首字符分派 + 合并正则 `_NUMBER_RE`（`-?(?:\d+|\d+\.\d+|\d{4}-\d{2}-\d{2})$`），不匹配即原样返回；整数分支显式判前导零；表头不再 `strip()`。
- **测试用例**：
  `TestTypeInference::test_leading_zeros_kept_as_string`、
  `test_scientific_notation_kept_as_string`、`test_inf_nan_not_inferred_as_float`、
  `test_plain_string_preserved_without_stripping`、`test_header_not_stripped`、
  `test_int_inference`、`test_float_inference`

## 问题 5：日期不推断，导入后变成字符串

- **规范条款**：第 4 条 类型推断（`2024-03-01` → `datetime.date`）
- **代码位置**：旧 `forge/reader.py:14-29`（`_infer` 无任何日期分支）
- **当前行为**：`data/nulls.csv` 的 `date` 列全部以 `str` 返回，下游拿到的是字符串而非日期。
- **正确行为**：严格匹配 `YYYY-MM-DD` 且能通过 `date.fromisoformat` 校验（如 `2024-13-40` 不合法则保留字符串），返回 `datetime.date`。
- **修复方式**：日期形态并入 `_NUMBER_RE` 一次性匹配，再用 `date.fromisoformat()` 校验转换。
- **测试用例**：`TestTypeInference::test_date_inference`

## 问题 6：布尔推断规则过宽且顺序错误

- **规范条款**：第 4 条（`true`/`false` 大小写不敏感 → `bool`）
- **代码位置**：旧 `forge/reader.py:27-28`
- **当前行为**：布尔判断排在 `int`/`float` 之后，且未限制必须整词匹配；结合旧 `float()` 行为，类型优先级整体混乱。
- **正确行为**：仅整词 `true`/`false`（忽略大小写）转 `bool`，其余同首字母词（如 `truth`）保持字符串。
- **修复方式**：`_infer()` 首字符为 `t/T/f/F` 时整词比较，命中返回 `True/False`，否则原样返回。
- **测试用例**：`TestTypeInference::test_bool_inference_case_insensitive`

## 问题 7：结构校验不带行号，且无容错模式

- **规范条款**：第 5 条（列数不一致抛带行号的 `FormatError`）、第 7 条（严格默认；`skip_bad=True` 返回 `(rows, stats)`，`stats` 含 `total/skipped/errors`，坏行不影响后续行）
- **代码位置**：旧 `forge/reader.py:43-44`（报错信息只拼接了整行内容，没有行号）；旧文件无 `skip_bad` 参数
- **当前行为**：`data/badcols.csv` 报错信息不含行号；没有容错模式，一个坏行整批失败。
- **正确行为**：错误信息带从 1 开始的物理行号（表头为第 1 行）；`skip_bad=True` 时跳过坏行继续解析，返回行数据与统计，`errors` 为 `(行号, 原因)` 列表。
- **修复方式**：物理行扫描全程维护行号，`iter_csv()`/`read_csv()` 严格模式抛 `FormatError("第 N 行…")`；`read_csv(skip_bad=True)` 收集 `total/skipped/errors` 并继续产出后续行。
- **测试用例**：
  `TestStructure::test_column_mismatch_raises_with_line_number`、
  `TestErrorRecovery::test_strict_mode_is_default`、
  `test_skip_bad_returns_rows_and_stats`、
  `test_skip_bad_keeps_rows_after_error`、`test_skip_bad_on_sample_badcols`

## 问题 8：整文件读入内存，无流式 API，100MB 文件 OOM

- **规范条款**：第 6 条 流式与资源（提供 `iter_csv(path)` 逐行产出，不得整文件读入内存；100MB 文件峰值内存增量 < 128MB；100 万行 8 列 < 8 秒）
- **代码位置**：旧 `forge/reader.py:33-34`（`read_text()` 一次性读全文 + `splitlines()` 复制成列表）；旧文件无 `iter_csv`
- **当前行为**：100MB 文件会完整驻留内存并再复制一份行列表，整批任务被 OOM 杀掉；也没有逐行接口。
- **正确行为**：提供生成器 `iter_csv()`，全程按 64 KiB 块读取解码、逐行产出 dict；内存增量有界；性能达标。
- **修复方式**：
  - 新增 `iter_csv()`，与 `read_csv()` 共用 `_header_and_rows()`/`_iter_records()` 管线，全程不持有全量数据；
  - 为满足 8 秒预算采用双路径：无引号行用 C 级 `str.split` 快路径，仅含引号行进入字符级状态机；类型推断合并为一次正则 + 首字符分派。
  - 实测：103MB / 100 万行 / 8 列纯解析约 **5.6 秒**，内存增量用例（Windows 工作集采样，每 10 万行检查一次）通过，峰值增量 < 128MB。
- **测试用例**：
  `TestStreaming::test_iter_csv_yields_dicts_lazily`、
  `test_iter_csv_works_with_quoted_multiline`、
  `test_iter_csv_memory_under_128mb_on_100mb_file`、
  `test_parse_1m_rows_8_cols_within_8_seconds`

## 问题 9：输出契约与空文件行为

- **规范条款**：第 5 条（输出 `list[dict]`，键为表头字段名、顺序与文件一致）、第 8 条（`data/` 全部样例端到端验收）
- **代码位置**：旧 `forge/reader.py:35-37, 45`
- **当前行为**：表头被 `strip()` 改名；空文件返回 `[]` 虽侥幸正确，但与新管线的边界未统一；样例中 BOM/GBK/UTF-16/引号/NULL/前导零/日期/布尔场景全部无法通过。
- **正确行为**：dict 键严格等于表头原文且保持文件顺序；空文件返回空列表（`skip_bad=True` 时返回 `([], 零值 stats)`）。
- **修复方式**：表头不再 strip，`_build_row()` 按 `zip(header, fields)` 保序构造；`_header_and_rows()` 统一处理空文件。
- **测试用例**：
  `TestStructure::test_output_is_list_of_dicts_in_header_order`、
  `test_all_samples_end_to_end`（覆盖第 8 条列出的全部样例类型）；
  原有 `tests/test_functional.py::test_empty_file_returns_empty_list` 等 4 个用例继续全绿。

---

## 验收结果

```
.venv\Scripts\python -m pytest -q
41 passed
```

- `tests/test_spec.py`：37 个新增规范用例（含 100MB 内存与百万行性能用例）全部通过
- `tests/test_functional.py`：原有 4 个功能用例断言未改，全部通过
- 无第三方依赖，未修改规范文档、样例数据与运行配置
