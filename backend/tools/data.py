"""Bounded in-memory CSV/JSON inspection and descriptive analysis tools."""
from __future__ import annotations

import csv
import io
import json
import math
import statistics
import html
from datetime import datetime
from typing import Any

from backend.permissions.policy import PermissionLevel
from backend.tools.registry import ToolRegistry, ToolSpec

MAX_DATA_CHARS = 60_000
MAX_ROWS = 2_000
MAX_COLUMNS = 100


def _csv_rows(content: Any) -> tuple[list[str], list[dict[str, str]]]:
    if not isinstance(content, str) or len(content) > MAX_DATA_CHARS:
        raise ValueError("CSV input must be text under 100 KB")
    reader = csv.DictReader(io.StringIO(content))
    fields = reader.fieldnames or []
    if not fields or len(fields) > MAX_COLUMNS or any(not name for name in fields):
        raise ValueError("CSV requires a valid header and at most 100 columns")
    rows = list(reader)
    if len(rows) > MAX_ROWS:
        raise ValueError("CSV exceeds the 2,000 row limit")
    if any(None in row for row in rows):
        raise ValueError("CSV rows contain more fields than the header")
    return fields, rows


def analyze_csv(content: str) -> dict[str, Any]:
    fields, rows = _csv_rows(content)
    columns: dict[str, Any] = {}
    for field in fields:
        values = []
        missing = 0
        for row in rows:
            raw = (row.get(field) or "").strip()
            if not raw:
                missing += 1
                continue
            try:
                value = float(raw)
                if math.isfinite(value):
                    values.append(value)
            except ValueError:
                pass
        if values:
            columns[field] = {"numeric_count": len(values), "missing_count": missing,
                              "mean": statistics.fmean(values), "median": statistics.median(values),
                              "min": min(values), "max": max(values)}
        else:
            columns[field] = {"numeric_count": 0, "missing_count": missing}
    return {"row_count": len(rows), "column_names": fields, "columns": columns,
            "limitations": ["Descriptive numeric statistics only; nonnumeric values are not inferred."]}


