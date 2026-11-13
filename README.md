# csv-forge

内部数据导入用的 CSV 读取器。

## 技术栈

- Python 3.12，纯标准库

## 用法

```python
from forge.reader import read_csv

rows = read_csv("data/basic.csv")
# [{'id': 1, 'name': 'Alice', 'score': 90.5}, ...]
```

## 目录

```
forge/reader.py        读取与类型推断
data/                  各种编码与格式的样例文件
docs/FORMAT_SPEC.md    格式与类型要求
tests/test_functional.py  功能测试
```

## 测试

```bash
.venv/Scripts/python -m pytest -q
```

## 已知问题

- 只测过 UTF-8 的简单文件，其他编码没验证。
- 字段里带逗号或换行时解析会错位。
