"""Minimal dependency-free .xlsx reader (standard library only).

Returns a workbook as ``OrderedDict[sheet_name, rows]`` where ``rows`` is a
``list[list[str]]``. Every cell is returned as a string (empty string for blank
cells) so callers decide how to parse numbers. Shared strings, inline strings,
booleans and numeric cells are all resolved.
"""
from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from collections import OrderedDict

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

R_ID = f"{{{NS_OFFICE_REL}}}id"


def _col_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n  # 1-based column number


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return ["".join(si.itertext()) for si in root.findall(f"{{{NS_MAIN}}}si")]


def _read_sheet(zf: zipfile.ZipFile, path: str, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(zf.read(path))
    rows: list[list[str]] = []
    sheet_data = root.find(f"{{{NS_MAIN}}}sheetData")
    if sheet_data is None:
        return rows
    for row_el in sheet_data.findall(f"{{{NS_MAIN}}}row"):
        cells: dict[int, str] = {}
        max_col = 0
        for c in row_el.findall(f"{{{NS_MAIN}}}c"):
            ref = c.get("r") or ""
            if not ref:
                continue
            t = c.get("t")
            val = ""
            if t == "inlineStr":
                is_el = c.find(f"{{{NS_MAIN}}}is")
                val = "".join(is_el.itertext()) if is_el is not None else ""
            else:
                v_el = c.find(f"{{{NS_MAIN}}}v")
                val = (v_el.text or "") if v_el is not None else ""
                if t == "s" and val != "":
                    idx = int(val)
                    val = shared[idx] if 0 <= idx < len(shared) else ""
            col = _col_index(ref)
            cells[col] = val
            if col > max_col:
                max_col = col
        row_vals = [""] * max_col
        for col, val in cells.items():
            row_vals[col - 1] = val
        rows.append(row_vals)
    return rows


def read_xlsx(path: str) -> "OrderedDict[str, list[list[str]]]":
    with zipfile.ZipFile(path) as zf:
        shared = _shared_strings(zf)
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        sheets = []
        for s in wb.findall(f"{{{NS_MAIN}}}sheets/{{{NS_MAIN}}}sheet"):
            sheets.append((s.get("name"), s.get(R_ID)))

        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {}
        for rel in rels.findall(f"{{{NS_PKG_REL}}}Relationship"):
            rel_map[rel.get("Id")] = rel.get("Target")

        out: "OrderedDict[str, list[list[str]]]" = OrderedDict()
        for name, rid in sheets:
            target = rel_map.get(rid) or f"worksheets/{name}.xml"
            if not target.startswith("xl/"):
                target = target.lstrip("/")
                if not target.startswith("xl/"):
                    target = "xl/" + target
            out[name] = _read_sheet(zf, target, shared)
    return out
