"""CSV 读取与类型推断。

实现 docs/FORMAT_SPEC.md 的全部要求：
- 自动识别 UTF-8（含 BOM）/ UTF-16 LE/BE（含 BOM）/ GBK
- 支持引号包裹（逗号、换行、回车作为字段内容）与双引号转义
- 空值 / NULL / 引号空串的区分
- int / float / date / bool 类型推断（前导零与科学计数法保留为字符串）
- 列数结构校验（错误带行号）与 skip_bad 容错模式
- iter_csv 流式产出，不把整个文件读进内存
仅使用标准库。
"""

import io
import re
from datetime import date
from pathlib import Path

# 编码嗅探块与流式读取的块大小（64 KiB）
_SNIFF_BYTES = 65536
_READ_CHARS = 65536


class FormatError(Exception):
    """CSV 结构不合法（列数不一致、引号未闭合等）。"""


class DecodeError(Exception):
    """文件编码无法识别或解码失败。"""


# --------------------------------------------------------------------------
# 编码识别
# --------------------------------------------------------------------------
def _open_text(path: Path) -> io.TextIOBase:
    """按 BOM / 内容嗅探编码，返回逐块解码的文本流。

    识别顺序：UTF-16 BOM -> UTF-8 BOM/严格 UTF-8 -> GBK，全部失败抛 DecodeError。
    全程只读开头一小块，不把整个文件载入内存。
    """
    raw = open(path, "rb")
    try:
        head = raw.read(_SNIFF_BYTES)
    except BaseException:
        raw.close()
        raise

    if not head:
        # 空文件：给一个空文本流即可
        raw.close()
        return io.StringIO("")

    # UTF-16 必须带 BOM（规范第 1 条）
    if head.startswith(b"\xff\xfe"):
        encoding, bom_len = "utf-16-le", 2
    elif head.startswith(b"\xfe\xff"):
        encoding, bom_len = "utf-16-be", 2
    elif head.startswith(b"\xef\xbb\xbf"):
        encoding, bom_len = "utf-8-sig", 0  # 自动消费 BOM
    else:
        bom_len = 0
        try:
            head.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            try:
                head.decode("gbk")
                encoding = "gbk"
            except UnicodeDecodeError as exc:
                raw.close()
                raise DecodeError(
                    f"无法识别文件编码：既不是合法 UTF-8 也不是 GBK（{exc}）"
                ) from exc

    raw.seek(0)
    if bom_len:
        raw.read(bom_len)  # 手工跳过 BOM，避免文本流残留
    return io.TextIOWrapper(raw, encoding=encoding, errors="strict", newline="")


# --------------------------------------------------------------------------
# 物理行读取：把文本流切成行，保留行尾的 \r / \n，保留引号内换行
# --------------------------------------------------------------------------
def _iter_physical_lines(text: io.TextIOBase):
    """逐物理行产出 (line_no, line)，line 保留行尾的 \\r / \\n。

    只承认 \\r 与 \\n 为行结束（规范第 2 条），\\v、\\x85 等一律视为字段内容。
    用 str.find 做 C 级扫描，按块读取且正确处理跨行块边界的 \\r\\n。
    行号从 1 开始；最后一行没有换行时照常产出。
    """
    buf = ""
    line_no = 0
    while True:
        chunk = text.read(_READ_CHARS)
        if not chunk:
            break
        buf += chunk
        pos = 0
        while True:
            cr = buf.find("\r", pos)
            lf = buf.find("\n", pos)
            if cr == -1 and lf == -1:
                break
            if cr != -1 and (lf == -1 or cr < lf):
                end = cr + 1
                if buf[cr:cr + 2] == "\r\n":
                    end = cr + 2
            else:
                end = lf + 1
            line_no += 1
            yield line_no, buf[pos:end]
            pos = end
        buf = buf[pos:]
    if buf:
        line_no += 1
        yield line_no, buf


def _strip_line_ending(line: str) -> str:
    """去掉行尾的 \\n、\\r、\\r\\n。"""
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    return line


