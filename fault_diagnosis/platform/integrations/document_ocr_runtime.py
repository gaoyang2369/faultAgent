"""Document extraction and OCR runtime for uploaded knowledge files."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from fault_diagnosis.platform.paths import PROJECT_ROOT
from fault_diagnosis.platform.settings import (
    DOCUMENT_OCR_BACKEND,
    DOCUMENT_OCR_LANG,
    DOCUMENT_OCR_MAX_PAGES,
    DOCUMENT_OCR_RENDER_DPI,
    PDF_TEXT_MIN_CHARS,
    PDF_TEXT_PREVIEW_CHARS,
)

_HEADING_PATTERN = re.compile(r"^(?:第?[一二三四五六七八九十百千万0-9]+[章节部分篇]|[0-9]+(?:\.[0-9]+){0,4})[\s\u3000]+.+")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_OCR_INSTANCE = None


@dataclass
class DocumentOcrCapabilities:
    provider: str = "document_ocr"
    requested_backend: str = "auto"
    paddleocr_available: bool = False
    pymupdf_available: bool = False
    pillow_available: bool = False
    opencv_available: bool = False
    dependencies_ok: bool = False
    lang: str = "ch"
    max_pages: int = 20
    render_dpi: int = 180
    recommended_mode: str = "pypdf_text"
    notes: list[str] = field(default_factory=list)
    load_tested: bool = False
    last_error: str = ""


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def get_document_ocr_status(load_tested: bool = False) -> dict[str, Any]:
    paddleocr_available = _module_available("paddleocr")
    pymupdf_available = _module_available("fitz")
    pillow_available = _module_available("PIL")
    opencv_available = _module_available("cv2")
    dependencies_ok = paddleocr_available and pillow_available
    notes: list[str] = []
    if not paddleocr_available:
        notes.append("未安装 paddleocr，扫描件和图片无法自动识别。")
    if not pymupdf_available:
        notes.append("未安装 PyMuPDF，扫描 PDF 无法渲染为图片。")
    if not pillow_available:
        notes.append("未安装 Pillow，图片预处理不可用。")
    if not opencv_available:
        notes.append("未安装 opencv-python-headless，将跳过去噪、锐化等增强。")
    recommended_mode = "paddleocr" if dependencies_ok else "pypdf_text"
    return asdict(
        DocumentOcrCapabilities(
            requested_backend=DOCUMENT_OCR_BACKEND,
            paddleocr_available=paddleocr_available,
            pymupdf_available=pymupdf_available,
            pillow_available=pillow_available,
            opencv_available=opencv_available,
            dependencies_ok=dependencies_ok,
            lang=DOCUMENT_OCR_LANG,
            max_pages=DOCUMENT_OCR_MAX_PAGES,
            render_dpi=DOCUMENT_OCR_RENDER_DPI,
            recommended_mode=recommended_mode,
            notes=notes,
            load_tested=load_tested,
        )
    )


def _extract_sections(lines: list[str]) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    current_heading = "正文"
    current_lines: list[str] = []
    for line in lines:
        normalized = line.strip()
        if not normalized:
            continue
        if _HEADING_PATTERN.match(normalized):
            if current_lines:
                sections.append(
                    {
                        "heading": current_heading,
                        "content": "\n".join(current_lines).strip(),
                        "excerpt": " ".join(current_lines)[:240].strip(),
                    }
                )
                current_lines = []
            current_heading = normalized
            continue
        current_lines.append(normalized)
    if current_lines:
        sections.append(
            {
                "heading": current_heading,
                "content": "\n".join(current_lines).strip(),
                "excerpt": " ".join(current_lines)[:240].strip(),
            }
        )
    return sections[:12]


def _pick_title(file_name: str, text: str) -> str:
    for line in text.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate[:120]
    return Path(file_name).stem


def _build_markdown(title: str, file_name: str, page_count: int, full_text: str, backend: str, mode: str) -> str:
    return "\n".join(
        [
            f"# {title}",
            "",
            "## 文档元信息",
            f"- 文件名：{file_name}",
            f"- 页数：{page_count}",
            f"- 解析后端：{backend}",
            f"- 处理模式：{mode}",
            "",
            "## 文档正文",
            "",
            full_text.strip(),
            "",
        ]
    )


def _build_result(
    *,
    file_name: str,
    page_count: int,
    full_text: str,
    page_summaries: list[dict[str, Any]],
    backend: str,
    mode: str,
    status: str,
    status_label: str,
    error: str = "",
    warnings: list[str] | None = None,
    ocr_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    warnings = warnings or []
    title = _pick_title(file_name, full_text)
    structured_result = {
        "title": title,
        "file_name": file_name,
        "page_count": page_count,
        "text_length": len(full_text),
        "preview_text": full_text[:PDF_TEXT_PREVIEW_CHARS],
        "preview_chars": PDF_TEXT_PREVIEW_CHARS,
        "sections": _extract_sections([line.strip() for line in full_text.splitlines() if line.strip()]),
        "page_summaries": page_summaries[:100],
        "ocr_pages": (ocr_pages or [])[:100],
        "extraction_mode": mode,
        "ocr_backend": backend,
        "minimum_text_chars": PDF_TEXT_MIN_CHARS,
        "full_text_saved": True,
        "document_ocr": get_document_ocr_status(load_tested=False) | {"notes": warnings},
    }
    kb_markdown = _build_markdown(title, file_name, page_count, full_text, backend, mode) if full_text.strip() else ""
    return {
        "status": status,
        "status_label": status_label,
        "capabilities": get_document_ocr_status(load_tested=False),
        "ocr_backend": backend,
        "ocr_mode": mode,
        "page_count": page_count,
        "text_length": len(full_text),
        "page_summaries": page_summaries,
        "ocr_pages": ocr_pages or [],
        "raw_text": full_text,
        "structured_result": structured_result,
        "kb_markdown": kb_markdown,
        "warnings": warnings,
        "error": error,
    }


def _extract_pdf_text(pdf_path: str) -> tuple[str, list[dict[str, Any]], int]:
    reader = PdfReader(pdf_path)
    page_texts: list[str] = []
    page_summaries: list[dict[str, Any]] = []
    for index, page in enumerate(reader.pages, start=1):
        extracted = (page.extract_text() or "").strip()
        page_texts.append(extracted)
        page_summaries.append(
            {
                "page_number": index,
                "char_count": len(extracted),
                "has_text": bool(extracted),
                "excerpt": extracted[:240],
            }
        )
    return "\n\n".join(text for text in page_texts if text).strip(), page_summaries, len(reader.pages)


def _ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def _preprocess_image(source_path: str, output_path: str) -> str:
    from PIL import Image, ImageEnhance, ImageFilter

    image = Image.open(source_path).convert("RGB")
    image = ImageEnhance.Contrast(image).enhance(1.35)
    image = image.filter(ImageFilter.SHARPEN)
    image.save(output_path)

    if _module_available("cv2"):
        try:
            import cv2

            img = cv2.imread(output_path)
            if img is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
                sharpened = cv2.addWeighted(denoised, 1.5, cv2.GaussianBlur(denoised, (0, 0), 3), -0.5, 0)
                cv2.imwrite(output_path, sharpened)
        except Exception:
            pass
    return output_path


def _render_pdf_pages(pdf_path: str, output_dir: Path) -> list[str]:
    if not _module_available("fitz"):
        raise RuntimeError("未安装 PyMuPDF，无法将扫描 PDF 渲染为图片。")
    import fitz

    _ensure_clean_dir(output_dir)
    doc = fitz.open(pdf_path)
    zoom = DOCUMENT_OCR_RENDER_DPI / 72
    matrix = fitz.Matrix(zoom, zoom)
    rendered_paths: list[str] = []
    try:
        for index in range(min(len(doc), DOCUMENT_OCR_MAX_PAGES)):
            page = doc[index]
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            output_path = output_dir / f"page_{index + 1:04d}.png"
            pix.save(str(output_path))
            rendered_paths.append(str(output_path))
    finally:
        doc.close()
    return rendered_paths


def _get_paddleocr():
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        if not _module_available("paddleocr"):
            raise RuntimeError("未安装 paddleocr，无法执行本地 OCR。")
        from paddleocr import PaddleOCR

        try:
            _OCR_INSTANCE = PaddleOCR(use_angle_cls=True, lang=DOCUMENT_OCR_LANG, show_log=False)
        except TypeError:
            _OCR_INSTANCE = PaddleOCR(use_angle_cls=True, lang=DOCUMENT_OCR_LANG)
    return _OCR_INSTANCE


def _parse_ocr_result(result: Any) -> tuple[str, float, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    confidences: list[float] = []

    def visit(item: Any) -> None:
        if not item:
            return
        if isinstance(item, (list, tuple)):
            if len(item) >= 2 and isinstance(item[1], (list, tuple)) and len(item[1]) >= 2:
                text = str(item[1][0] or "").strip()
                try:
                    confidence = float(item[1][1])
                except Exception:
                    confidence = 0.0
                if text:
                    rows.append({"text": text, "confidence": confidence, "box": item[0]})
                    confidences.append(confidence)
                return
            for child in item:
                visit(child)
            return
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("rec_text") or "").strip()
            confidence = float(item.get("confidence") or item.get("score") or 0.0)
            if text:
                rows.append({"text": text, "confidence": confidence, "box": item.get("box")})
                confidences.append(confidence)

    visit(result)
    text = "\n".join(row["text"] for row in rows)
    average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return text, average_confidence, rows


def _ocr_images(image_paths: list[str], preprocess_dir: Path) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    if not image_paths:
        return "", [], []
    if not _module_available("PIL"):
        raise RuntimeError("未安装 Pillow，无法预处理图片。")
    preprocess_dir.mkdir(parents=True, exist_ok=True)
    ocr = _get_paddleocr()
    page_texts: list[str] = []
    page_summaries: list[dict[str, Any]] = []
    ocr_pages: list[dict[str, Any]] = []
    for index, image_path in enumerate(image_paths, start=1):
        preprocessed_path = preprocess_dir / f"page_{index:04d}.png"
        _preprocess_image(image_path, str(preprocessed_path))
        result = ocr.ocr(str(preprocessed_path), cls=True)
        text, confidence, rows = _parse_ocr_result(result)
        page_texts.append(text)
        page_summaries.append(
            {
                "page_number": index,
                "char_count": len(text),
                "has_text": bool(text.strip()),
                "excerpt": text[:240],
                "confidence": round(confidence, 4),
            }
        )
        ocr_pages.append(
            {
                "page_number": index,
                "source_image": image_path,
                "preprocessed_image": str(preprocessed_path),
                "confidence": round(confidence, 4),
                "line_count": len(rows),
                "lines": rows[:200],
            }
        )
    return "\n\n".join(text for text in page_texts if text.strip()).strip(), page_summaries, ocr_pages


def extract_document_content(file_path: str, file_name: str, content_type: str | None = None, backend: str | None = None) -> dict[str, Any]:
    requested_backend = (backend or DOCUMENT_OCR_BACKEND or "auto").strip().lower()
    suffix = Path(file_name or file_path).suffix.lower()
    is_pdf = suffix == ".pdf" or (content_type or "").lower() == "application/pdf"
    is_image = suffix in _IMAGE_SUFFIXES or (content_type or "").lower().startswith("image/")
    artifact_root = Path(file_path).parent.parent
    rendered_dir = artifact_root / "rendered_pages" / Path(file_path).stem
    preprocessed_dir = artifact_root / "preprocessed" / Path(file_path).stem

    if is_pdf:
        try:
            text, page_summaries, page_count = _extract_pdf_text(file_path)
        except Exception as exc:
            text, page_summaries, page_count = "", [], 0
            pdf_error = f"PDF 文本层抽取失败：{exc}"
        else:
            pdf_error = ""
        if len(text) >= PDF_TEXT_MIN_CHARS and requested_backend != "paddleocr":
            return _build_result(
                file_name=file_name,
                page_count=page_count,
                full_text=text,
                page_summaries=page_summaries,
                backend="pypdf_text",
                mode="pypdf_text",
                status="needs_review",
                status_label="已提取文本，等待管理员校对",
                warnings=[pdf_error] if pdf_error else [],
            )
        try:
            rendered_paths = _render_pdf_pages(file_path, rendered_dir)
            ocr_text, ocr_summaries, ocr_pages = _ocr_images(rendered_paths, preprocessed_dir)
        except Exception as exc:
            warnings = [message for message in [pdf_error, f"OCR 处理失败：{exc}"] if message]
            return _build_result(
                file_name=file_name,
                page_count=page_count,
                full_text=text,
                page_summaries=page_summaries,
                backend="pypdf_text",
                mode="ocr_required",
                status="failed",
                status_label="OCR 失败",
                error=warnings[-1],
                warnings=warnings,
            )
        status = "needs_review" if ocr_text.strip() else "failed"
        return _build_result(
            file_name=file_name,
            page_count=page_count or len(ocr_summaries),
            full_text=ocr_text,
            page_summaries=ocr_summaries,
            backend="paddleocr",
            mode="pdf_rendered_ocr",
            status=status,
            status_label="OCR 已完成，等待管理员校对" if status == "needs_review" else "OCR 未识别到有效文本",
            error="" if status == "needs_review" else "OCR 未识别到有效文本。",
            warnings=[pdf_error] if pdf_error else [],
            ocr_pages=ocr_pages,
        )

    if is_image:
        try:
            ocr_text, page_summaries, ocr_pages = _ocr_images([file_path], preprocessed_dir)
        except Exception as exc:
            return _build_result(
                file_name=file_name,
                page_count=1,
                full_text="",
                page_summaries=[],
                backend="paddleocr",
                mode="image_ocr",
                status="failed",
                status_label="OCR 失败",
                error=f"OCR 处理失败：{exc}",
                warnings=[f"OCR 处理失败：{exc}"],
            )
        status = "needs_review" if ocr_text.strip() else "failed"
        return _build_result(
            file_name=file_name,
            page_count=1,
            full_text=ocr_text,
            page_summaries=page_summaries,
            backend="paddleocr",
            mode="image_ocr",
            status=status,
            status_label="OCR 已完成，等待管理员校对" if status == "needs_review" else "OCR 未识别到有效文本",
            error="" if status == "needs_review" else "OCR 未识别到有效文本。",
            ocr_pages=ocr_pages,
        )

    return _build_result(
        file_name=file_name,
        page_count=0,
        full_text="",
        page_summaries=[],
        backend="unsupported",
        mode="unsupported",
        status="failed",
        status_label="文件类型不支持",
        error="仅支持 PDF、PNG、JPG、JPEG、WEBP 文件。",
    )


__all__ = [
    "extract_document_content",
    "get_document_ocr_status",
]

