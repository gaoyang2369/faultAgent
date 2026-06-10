"""medicineOCR 轻量探测与 PDF 文本提取 provider 封装。"""

from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pypdf import PdfReader

from ..config import (
    MEDICINE_OCR_BACKEND,
    MEDICINE_OCR_DEVICE,
    MEDICINE_OCR_ENABLE_HEAVY_MODEL,
    MEDICINE_OCR_MAX_PAGES,
    MEDICINE_OCR_MODEL_DIR,
    MEDICINE_OCR_RENDER_DPI,
    MEDICINE_OCR_TIMEOUT_SECONDS,
    PDF_TEXT_EXTRACT_BACKEND,
    PDF_TEXT_MIN_CHARS,
    PDF_TEXT_PREVIEW_CHARS,
)
from ..common.paths import PROJECT_ROOT


_MEDICINE_OCR_DIR = Path(PROJECT_ROOT) / "medicineOCR"
_DEFAULT_YOLO_WEIGHTS = _MEDICINE_OCR_DIR / "yolobest.pt"
_DEFAULT_GTK_INSTALLER = _MEDICINE_OCR_DIR / "gtk3-runtime-3.24.31-2022-01-04-ts-win64.exe"
_HEADING_PATTERN = re.compile(
    r"^(?:第[一二三四五六七八九十百千万0-9]+[章节部分篇]|[0-9]+(?:\.[0-9]+){0,4})[\s\u3000]+.+"
)


@dataclass
class MedicineOcrCapabilities:
    root_exists: bool
    has_extract_script: bool
    has_ocr_script: bool
    has_topdf_script: bool
    has_yolo_weights: bool
    has_gtk_installer: bool
    missing_dependencies: list[str] = field(default_factory=list)
    dependencies_ok: bool = False
    has_cuda: bool = False
    configured: bool = False
    configured_model_path: str = ""
    model_dir_exists: bool = False
    heavy_model_enabled: bool = False
    backend_available: bool = False
    load_tested: bool = False
    last_error: str = ""
    requested_backend: str = "auto"
    requested_text_backend: str = "auto"
    device: str = "auto"
    timeout_seconds: int = 300
    max_pages: int = 1
    render_dpi: int = 120
    recommended_mode: str = "pypdf_text"
    notes: list[str] = field(default_factory=list)


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _detect_cuda() -> bool:
    if not _module_available("torch"):
        return False
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _mask_path_for_status(path: str) -> str:
    if not path:
        return ""
    target = Path(path)
    parts = [part for part in target.parts if part]
    if len(parts) >= 2:
        return os.path.join("...", parts[-2], parts[-1])
    if parts:
        return parts[-1]
    return ""


def _extract_sections(lines: list[str]) -> list[dict]:
    sections: list[dict] = []
    current_heading = "正文"
    current_lines: list[str] = []

    for line in lines:
        normalized = line.strip()
        if not normalized:
            continue
        if _HEADING_PATTERN.match(normalized) and current_lines:
            sections.append(
                {
                    "heading": current_heading,
                    "content": "\n".join(current_lines).strip(),
                    "excerpt": " ".join(current_lines)[:240].strip(),
                }
            )
            current_heading = normalized
            current_lines = []
            continue
        if _HEADING_PATTERN.match(normalized) and not current_lines:
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

    return sections[:12] if sections else []


def _pick_title(file_name: str, lines: list[str]) -> str:
    for line in lines:
        candidate = line.strip()
        if candidate:
            return candidate[:120]
    return Path(file_name).stem


