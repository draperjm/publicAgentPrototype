"""
Tool: extract_pdf_content
Extracts structured content from a PDF file including text, tables, and images.
Automatically detects document sections from headings/numbering and generates
an LLM summary for each section.
Follows the Claude tool-use pattern: DEFINITION + run().
"""

import os
import re
import base64
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

from openai import OpenAI


# ── Claude tool definition ─────────────────────────────────────────────────────
DEFINITION = {
    "name": "extract_pdf_content",
    "description": (
        "Extracts structured content from a PDF file, including text, tables, and images. "
        "Automatically detects document sections from heading patterns and numbering. "
        "For each section returns: section_number, section_name, page_number, "
        "text content, extracted tables (as rows/headers), image descriptions "
        "(analysed via vision LLM), and an LLM-generated summary. "
        "Use for deep analysis of technical reports, design briefs, and engineering documents."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "Absolute path to the PDF file to extract.",
            },
            "max_pages": {
                "type": "integer",
                "description": (
                    "Optional maximum number of pages to process. "
                    "Defaults to all pages. Use to limit cost on very large documents."
                ),
            },
        },
        "required": ["filepath"],
    },
}


# ── Section heading patterns ────────────────────────────────────────────────────
# Numbered:    "1.2.3 Heading Text"   (up to 4 levels deep)
_NUMBERED_RE = re.compile(
    r"^\s*(\d+(?:\.\d+){0,3})\s+([A-Z][A-Za-z0-9 ,\-/&:()\[\]']{2,70})\s*$"
)
# All-caps:    "GENERAL REQUIREMENTS" (at least 2 words, not a sentence)
_ALLCAPS_RE = re.compile(r"^\s*([A-Z][A-Z0-9 \-/&]{3,60})\s*$")


# ── Internal helpers ───────────────────────────────────────────────────────────
def _client() -> OpenAI:
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def _text_model() -> str:
    return os.environ.get("LLM_MODEL", "gpt-4o-mini")


def _vision_model() -> str:
    # Allow override; default to gpt-4o for better vision quality
    return os.environ.get("VISION_MODEL", "gpt-4o")


def _page_to_b64(page) -> Optional[str]:
    """Render a pdfplumber page to a base64-encoded PNG string."""
    try:
        img_obj = page.to_image(resolution=120)
        buf = BytesIO()
        img_obj.original.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        return None


def _describe_page_images(b64_png: str, page_number: int) -> Optional[str]:
    """
    Call the vision LLM to describe figures/diagrams/photos on a rendered page.
    Returns None if the model says no significant images are present.
    """
    try:
        resp = _client().chat.completions.create(
            model=_vision_model(),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"You are reviewing page {page_number} of a technical document. "
                                "Describe any figures, diagrams, engineering drawings, charts, maps, "
                                "or photographs visible on this page. For each image, state what it "
                                "shows and its apparent purpose in the document. "
                                "If there are no significant images (only text or page borders), "
                                "respond with exactly: 'No significant images.'"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64_png}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            max_tokens=500,
        )
        description = resp.choices[0].message.content.strip()
        if description.startswith("No significant images"):
            return None
        return description
    except Exception as e:
        return f"[Image description error: {e}]"


