"""Atomic paging arithmetic: never reuse an offset from a different request."""
import re


MAX_ADDRESS = 2**53 - 1
MAX_INDEX = 2**32 - 1


def bounded_integer(value, name, maximum, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name}必须是{minimum}–{maximum}范围内的整数")
    return value


def translate_paging_address(logical_address, page_size_bytes, page_table):
    address = bounded_integer(logical_address, "逻辑地址", MAX_ADDRESS)
    size = bounded_integer(page_size_bytes, "页大小（字节）", 2**32, 1)
    if not isinstance(page_table, (dict, list)) or not page_table or len(page_table) > 4096:
        raise ValueError("page_table须为1–4096项的非空数组，每项明确填写page和frame，例如[{\"page\":7,\"frame\":11}]；不能传{}或省略题给映射。仅使用原题参数，不填推算结果")
    mapping = {}
    if isinstance(page_table, list) and any(isinstance(entry, dict) for entry in page_table):
        if not all(isinstance(entry, dict) and set(entry) == {"page", "frame"} for entry in page_table):
            raise ValueError("page_table记录数组的每项必须仅有page和frame两个字段；不能混用索引数组、缺字段或添加其它字段")
        entries = ((bounded_integer(entry["page"], "页号", MAX_INDEX), entry["frame"]) for entry in page_table)
    else:
        # Compatibility for existing deterministic Python callers. The model
        # schema exposes only explicit records, avoiding dynamic object keys.
        entries = enumerate(page_table) if isinstance(page_table, list) else page_table.items()
    for key, frame in entries:
        if isinstance(key, str):
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise ValueError("页表键必须是非负整数页号，不接受前导零或小数")
            key = int(key)
        page = bounded_integer(key, "页号", MAX_INDEX)
        if page in mapping:
            raise ValueError("页表包含重复页号")
        mapping[page] = None if frame is None else bounded_integer(frame, "页框号", MAX_INDEX)
    page, offset = divmod(address, size)
    if page > MAX_INDEX:
        raise ValueError("逻辑页号超出支持的页表索引范围")
    frame = mapping.get(page)
    if frame is None:
        return {"logical_address": address, "page_size_bytes": size, "page": page, "offset": offset,
                "frame": None, "physical_address": None, "present": False,
                "reason": "页表未提供该逻辑页映射" if page not in mapping else "该页映射明确为null，不能确定驻留页框",
                "verified_by": "integer_divmod_and_page_table", "assumption": "输入逻辑地址为字节地址；只使用本次页大小和页表，不推测缺失映射"}
    physical = frame * size + offset
    if physical > MAX_ADDRESS:
        raise ValueError("物理地址超出支持的整数地址上限")
    return {"logical_address": address, "page_size_bytes": size, "page": page, "offset": offset,
            "frame": frame, "physical_address": physical, "present": True,
            "formula": "page,offset=divmod(logical_address,page_size_bytes); physical_address=frame*page_size_bytes+offset",
            "verified_by": "integer_divmod_and_page_table", "assumption": "输入为字节地址，页表映射有效；未额外计入页表读取或TLB时间"}


def paging_argument_shape(arguments):
    """Allowlisted types/counts only; never retain unknown field names/values."""
    allowed = ("logical_address", "page_size_bytes", "page_table")

    def kind(value):
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return "array"
        return "other"

    result = {key + "_type": kind(arguments[key]) if key in arguments else "missing" for key in allowed}
    table = arguments.get("page_table")
    if isinstance(table, (dict, list)):
        result["page_table_entries"] = min(len(table), 4097)
    if isinstance(table, list):
        entries = table[:4097]
        result["page_table_entry_types"] = sorted({kind(entry) for entry in entries})
        records = [entry for entry in entries if isinstance(entry, dict)]
        if records:
            result["page_table_record_fields"] = sorted({key for entry in records for key in entry if key in {"page", "frame"}})
            result["page_table_unknown_fields"] = any(set(entry) - {"page", "frame"} for entry in records)
    return result
