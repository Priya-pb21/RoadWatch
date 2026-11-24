from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


HEADERS = (
    "Pothole ID",
    "Created (UTC)",
    "Location",
    "Latitude",
    "Longitude",
    "Severity",
    "Confidence (%)",
    "Verification status",
    "Repair status",
    "Source",
    "Observations",
    "GPS accuracy (m)",
)


def _location_label(record: Mapping[str, object]) -> str:
    for key in ("location_name", "road_name", "region", "address"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = record.get("location")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("name", "address", "region", "road_name"):
            label = value.get(key)
            if isinstance(label, str) and label.strip():
                return label.strip()
    return "Unavailable"


def report_values(record: Mapping[str, object]) -> tuple[object, ...]:
    created_at = record.get("created_at") or record.get("first_detected_at") or ""
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at = created_at.astimezone(timezone.utc).isoformat()
    return (
        record.get("pothole_id") or "ID unavailable",
        created_at,
        _location_label(record),
        record.get("latitude") if record.get("latitude") is not None else "",
        record.get("longitude") if record.get("longitude") is not None else "",
        record.get("severity") or "unknown",
        record.get("confidence_percent") if record.get("confidence_percent") is not None else "",
        record.get("verification_status") or "unknown",
        record.get("repair_status") or "unknown",
        record.get("source") or "historical/unknown",
        record.get("observation_count") or 1,
        record.get("gps_accuracy_m") if record.get("gps_accuracy_m") is not None else "",
    )


def _summary(records: Sequence[Mapping[str, object]]) -> tuple[tuple[str, int], ...]:
    verification = [str(record.get("verification_status") or "unknown") for record in records]
    repair = [str(record.get("repair_status") or "unknown") for record in records]
    return (
        ("Total reports", len(records)),
        ("Verified", sum(status == "verified" for status in verification)),
        ("Pending verification", sum(status in {"pending", "pending_verification", "awaiting_verification", "unverified"} for status in verification)),
        ("Rejected", sum(status == "rejected" for status in verification)),
        ("Under maintenance", sum(status in {"under_maintenance", "inspection_scheduled", "repair_scheduled", "repair_in_progress", "awaiting_repair_verification", "reopened"} for status in repair)),
        ("Repaired", sum(status in {"repaired", "verified", "marked_repaired", "repair_verified"} for status in repair)),
    )


def render_xlsx(
    records: Sequence[Mapping[str, object]],
    *,
    date_range: str,
    filters: Sequence[str],
    generated_at: datetime | None = None,
) -> bytes:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Report summary"
    summary.append(["RoadWatch AI · Pothole report"])
    summary.append(["Report creation date (UTC)", date_range])
    summary.append(["Generated (UTC)", (generated_at or datetime.now(timezone.utc)).isoformat()])
    summary.append(["Filters", " · ".join(filters) if filters else "None"])
    summary.append([])
    summary.append(["Statistic", "Count"])
    for label, count in _summary(records):
        summary.append([label, count])
    summary.freeze_panes = "A7"
    summary.column_dimensions["A"].width = 32
    summary.column_dimensions["B"].width = 64

    heading_fill = PatternFill("solid", fgColor="145B55")
    for cell in summary[1] + summary[6]:
        cell.fill = heading_fill
        cell.font = Font(color="FFFFFF", bold=True)
    for row in summary.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    sheet = workbook.create_sheet("Pothole reports")
    sheet.append(HEADERS)
    for record in records:
        sheet.append(report_values(record))
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = (18, 25, 30, 14, 14, 16, 18, 24, 24, 22, 16, 20)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for cell in sheet[1]:
        cell.fill = heading_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def render_pdf(
    records: Sequence[Mapping[str, object]],
    *,
    date_range: str,
    filters: Sequence[str],
    generated_at: datetime | None = None,
) -> bytes:
    generated = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=20 * mm,
        bottomMargin=14 * mm,
        title="RoadWatch AI Pothole Report",
    )
    styles = getSampleStyleSheet()
    styles["Title"].textColor = colors.HexColor("#145b55")
    styles["BodyText"].fontSize = 8
    styles["BodyText"].leading = 11
    story = [
        Paragraph("RoadWatch AI · Pothole Reports", styles["Title"]),
        Paragraph(f"Report creation date (UTC): {escape(date_range)}", styles["BodyText"]),
        Paragraph(f"Generated (UTC): {generated.isoformat()}", styles["BodyText"]),
        Paragraph(f"Filters: {escape(' · '.join(filters) if filters else 'None')}", styles["BodyText"]),
        Spacer(1, 5 * mm),
    ]
    summary_rows = [["Statistic", "Count"], *[[label, str(count)] for label, count in _summary(records)]]
    summary_table = Table(summary_rows, colWidths=[48 * mm, 18 * mm], repeatRows=1)
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#145b55")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#aab7b5")),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f6f5")]),
            ]
        )
    )
    story.extend([summary_table, Spacer(1, 5 * mm)])

    table_data = [[Paragraph(escape(header), styles["BodyText"]) for header in HEADERS]]
    for record in records:
        table_data.append(
            [
                Paragraph(escape(str(value)), styles["BodyText"])
                for value in report_values(record)
            ]
        )
    column_widths = [19 * mm, 28 * mm, 38 * mm, 17 * mm, 17 * mm, 18 * mm, 21 * mm, 25 * mm, 25 * mm, 25 * mm, 19 * mm, 20 * mm]
    report_table = Table(table_data, colWidths=column_widths, repeatRows=1, splitByRow=1)
    report_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#145b55")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#aab7b5")),
                ("FONTSIZE", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8f8")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(report_table)

    def draw_page(canvas, page_document) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#52625f"))
        canvas.drawString(10 * mm, 10 * mm, "RoadWatch AI · Authorized report")
        canvas.drawRightString(287 * mm, 10 * mm, f"Page {page_document.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return output.getvalue()