# --------------------------------------------------------------------------
# 单条记录解析（仅用于含引号的行，含跨行引号字段）
# --------------------------------------------------------------------------
# 状态：FIELD_START=字段开头；PLAIN=普通字段中；QUOTED=引号内；
#       QUOTE_SEEN=引号内刚遇到闭合引号
FIELD_START, PLAIN, QUOTED, QUOTE_SEEN = range(4)


def _parse_quoted_record(lines: list[str], start_line: int):
    """解析一条含引号的记录（可能跨多个物理行）。

    输入为该记录覆盖的物理行列表（保留行尾），返回 (fields, flags, consumed)。
    consumed 为实际消费的物理行数。引号未闭合抛 FormatError。
    """
    state = FIELD_START
    field_chars: list[str] = []
    quoted = False
    fields: list[str] = []
    flags: list[bool] = []
    record_done = False

    def end_field():
        fields.append("".join(field_chars))
        flags.append(quoted)
        field_chars.clear()

    consumed = 0
    for idx, raw_line in enumerate(lines):
        consumed = idx + 1
        content = _strip_line_ending(raw_line)
        for ch in content:
            if state == QUOTED:
                if ch == '"':
                    state = QUOTE_SEEN
                else:
                    field_chars.append(ch)
                continue
            if state == QUOTE_SEEN:
                if ch == '"':  # 转义引号 ""
                    field_chars.append('"')
                    state = QUOTED
                elif ch == ",":
                    end_field()
                    quoted = False
                    state = FIELD_START
                else:
                    raise FormatError(
                        f"第 {start_line + idx} 行：引号闭合后出现多余字符 {ch!r}"
                    )
                continue
            if ch == '"':
                quoted = True
                state = QUOTED
            elif ch == ",":
                end_field()
                quoted = False
                state = FIELD_START
            else:
                field_chars.append(ch)
                state = PLAIN
        # 行结束处理
        if state == QUOTED:
            # 引号内换行：\r\n 规范为两个字符都属字段内容
            if raw_line.endswith("\r\n"):
                field_chars.append("\r\n")
            elif raw_line.endswith("\n"):
                field_chars.append("\n")
            elif raw_line.endswith("\r"):
                field_chars.append("\r")
            continue
        # 引号外：记录在本行结束
        end_field()
        record_done = True
        break

    if not record_done:
        if state == QUOTED:
            raise FormatError(f"第 {start_line} 行：引号未闭合")
        # 文件在引号字段外、无换行结尾处结束：最后一个字段已在循环里收尾
        end_field()
    return fields, flags, consumed


def _iter_records(path: str | Path):
    """逐行产出 (行号, [字段], [quoted 标记])，跳过纯空行。

    无引号的行走 C 级 str.split 快路径；含引号的行走状态机，
    并正确处理引号内跨行字段。解码错误统一包装为 DecodeError。
    """
    text = _open_text(Path(path))
    try:
        line_iter = _iter_physical_lines(text)
        pushback: list[tuple[int, str]] = []
        try:
            while True:
                if pushback:
                    line_no, line = pushback.pop(0)
                else:
                    try:
                        line_no, line = next(line_iter)
                    except StopIteration:
                        return
                body = _strip_line_ending(line)
                if body == "":
                    continue  # 纯空行跳过
                if '"' not in body:
                    # 快路径：无引号，逗号直接切分
                    fields = body.split(",")
                    yield line_no, fields, [False] * len(fields)
                    continue
                # 慢路径：引号可能跨行，持续读取直到引号成对闭合
                group = [line]
                group_start = line_no
                while _count_unescaped_quotes(group) % 2 != 0:
                    try:
                        extra_no, extra_line = next(line_iter)
                    except StopIteration:
                        break
                    group.append(extra_line)
                fields, flags, consumed = _parse_quoted_record(group, group_start)
                # 正常情况下记录恰好消费到闭合行；多余的行放回缓冲
                for extra in range(len(group) - 1, consumed - 1, -1):
                    pushback.insert(0, (group_start + extra, group[extra]))
                if fields:
                    yield group_start, fields, flags
        except UnicodeDecodeError as exc:
            raise DecodeError(f"文件解码失败：{exc}") from exc
    finally:
        text.close()


