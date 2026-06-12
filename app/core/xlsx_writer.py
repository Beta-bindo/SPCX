"""Minimal XLSX writer with optional cell borders."""


from __future__ import annotations
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape


@dataclass
class CellSpec:
    value: str | int | float
    border: bool = False
    bold: bool = False


def _col_letter(col: int) -> str:
    letters = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _cell_xml(col: int, row: int, spec: CellSpec) -> str:
    ref = f"{_col_letter(col)}{row}"
    style = 0
    if spec.border and spec.bold:
        style = 2
    elif spec.border:
        style = 1
    style_attr = f' s="{style}"' if style else ""
    if isinstance(spec.value, (int, float)):
        return f'<c r="{ref}"{style_attr}><v>{spec.value}</v></c>'
    text = escape(str(spec.value))
    return f'<c r="{ref}"{style_attr} t="inlineStr"><is><t>{text}</t></is></c>'


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        "<font/>"
        '<font><b/></font>'
        "</fonts>"
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="2">'
        "<border><left/><right/><top/><bottom/></border>"
        "<border>"
        '<left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/>'
        "</border>"
        "</borders>"
        '<cellXfs count="3">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" applyBorder="1"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="1" applyBorder="1"/>'
        "</cellXfs>"
        "</styleSheet>"
    )


def write_xlsx(path: Path, rows: list[list]) -> None:
    grid = [[CellSpec(v) for v in row] for row in rows]
    write_styled_xlsx(path, grid)


def write_styled_xlsx(path: Path, grid: list[list[CellSpec]], col_widths: list[float] | None = None) -> None:
    sheet_rows: list[str] = []
    for r_idx, row in enumerate(grid, start=1):
        cells = "".join(_cell_xml(c_idx, r_idx, spec) for c_idx, spec in enumerate(row, start=1))
        sheet_rows.append(f'<row r="{r_idx}">{cells}</row>')

    cols_xml = ""
    if col_widths:
        parts = []
        for idx, width in enumerate(col_widths, start=1):
            parts.append(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')
        cols_xml = f"<cols>{''.join(parts)}</cols>"

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{cols_xml}<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="利润记录" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )

    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        "</Relationships>"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/styles.xml", _styles_xml())
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