def _build_structured_result(
    *,
    file_name: str,
    page_count: int,
    full_text: str,
    page_summaries: list[dict],
    extraction_mode: str,
    ocr_backend: str,
    capabilities: dict,
    warnings: list[str],
) -> dict:
    lines = [line.strip() for line in full_text.splitlines() if line.strip()]
    title = _pick_title(file_name, lines)
    return {
        "title": title,
        "file_name": file_name,
        "page_count": page_count,
        "text_length": len(full_text),
        "preview_text": full_text[:PDF_TEXT_PREVIEW_CHARS],
        "preview_chars": PDF_TEXT_PREVIEW_CHARS,
        "sections": _extract_sections(lines),
        "page_summaries": page_summaries[:50],
        "extraction_mode": extraction_mode,
        "ocr_backend": ocr_backend,
        "minimum_text_chars": PDF_TEXT_MIN_CHARS,
        "full_text_saved": True,
        "medicine_ocr": {
            "configured": bool(capabilities.get("configured")),
            "available": bool(capabilities.get("backend_available")),
            "heavy_model_enabled": bool(capabilities.get("heavy_model_enabled")),
            "recommended_mode": capabilities.get("recommended_mode"),
            "notes": warnings,
        },
    }


def _build_kb_markdown(
    *,
    title: str,
    file_name: str,
    page_count: int,
    full_text: str,
    ocr_backend: str,
    extraction_mode: str,
) -> str:
    return "\n".join(
        [
            f"# {title}",
            "",
            "## 文档元信息",
            f"- 文件名：{file_name}",
            f"- 页数：{page_count}",
            f"- 解析后端：{ocr_backend}",
            f"- 处理模式：{extraction_mode}",
            "",
            "## 文档正文",
            "",
            full_text,
            "",
        ]
    )


def get_medicine_ocr_status(load_tested: bool = False) -> dict:
    missing_dependencies: list[str] = []
    if not _module_available("modelscope"):
        missing_dependencies.append("modelscope")
    if not _module_available("cv2"):
        missing_dependencies.append("opencv-python")
    if not _module_available("ultralytics"):
        missing_dependencies.append("ultralytics")
    if not _module_available("fitz"):
        missing_dependencies.append("PyMuPDF")

    has_cuda = _detect_cuda()
    configured = bool(MEDICINE_OCR_MODEL_DIR)
    model_dir_exists = configured and os.path.isdir(MEDICINE_OCR_MODEL_DIR)
    dependencies_ok = not missing_dependencies
    backend_available = dependencies_ok and model_dir_exists and has_cuda

    notes: list[str] = [
        "默认链路使用轻量文本提取，不会在服务启动或健康检查时加载重型 OCR 模型。"
    ]
    if not configured:
        notes.append("未配置 MEDICINE_OCR_MODEL_DIR。")
    elif not model_dir_exists:
        notes.append("已配置 MEDICINE_OCR_MODEL_DIR，但目录不存在。")
    if not has_cuda:
        notes.append("当前环境未检测到 CUDA，原始 ocr_test.py 的 `.cuda()` 路径不可直接使用。")
    if not dependencies_ok:
        notes.append("当前环境缺少 medicineOCR 所需的部分依赖。")
    notes.append("原始 ocr_test.py 仅接受图片输入，仓库内没有可直接复用的 PDF 分页渲染总入口。")
    notes.append("topdf.py 依赖 GTK/GObject 运行时，默认部署不应把该链路绑定到主服务启动。")

    if backend_available and MEDICINE_OCR_ENABLE_HEAVY_MODEL:
        recommended_mode = "medicine_ocr_local"
    else:
        recommended_mode = "pypdf_text"

    capabilities = MedicineOcrCapabilities(
        root_exists=_MEDICINE_OCR_DIR.exists(),
        has_extract_script=(_MEDICINE_OCR_DIR / "extract.py").exists(),
        has_ocr_script=(_MEDICINE_OCR_DIR / "ocr_test.py").exists(),
        has_topdf_script=(_MEDICINE_OCR_DIR / "topdf.py").exists(),
        has_yolo_weights=_DEFAULT_YOLO_WEIGHTS.exists(),
        has_gtk_installer=_DEFAULT_GTK_INSTALLER.exists(),
        missing_dependencies=missing_dependencies,
        dependencies_ok=dependencies_ok,
        has_cuda=has_cuda,
        configured=configured,
        configured_model_path=_mask_path_for_status(MEDICINE_OCR_MODEL_DIR),
        model_dir_exists=model_dir_exists,
        heavy_model_enabled=MEDICINE_OCR_ENABLE_HEAVY_MODEL,
        backend_available=backend_available,
        load_tested=load_tested,
        last_error="",
        requested_backend=MEDICINE_OCR_BACKEND,
        requested_text_backend=PDF_TEXT_EXTRACT_BACKEND,
        device=MEDICINE_OCR_DEVICE,
        timeout_seconds=MEDICINE_OCR_TIMEOUT_SECONDS,
        max_pages=MEDICINE_OCR_MAX_PAGES,
        render_dpi=MEDICINE_OCR_RENDER_DPI,
        recommended_mode=recommended_mode,
        notes=notes,
    )
    return asdict(capabilities)


