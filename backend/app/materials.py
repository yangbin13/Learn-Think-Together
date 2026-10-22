"""Permission-checked page reading and bounded, on-demand PDF rendering."""
from __future__ import annotations

import io
import math
import threading
from pathlib import Path

import pypdfium2 as pdfium


MAX_IMAGE_EDGE = 1800
MAX_IMAGE_PIXELS = 3_000_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
# PDFium cannot be called simultaneously from multiple threads, even for separate documents.
PDFIUM_LOCK = threading.RLock()


class MaterialAccessError(ValueError):
    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


def accessible_document(db, course_id, user_id, document_id):
    doc = db.one("SELECT d.* FROM documents d JOIN courses co ON co.id=d.course_id "
                 "WHERE d.id=? AND d.course_id=? AND (d.user_id IS NULL OR d.user_id=?) "
                 "AND (co.is_builtin=1 OR co.user_id=?)", (document_id, course_id, user_id, user_id))
    if not doc:
        raise MaterialAccessError("当前课程中不存在可读取的教材", 404)
    if doc["status"] != "ready":
        raise MaterialAccessError("教材尚未就绪，请等待解析完成", 409)
    return doc


def checked_page_range(doc, start, end):
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
        raise MaterialAccessError("页码必须是整数")
    if not 1 <= start <= end <= doc["page_count"]:
        raise MaterialAccessError(f"页码超出范围；该教材共有 {doc['page_count']} 页/逻辑段")


def read_material_pages(db, course_id, user_id, document_id, start_page, end_page=None):
    end_page = start_page if end_page is None else end_page
    doc = accessible_document(db, course_id, user_id, document_id)
    checked_page_range(doc, start_page, end_page)
    if end_page - start_page + 1 > 3:
        raise MaterialAccessError("一次最多精读 3 页，请缩小页码范围")
    rows = db.all("SELECT c.id AS chunk_id,c.document_id,c.page,c.section,c.text,d.title,d.source_type "
                  "FROM chunks c JOIN documents d ON d.id=c.document_id "
                  "WHERE c.document_id=? AND c.course_id=? AND (c.user_id IS NULL OR c.user_id=?) "
                  "AND d.status='ready' AND c.page BETWEEN ? AND ? ORDER BY c.page,c.rowid",
                  (document_id, course_id, user_id, start_page, end_page))
    # A physical source page stays independent from its neighbors; do not invent a combined page number.
    return {"items": rows[:18], "start_page": start_page, "end_page": end_page,
            "notice": "只返回所选真实页的文字层。空白/图表页可能无文字；图形须查看原页或调用页图核对，不能补造。"}


def render_pdf_page(doc, physical_page):
    checked_page_range(doc, physical_page, physical_page)
    path = Path(doc["path"])
    if path.suffix.lower() != ".pdf":
        raise MaterialAccessError("页图核对只支持 PDF 教材", 415)
    if not path.is_file():
        raise MaterialAccessError("教材源文件不存在，请重新上传", 404)
    with PDFIUM_LOCK:
        try:
            with pdfium.PdfDocument(path) as pdf:
                if physical_page > len(pdf):
                    raise MaterialAccessError("页码超出实际 PDF 范围")
                page = pdf[physical_page - 1]
                bitmap = None
                image = None
                try:
                    width, height = page.get_size()
                    if not all(math.isfinite(value) and 0 < value <= 1_000_000 for value in (width, height)):
                        raise MaterialAccessError("PDF 页面尺寸异常，无法安全渲染")
                    scale = min(2.5, (MAX_IMAGE_EDGE - 1) / width, (MAX_IMAGE_EDGE - 1) / height,
                                math.sqrt((MAX_IMAGE_PIXELS - MAX_IMAGE_EDGE * 2) / (width * height)))
                    bitmap = page.render(scale=scale)
                    image = bitmap.to_pil().convert("RGB")
                    if max(image.size) > MAX_IMAGE_EDGE or image.width * image.height > MAX_IMAGE_PIXELS:
                        raise MaterialAccessError("PDF 渲染图像超出像素上限")
                    output = io.BytesIO()
                    image.save(output, format="PNG", optimize=True)
                    encoded = output.getvalue()
                    if len(encoded) > MAX_IMAGE_BYTES:
                        raise MaterialAccessError("PDF 页图过大，请下载原文件核验")
                    return {"png": encoded, "width": image.width, "height": image.height, "physical_page": physical_page}
                finally:
                    if image is not None:
                        image.close()
                    if bitmap is not None:
                        bitmap.close()
                    page.close()
        except MaterialAccessError:
            raise
        except Exception:
            raise MaterialAccessError("PDF 页图渲染失败，请下载原文件核验", 422) from None