def _count_unescaped_quotes(lines: list[str]) -> int:
    """统计一组物理行里的引号总数：奇偶即可判断引号是否全部闭合。"""
    return sum(line.count('"') for line in lines)


# --------------------------------------------------------------------------
# 类型推断
# --------------------------------------------------------------------------
# 合并为一次正则匹配，减少每字段的函数调用
_NUMBER_RE = re.compile(r"-?(?:\d+|\d+\.\d+|\d{4}-\d{2}-\d{2})$")


def _infer(text: str, quoted: bool):
    """对单个字段做类型推断（规范第 3、4 条）。"""
    if not quoted:
        if text == "":
            return None  # 完全空字段 ,,
        if text.upper() == "NULL":
            return None  # NULL 大小写不敏感（仅限未加引号的裸字段）
    elif text == "":
        return ""  # "" 显式空字符串

    # 布尔：先按首字符快速排除绝大多数字段
    first = text[0]
    if first in "tTfF":
        low = text.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        return text
    if first in "0123456789-":
        if not _NUMBER_RE.match(text):
            return text  # 科学计数法 1e5、inf、其他形态保持字符串
        if "-" in text[3:]:  # 日期 2024-03-01：第 4 位之后还有 '-'
            try:
                return date.fromisoformat(text)
            except ValueError:
                return text
        if "." in text:
            return float(text)
        digits = text[1:] if first == "-" else text
        if len(digits) > 1 and digits[0] == "0":
            return text  # 前导零 007 / 0123 保留为字符串
        return int(text)
    return text  # 其他形态保持原样，不去空格


def _build_row(header: list[str], fields: list[str], flags: list[bool]) -> dict:
    return {
        name: _infer(cell, quoted)
        for name, cell, quoted in zip(header, fields, flags)
    }


# --------------------------------------------------------------------------
# 对外 API
# --------------------------------------------------------------------------
def _header_and_rows(path: str | Path):
    """生成 (header, 后续记录生成器)；供 read_csv / iter_csv 共用。"""
    records = _iter_records(path)
    try:
        _, header_fields, header_flags = next(records)
    except StopIteration:
        return [], iter(())
    # 表头字段名保持原样（不去空格）；仅引号空串等特殊形态做推断
    header = [
        _infer(cell, quoted) if quoted else cell
        for cell, quoted in zip(header_fields, header_flags)
    ]
    return header, records


def iter_csv(path: str | Path):
    """逐行产出 dict 的流式读取器，不把整个文件读进内存。

    严格模式：列数不齐抛带行号的 FormatError。
    """
    header, records = _header_and_rows(path)
    if not header:
        return
    width = len(header)
    for line_no, fields, flags in records:
        if len(fields) != width:
            raise FormatError(
                f"第 {line_no} 行列数为 {len(fields)}，与表头列数 {width} 不一致"
            )
        yield _build_row(header, fields, flags)


def read_csv(path: str | Path, skip_bad: bool = False):
    """读取整个 CSV。

    默认严格模式，返回 list[dict]；skip_bad=True 时跳过坏行，
    返回 (rows, stats)，stats 含 total / skipped / errors。
    """
    header, records = _header_and_rows(path)
    if not header:
        if skip_bad:
            return [], {"total": 0, "skipped": 0, "errors": []}
        return []

    width = len(header)

    if not skip_bad:
        rows = []
        for line_no, fields, flags in records:
            if len(fields) != width:
                raise FormatError(
                    f"第 {line_no} 行列数为 {len(fields)}，与表头列数 {width} 不一致"
                )
            rows.append(_build_row(header, fields, flags))
        return rows

    rows = []
    total = 0
    skipped = 0
    errors = []
    for line_no, fields, flags in records:
        total += 1
        if len(fields) != width:
            skipped += 1
            errors.append((
                line_no,
                f"列数为 {len(fields)}，与表头列数 {width} 不一致",
            ))
            continue
        try:
            rows.append(_build_row(header, fields, flags))
        except FormatError as exc:
            skipped += 1
            errors.append((line_no, str(exc)))
    return rows, {"total": total, "skipped": skipped, "errors": errors}