def inspect_medicine_ocr_environment() -> dict:
    """兼容旧调用方的能力探测入口。"""
    return get_medicine_ocr_status(load_tested=False)


def _extract_with_pypdf_text(pdf_path: str, file_name: str) -> dict:
    capabilities = get_medicine_ocr_status(load_tested=False)
    reader = PdfReader(pdf_path)
    page_texts: list[str] = []
    page_summaries: list[dict] = []

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

    full_text = "\n\n".join(text for text in page_texts if text).strip()
    warnings = list(capabilities.get("notes", []))
    text_length = len(full_text)
    if text_length < PDF_TEXT_MIN_CHARS:
        warnings.append(
            f"轻量文本提取仅得到 {text_length} 个字符，低于阈值 {PDF_TEXT_MIN_CHARS}，疑似扫描版或图片型 PDF。"
        )

    structured_result = _build_structured_result(
        file_name=file_name,
        page_count=len(reader.pages),
        full_text=full_text,
        page_summaries=page_summaries,
        extraction_mode="pypdf_text",
        ocr_backend="pypdf_text",
        capabilities=capabilities,
        warnings=warnings,
    )

    result = {
        "status": "text_extracted" if text_length >= PDF_TEXT_MIN_CHARS else "needs_heavy_ocr",
        "status_label": "已提取文本" if text_length >= PDF_TEXT_MIN_CHARS else "文本不足，可能需要重型 OCR",
        "capabilities": capabilities,
        "ocr_backend": "pypdf_text",
        "ocr_mode": "pypdf_text",
        "page_count": len(reader.pages),
        "text_length": text_length,
        "page_summaries": page_summaries,
        "raw_text": full_text,
        "structured_result": structured_result,
        "kb_markdown": (
            _build_kb_markdown(
                title=structured_result["title"],
                file_name=file_name,
                page_count=len(reader.pages),
                full_text=full_text,
                ocr_backend="pypdf_text",
                extraction_mode="pypdf_text",
            )
            if text_length >= PDF_TEXT_MIN_CHARS
            else ""
        ),
        "warnings": warnings,
        "error": (
            ""
            if text_length >= PDF_TEXT_MIN_CHARS
            else "当前 PDF 可提取文本过少，轻量文本路径不足以支撑知识库归档。"
        ),
    }
    return result


