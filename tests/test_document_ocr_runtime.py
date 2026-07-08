from __future__ import annotations

from fault_diagnosis.platform.integrations import document_ocr_runtime as runtime


def test_pdf_text_layer_skips_ocr(monkeypatch, tmp_path) -> None:
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(
        runtime,
        "_extract_pdf_text",
        lambda _path: ("A" * 150, [{"page_number": 1, "char_count": 150, "has_text": True}], 1),
    )

    def fail_render(*_args, **_kwargs):
        raise AssertionError("OCR render should not run for text PDFs")

    monkeypatch.setattr(runtime, "_render_pdf_pages", fail_render)
    result = runtime.extract_document_content(str(pdf_path), "manual.pdf", "application/pdf")

    assert result["status"] == "needs_review"
    assert result["ocr_backend"] == "pypdf_text"
    assert result["kb_markdown"]


def test_scanned_pdf_uses_rendered_page_ocr(monkeypatch, tmp_path) -> None:
    pdf_path = tmp_path / "manual.pdf"
    image_path = tmp_path / "page.png"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(runtime, "_extract_pdf_text", lambda _path: ("", [], 1))
    monkeypatch.setattr(runtime, "_render_pdf_pages", lambda _path, _out: [str(image_path)])
    monkeypatch.setattr(
        runtime,
        "_ocr_images",
        lambda paths, _out: ("识别文本", [{"page_number": 1, "char_count": 4, "has_text": True}], [{"page_number": 1}]),
    )

    result = runtime.extract_document_content(str(pdf_path), "manual.pdf", "application/pdf")

    assert result["status"] == "needs_review"
    assert result["ocr_backend"] == "paddleocr"
    assert result["ocr_mode"] == "pdf_rendered_ocr"
    assert "识别文本" in result["kb_markdown"]


def test_image_upload_uses_ocr(monkeypatch, tmp_path) -> None:
    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(
        runtime,
        "_ocr_images",
        lambda paths, _out: ("图片文本", [{"page_number": 1, "char_count": 4, "has_text": True}], [{"page_number": 1}]),
    )

    result = runtime.extract_document_content(str(image_path), "photo.png", "image/png")

    assert result["status"] == "needs_review"
    assert result["ocr_backend"] == "paddleocr"
    assert result["ocr_mode"] == "image_ocr"

