"""CSV 读取与类型推断。"""

from pathlib import Path


class FormatError(Exception):
    pass


class DecodeError(Exception):
    pass


def _infer(text: str):
    if text == "":
        return None
    if text.strip().upper() == "NULL":
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    return text


def read_csv(path: str | Path) -> list[dict]:
    content = Path(path).read_text(encoding="utf-8")
    lines = content.splitlines()
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split(",")
        if len(cells) != len(header):
            raise FormatError(f"column count mismatch: {line}")
        rows.append({name: _infer(cell) for name, cell in zip(header, cells)})
    return rows