def register_data_tools(registry: ToolRegistry) -> None:
    schema = {"type": "object", "properties": {"content": {"type": "string", "maxLength": MAX_DATA_CHARS}},
              "required": ["content"], "additionalProperties": False}

    def json_inspect(args: dict[str, Any]) -> dict[str, Any]:
        content = args["content"]
        if not isinstance(content, str) or len(content) > MAX_DATA_CHARS:
            raise ValueError("JSON input must be text under 100 KB")
        value = json.loads(content, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite JSON constant: {value}")))
        if isinstance(value, list) and len(value) > MAX_ROWS:
            raise ValueError("JSON array exceeds the 2,000 item limit")
        if isinstance(value, dict) and len(value) > MAX_COLUMNS:
            raise ValueError("JSON object exceeds the 100 key limit")
        return {"root_type": type(value).__name__, "item_count": len(value) if isinstance(value, (list, dict)) else 1,
                "keys": list(value)[:100] if isinstance(value, dict) else [],
                "preview": value[:10] if isinstance(value, list) else value}

    registry.register(ToolSpec("data.csv.analyze", "Validate a bounded CSV string and calculate numeric descriptive statistics.",
        schema, {"type": "object"}, PermissionLevel.READ_ONLY, 5, lambda args: analyze_csv(args["content"])))
    registry.register(ToolSpec("data.json.inspect", "Inspect a bounded JSON string without executing embedded content.",
        schema, {"type": "object"}, PermissionLevel.READ_ONLY, 3, json_inspect))

    transform_schema = {"type":"object","properties":{"content":{"type":"string","maxLength":MAX_DATA_CHARS},
        "operation":{"type":"string","enum":["filter","group_count","sort","clean"]},
        "column":{"type":"string","maxLength":200},"value":{"type":"string","maxLength":500}},
        "required":["content","operation"],"additionalProperties":False}
    def transform(args):
        fields, rows = _csv_rows(args["content"])
        operation, column = args["operation"], args.get("column", "")
        if operation != "clean" and column not in fields: raise ValueError("column does not exist")
        if operation == "clean":
            rows=[{key:(value or "").strip() for key,value in row.items() if key is not None} for row in rows]
            rows=[row for row in rows if any(row.values())]
        elif operation == "filter": rows=[row for row in rows if row.get(column) == args.get("value", "")]
        elif operation == "sort": rows=sorted(rows, key=lambda row: row.get(column, ""))
        else:
            groups={}
            for row in rows: groups[column_value(row.get(column, ""))]=groups.get(column_value(row.get(column, "")),0)+1
            rows=[{column:key,"count":count} for key,count in sorted(groups.items())]
        return {"operation":operation,"rows":rows[:MAX_ROWS],"row_count":len(rows),"source_modified":False}
    registry.register(ToolSpec("data.csv.transform", "Filter, sort, or group a bounded CSV in memory; never edits the source.",
        transform_schema,{"type":"object"},PermissionLevel.READ_ONLY,5,transform))

    trend_schema={"type":"object","properties":{"content":{"type":"string","maxLength":MAX_DATA_CHARS},
        "date_column":{"type":"string","maxLength":200},"value_column":{"type":"string","maxLength":200}},
        "required":["content","date_column","value_column"],"additionalProperties":False}
    def trend(args):
        fields, rows=_csv_rows(args["content"]); date_col=args["date_column"]; value_col=args["value_column"]
        if date_col not in fields or value_col not in fields: raise ValueError("date or numeric value column does not exist")
        points=[]
        for row in rows:
            try:
                stamp=datetime.fromisoformat(row[date_col].replace("Z","+00:00"))
                value=float(row[value_col])
                if math.isfinite(value): points.append((stamp.isoformat(),value))
            except (ValueError,TypeError,AttributeError): continue
        points.sort()
        if not points: raise ValueError("no valid date and numeric-value rows were found")
        return {"points": [{"date":date,"value":value} for date,value in points[:MAX_ROWS]],
                "change":points[-1][1]-points[0][1],"points_count":len(points),
                "limitation":"Endpoint difference only; this is not a forecast."}
    registry.register(ToolSpec("data.csv.trend", "Sort valid ISO dates and report endpoint change without forecasting.",
        trend_schema,{"type":"object"},PermissionLevel.READ_ONLY,5,trend))

    chart_schema={"type":"object","properties":{"content":{"type":"string","maxLength":MAX_DATA_CHARS},
        "label_column":{"type":"string","maxLength":200},"value_column":{"type":"string","maxLength":200}},
        "required":["content","label_column","value_column"],"additionalProperties":False}
    def chart(args):
        fields, rows=_csv_rows(args["content"]); label=args["label_column"]; value=args["value_column"]
        if label not in fields or value not in fields: raise ValueError("chart column does not exist")
        values=[]
        for row in rows[:20]:
            try:
                number=float(row[value])
                if math.isfinite(number): values.append((row[label][:40],number))
            except (TypeError,ValueError): continue
        if not values: raise ValueError("no finite numeric chart values")
        scale=max(abs(value) for _,value in values) or 1
        bars=[]
        for idx,(name,number) in enumerate(values):
            width=max(0,min(400,abs(number)/scale*400)); y=20+idx*24
            bars.append(f'<text x="0" y="{y+14}" font-size="12">{html.escape(name)}</text><rect x="120" y="{y}" width="{width:.2f}" height="18" fill="#337ab7"/><text x="{125+width:.2f}" y="{y+14}" font-size="12">{number:.6g}</text>')
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="560" height="{40+24*len(values)}" viewBox="0 0 560 {40+24*len(values)}">'+''.join(bars)+"</svg>"
        return {"format":"svg","svg":svg,"points":len(values),"source_modified":False}
    registry.register(ToolSpec("data.csv.chart", "Generate a bounded SVG bar chart from supplied CSV values.",
        chart_schema,{"type":"object"},PermissionLevel.READ_ONLY,5,chart))


def column_value(value: str) -> str:
    return str(value)[:500]