def _finalize_heavy_ocr_requirement(base_result: dict) -> dict:
    capabilities = base_result["capabilities"]
    backend_available = bool(capabilities.get("backend_available"))
    heavy_enabled = bool(capabilities.get("heavy_model_enabled"))
    configured = bool(capabilities.get("configured"))
    dependencies_ok = bool(capabilities.get("dependencies_ok"))

    if not heavy_enabled:
        status = "ocr_model_not_configured"
        status_label = "需要重型 OCR（当前未启用模型）"
        error = "该 PDF 可能是扫描件，当前未启用重型 OCR 模型。"
    elif not configured:
        status = "ocr_model_not_configured"
        status_label = "需要重型 OCR（未配置模型目录）"
        error = "该 PDF 可能是扫描件，但未配置 MEDICINE_OCR_MODEL_DIR。"
    elif not capabilities.get("model_dir_exists"):
        status = "ocr_model_not_configured"
        status_label = "需要重型 OCR（模型目录不存在）"
        error = "该 PDF 可能是扫描件，但本地模型目录不存在。"
    elif not dependencies_ok:
        status = "ocr_model_not_configured"
        status_label = "需要重型 OCR（依赖缺失）"
        error = "该 PDF 可能是扫描件，但本地 heavy OCR 依赖未安装完整。"
    elif backend_available:
        status = "needs_heavy_ocr"
        status_label = "需要重型 OCR"
        error = "该 PDF 可能是扫描件；已检测到可用的本地模型环境，但当前主链路不默认自动加载重模型。"
    else:
        status = "needs_heavy_ocr"
        status_label = "需要重型 OCR"
        error = "该 PDF 可能是扫描件，轻量文本提取不足以完成归档。"

    warnings = list(base_result.get("warnings", []))
    warnings.append(error)

    result = dict(base_result)
    result.update(
        {
            "status": status,
            "status_label": status_label,
            "error": error,
            "warnings": warnings,
            "ocr_backend": "pypdf_text",
            "ocr_mode": "auto",
            "kb_markdown": "",
        }
    )
    structured_result = dict(result.get("structured_result") or {})
    structured_result["extraction_mode"] = "pypdf_text"
    structured_result["ocr_backend"] = "pypdf_text"
    structured_result["medicine_ocr"] = {
        "configured": bool(capabilities.get("configured")),
        "available": bool(capabilities.get("backend_available")),
        "heavy_model_enabled": bool(capabilities.get("heavy_model_enabled")),
        "recommended_mode": capabilities.get("recommended_mode"),
        "notes": warnings,
    }
    result["structured_result"] = structured_result
    return result


def _extract_with_medicine_ocr_local(pdf_path: str, file_name: str, base_result: dict | None = None) -> dict:
    del pdf_path
    if base_result is None:
        capabilities = get_medicine_ocr_status(load_tested=False)
        base_result = {
            "capabilities": capabilities,
            "page_count": 0,
            "page_summaries": [],
            "raw_text": "",
            "structured_result": _build_structured_result(
                file_name=file_name,
                page_count=0,
                full_text="",
                page_summaries=[],
                extraction_mode="medicine_ocr_local",
                ocr_backend="medicine_ocr_local",
                capabilities=capabilities,
                warnings=[],
            ),
            "warnings": [],
        }
    return _finalize_heavy_ocr_requirement(base_result)


def extract_pdf_content(pdf_path: str, file_name: str, backend: str | None = None) -> dict:
    """根据配置选择 provider，默认只走轻量文本提取与安全降级。"""

    requested_backend = (backend or MEDICINE_OCR_BACKEND or "auto").strip().lower()
    text_backend = PDF_TEXT_EXTRACT_BACKEND

    if requested_backend == "pypdf_text":
        base_result = _extract_with_pypdf_text(pdf_path, file_name)
        if base_result["status"] == "text_extracted":
            return base_result
        return _finalize_heavy_ocr_requirement(base_result)

    if requested_backend == "medicine_ocr_local":
        base_result = _extract_with_pypdf_text(pdf_path, file_name)
        if base_result["status"] == "text_extracted":
            return base_result
        return _extract_with_medicine_ocr_local(pdf_path, file_name, base_result=base_result)

    if text_backend == "pypdf_text":
        base_result = _extract_with_pypdf_text(pdf_path, file_name)
        if base_result["status"] == "text_extracted":
            return base_result
        return _finalize_heavy_ocr_requirement(base_result)

    base_result = _extract_with_pypdf_text(pdf_path, file_name)
    if base_result["status"] == "text_extracted":
        return base_result
    return _extract_with_medicine_ocr_local(pdf_path, file_name, base_result=base_result)


def parse_pdf_with_fallback(pdf_path: str, file_name: str) -> dict:
    """兼容旧测试的成功路径包装；失败时抛出明确错误。"""
    result = extract_pdf_content(pdf_path, file_name)
    if result["status"] != "text_extracted":
        raise RuntimeError(result.get("error") or result.get("status_label") or "PDF 文本提取失败。")
    return result