def _summarise_section(
    section_label: str,
    text: str,
    table_count: int,
    image_descriptions: List[str],
) -> str:
    """Generate a concise 2–4 sentence summary of a section using the text LLM."""
    extras_parts = []
    if table_count:
        extras_parts.append(f"{table_count} table(s)")
    if image_descriptions:
        descs_preview = "; ".join(d[:120] for d in image_descriptions[:2])
        extras_parts.append(f"{len(image_descriptions)} image(s): {descs_preview}")
    extras_note = (
        f"\n\nThe section also contains: {', '.join(extras_parts)}." if extras_parts else ""
    )

    prompt = (
        f"Summarise the following document section in 2–4 concise sentences. "
        f"Be specific about key facts, requirements, constraints, or findings.\n\n"
        f"Section: {section_label}\n\n"
        f"Content:\n{text[:3500]}{extras_note}"
    )
    try:
        resp = _client().chat.completions.create(
            model=_text_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=250,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"[Summary error: {e}]"


def _parse_heading(line: str) -> Optional[tuple]:
    """
    Check whether a line is a section heading.
    Returns (section_number_or_None, section_name) or None if not a heading.
    """
    stripped = line.strip()
    if not stripped:
        return None

    m = _NUMBERED_RE.match(stripped)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    m2 = _ALLCAPS_RE.match(stripped)
    if m2 and len(m2.group(1).split()) >= 2:
        # Exclude short acronyms and common false-positives
        candidate = m2.group(1).strip()
        if len(candidate) > 6 and not re.match(r"^[A-Z]{2,6}$", candidate):
            return None, candidate

    return None


# ── Tool implementation ────────────────────────────────────────────────────────
def run(filepath: str, max_pages: Optional[int] = None) -> dict:
    """
    Extract structured content from a PDF file.

    Returns:
        {
            "filepath": str,
            "filename": str,
            "total_pages": int,
            "pages_processed": int,
            "total_sections": int,
            "sections": [
                {
                    "section_number": str | null,
                    "section_name": str,
                    "page_number": int,
                    "contents": {
                        "text": str,
                        "tables": [
                            {
                                "table_number": int,
                                "headers": [str, ...],
                                "rows": [[str, ...], ...]
                            }
                        ],
                        "images": [
                            {
                                "page_number": int,
                                "description": str
                            }
                        ]
                    },
                    "summary": str
                }
            ]
        }
        or on error:
        {
            "error": str,
            "sections": []
        }
    """
    try:
        import pdfplumber
    except ImportError:
        return {"error": "pdfplumber is not installed.", "sections": []}

    path = Path(filepath)
    if not path.exists():
        return {"error": f"File not found: {filepath}", "sections": []}
    if path.suffix.lower() != ".pdf":
        return {"error": f"Not a PDF file: {filepath}", "sections": []}

    try:
        with pdfplumber.open(str(path)) as pdf:
            total_pages = len(pdf.pages)
            pages_to_process = pdf.pages[:max_pages] if max_pages else pdf.pages

            # ── Pass 1: extract raw content from each page ─────────────────────
            page_data: List[Dict] = []

            for page in pages_to_process:
                page_num = page.page_number

                # Text
                raw_text = page.extract_text() or ""

                # Tables
                raw_tables = page.extract_tables() or []
                tables_out = []
                for tbl_idx, tbl in enumerate(raw_tables, start=1):
                    if not tbl:
                        continue
                    headers = [str(c).strip() if c is not None else "" for c in tbl[0]]
                    rows = [
                        [str(c).strip() if c is not None else "" for c in row]
                        for row in tbl[1:]
                        if any(c for c in row)
                    ]
                    tables_out.append({
                        "table_number": tbl_idx,
                        "headers": headers,
                        "rows": rows,
                    })

                # Images — render page only when pdfplumber detects embedded images
                image_description = None
                if page.images:
                    b64 = _page_to_b64(page)
                    if b64:
                        image_description = _describe_page_images(b64, page_num)

                page_data.append({
                    "page_number": page_num,
                    "text": raw_text,
                    "tables": tables_out,
                    "image_description": image_description,
                })

            # ── Pass 2: detect sections from page text ─────────────────────────
            sections: List[Dict] = []
            current: Dict = {
                "section_number": None,
                "section_name": "Document Preamble",
                "start_page": page_data[0]["page_number"] if page_data else 1,
                "page_data": [],
            }

            for pd in page_data:
                heading_found = False
                for line in pd["text"].splitlines():
                    result = _parse_heading(line)
                    if result is None:
                        continue
                    sec_num, sec_name = result
                    # Require either a number or a multi-word all-caps heading
                    if sec_num is not None or (sec_name and len(sec_name.split()) >= 2):
                        sections.append(current)
                        current = {
                            "section_number": sec_num,
                            "section_name": sec_name,
                            "start_page": pd["page_number"],
                            "page_data": [pd],
                        }
                        heading_found = True
                        break  # one heading per page scan

                if not heading_found:
                    current["page_data"].append(pd)

            sections.append(current)

            # ── Pass 3: assemble output + generate summaries ───────────────────
            output_sections = []

            for sec in sections:
                pages = sec["page_data"]

                # Skip empty preamble with no content
                if (
                    not pages
                    and sec["section_number"] is None
                    and sec["section_name"] == "Document Preamble"
                ):
                    continue

                combined_text = "\n\n".join(
                    p["text"] for p in pages if p["text"]
                ).strip()

                all_tables: List[Dict] = []
                for p in pages:
                    all_tables.extend(p["tables"])

                all_images: List[Dict] = []
                for p in pages:
                    if p.get("image_description"):
                        all_images.append({
                            "page_number": p["page_number"],
                            "description": p["image_description"],
                        })

                # Generate summary only if there is content
                summary = ""
                if combined_text or all_tables or all_images:
                    section_label = (
                        f"{sec['section_number']} {sec['section_name']}"
                        if sec["section_number"]
                        else sec["section_name"]
                    )
                    summary = _summarise_section(
                        section_label=section_label,
                        text=combined_text,
                        table_count=len(all_tables),
                        image_descriptions=[img["description"] for img in all_images],
                    )

                output_sections.append({
                    "section_number": sec["section_number"],
                    "section_name": sec["section_name"],
                    "page_number": sec["start_page"],
                    "contents": {
                        "text": combined_text,
                        "tables": all_tables,
                        "images": all_images,
                    },
                    "summary": summary,
                })

    except Exception as e:
        return {"error": f"Failed to process PDF: {e}", "sections": []}

    return {
        "filepath": str(path),
        "filename": path.name,
        "total_pages": total_pages,
        "pages_processed": len(page_data),
        "total_sections": len(output_sections),
        "sections": output_sections,
    }
