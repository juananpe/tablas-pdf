"""Local PDF table workbench. Run: uvicorn app:app --host 127.0.0.1 --port 8765."""
from pathlib import Path
from threading import RLock
import re
import asyncio
from contextlib import asynccontextmanager
import shutil
import tempfile
import time
import uuid

import pymupdf as fitz
import pdfplumber
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent
STORAGE = Path(tempfile.mkdtemp(prefix="pdf-table-app-"))
MAX_BYTES = 50 * 1024 * 1024
TTL = 3600
DOCUMENTS = {}
LOCK = RLock()
def cleanup_expired():
    with LOCK:
        for key, item in list(DOCUMENTS.items()):
            if time.time() - item["touched"] > TTL:
                shutil.rmtree(item["path"].parent, ignore_errors=True)
                del DOCUMENTS[key]


@asynccontextmanager
async def lifespan(app):
    async def housekeeping():
        while True:
            await asyncio.sleep(60)
            await run_in_threadpool(cleanup_expired)
    task = asyncio.create_task(housekeeping())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        shutil.rmtree(STORAGE, ignore_errors=True)


app = FastAPI(title="Tablas PDF", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


def get_document(document_id):
    cleanup_expired()
    with LOCK:
        item = DOCUMENTS.get(document_id)
        if not item:
            raise HTTPException(404, "El PDF ha caducado. Vuelve a cargarlo.")
        item["touched"] = time.time()
        return item.copy()


@app.post("/api/documents")
async def upload(file: UploadFile = File(...)):
    document_id = uuid.uuid4().hex
    directory = STORAGE / document_id
    directory.mkdir()
    path = directory / "document.pdf"
    try:
        size = 0
        with path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "El límite es de 50 MB por PDF.")
                output.write(chunk)
        def inspect():
            with fitz.open(path) as pdf:
                if not pdf.is_pdf:
                    raise HTTPException(400, "El archivo no es un PDF.")
                if pdf.needs_pass:
                    raise HTTPException(400, "El PDF está protegido con contraseña.")
                if not 1 <= len(pdf) <= 300:
                    raise HTTPException(400, "Carga un PDF de entre 1 y 300 páginas.")
                return len(pdf)
        count = await run_in_threadpool(inspect)
        with LOCK:
            DOCUMENTS[document_id] = {"path": path, "name": Path(file.filename or "document.pdf").name,
                                      "pages": count, "touched": time.time()}
        return {"id": document_id, "nombre": DOCUMENTS[document_id]["name"],
                "paginas": count, "bytes": size}
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(400, "No se ha podido abrir el PDF. Comprueba que no esté dañado.") from exc
    finally:
        await file.close()


@app.get("/api/documents/{document_id}/pages/{page}/thumbnail")
def thumbnail(document_id: str, page: int):
    document = get_document(document_id)
    if not 1 <= page <= document["pages"]:
        raise HTTPException(400, "Página fuera de rango.")
    with fitz.open(document["path"]) as pdf:
        current = pdf[page - 1]
        scale = min(420 / current.rect.width, 540 / current.rect.height)
        pixmap = current.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return Response(pixmap.tobytes("png"), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=3600"})


class ExtractionRequest(BaseModel):
    paginas: list[int] = Field(min_length=1, max_length=300)


def clean(cell):
    return re.sub(r"\s+", " ", cell or "").strip()


def enrollment_header(rows):
    for i, row in enumerate(rows):
        if len(row) != 7:
            continue
        values = [clean(c).lower() for c in row]
        if ("asignatura" in values[0] or "irakasgaia" in values[0]) and \
           "binario" in values[1] and values[2:4] == ["e", "g"] and \
           "total" in values[4] and values[5:] == ["e", "g"]:
            return i
    return None


def valid_enrollment_row(row):
    if len(row) != 7 or not clean(row[0]):
        return False
    return all(re.fullmatch(r"\d+", clean(row[i])) for i in [2, 3, 4]) and \
        (not clean(row[1]) or re.fullmatch(r"\d+", clean(row[1]))) and \
        all(re.fullmatch(r"\d+(?:[.,]\d+)?\s*%", clean(row[i])) for i in [5, 6])


def course_from_context(text):
    # Associate the year with the paragraph about the data, not the publication footer.
    match = re.search(r"(?:curso|ikasturte)\s+(20\d{2})\s*[-–/]\s*(\d{2}|20\d{2})", text, re.I)
    if not match:
        match = re.search(r"(20\d{2})\s*[-–/]\s*(\d{2}|20\d{2})\s+ikasturtean", text, re.I)
    return f"{match[1]}-{match[2][-2:]}" if match else None


def same_columns(first, second):
    return len(first) == len(second) and all(abs(a - b) < 3 for a, b in zip(first, second))


def extract_document(document, pages):
    tables, records, totals, warnings = [], [], [], []
    with pdfplumber.open(document["path"]) as pdf:
        cache = {}
        def page_tables(index):
            if index not in cache:
                cache[index] = [(table, table.extract()) for table in pdf.pages[index].find_tables()]
            return cache[index]

        def schema_for(index, table, rows):
            header = enrollment_header(rows)
            if header is not None:
                return index, header
            # Only inherit from an immediately preceding compatible table. This also
            # works when only the continuation page is selected by the user.
            if index == 0 or not rows or not all(valid_enrollment_row(row) for row in rows):
                return None
            x_positions = sorted({cell[0] for cell in table.rows[-1].cells if cell})
            for previous, previous_rows in reversed(page_tables(index - 1)):
                prev_x = sorted({cell[0] for cell in previous.rows[-1].cells if cell})
                prev_header = enrollment_header(previous_rows)
                if prev_header is not None and same_columns(x_positions, prev_x):
                    return index - 1, -1
            return None

        for number in pages:
            page = pdf.pages[number - 1]
            found = page_tables(number - 1)
            if not found:
                warnings.append(f"Página {number}: no se detectaron tablas con líneas. Si es un escaneo, necesita OCR.")
            for table_index, (table, rows) in enumerate(found, 1):
                table_id = f"p{number}-t{table_index}"
                raw = {"id": table_id, "pagina": number, "bbox": [round(n, 2) for n in table.bbox],
                       "filas": rows, "esquema": "celdas_originales"}
                tables.append(raw)
                schema = schema_for(number - 1, table, rows)
                if not schema:
                    continue
                context_index, header_index = schema
                context_page = pdf.pages[context_index]
                context_tables = page_tables(context_index)
                header_table = next((t for t, r in context_tables if enrollment_header(r) is not None), table)
                context = context_page.crop((0, 0, context_page.width, header_table.bbox[1])).extract_text() or ""
                course = course_from_context(context)
                # E/G is supported for this bilingual schema, with the source note
                # confirming the meaning. Do not silently generalize abbreviations.
                note = " ".join((pdf.pages[i].extract_text() or "") for i in range(context_index, min(context_index + 2, len(pdf.pages))))
                if not ("emakume" in note.lower() and "gizone" in note.lower()):
                    warnings.append(f"Tabla {table_id}: no se pudo confirmar el significado de E/G; se conservan las celdas originales.")
                    continue
                raw["esquema"] = "matriculas_por_sexo"
                raw["curso_datos"] = course
                if not course:
                    warnings.append(f"Tabla {table_id}: curso de los datos no identificado.")
                for row in rows[header_index + 1:]:
                    if not valid_enrollment_row(row):
                        warnings.append(f"Tabla {table_id}: fila no normalizada; revisa las celdas originales.")
                        continue
                    women, men, total = (int(clean(row[i])) for i in [2, 3, 4])
                    nb = int(clean(row[1])) if clean(row[1]) else None
                    record = {"asignatura": clean(row[0]), "curso_datos": course,
                              "mujeres": women, "hombres": men, "no_binario": nb, "total": total,
                              "porcentaje_mujeres": float(clean(row[5]).replace("%", "").replace(",", ".")),
                              "porcentaje_hombres": float(clean(row[6]).replace("%", "").replace(",", ".")),
                              "pagina": number, "tabla": "Distribución del alumnado por sexo",
                              "tabla_id": table_id, "documento": document["name"]}
                    if women + men + (nb or 0) != total:
                        warnings.append(f"{record['asignatura']}, página {number}: los recuentos no coinciden con el total.")
                    for count, percentage in [(women, record["porcentaje_mujeres"]), (men, record["porcentaje_hombres"])]:
                        if total and abs(100 * count / total - percentage) > 0.015:
                            warnings.append(f"{record['asignatura']}, página {number}: porcentaje inconsistente.")
                    if re.search(r"\b(total|guztira)\b", record["asignatura"], re.I):
                        totals.append(record)
                    else:
                        records.append(record)
    if tables and not records:
        warnings.append("Se extrajeron celdas originales. Estas tablas no tienen el esquema de matrículas del ejemplo; no se han inventado nombres de campos.")
    return {"documento": document["name"], "paginas_seleccionadas": pages,
            "motor": "pdfplumber", "registros": records, "totales_publicados": totals,
            "tablas": tables, "avisos": list(dict.fromkeys(warnings))}


@app.post("/api/documents/{document_id}/extract")
def extract(document_id: str, request: ExtractionRequest):
    document = get_document(document_id)
    pages = sorted(set(request.paginas))
    if any(number < 1 or number > document["pages"] for number in pages):
        raise HTTPException(400, "Hay páginas fuera de rango.")
    try:
        result = extract_document(document, pages)
        with LOCK:
            if document_id in DOCUMENTS:
                DOCUMENTS[document_id]["result"] = result
        return result
    except Exception as exc:
        raise HTTPException(422, "No se pudieron extraer las tablas de estas páginas.") from exc


@app.get("/api/documents/{document_id}/result.json")
def download_result(document_id: str):
    document = get_document(document_id)
    if "result" not in document:
        raise HTTPException(404, "Extrae las tablas antes de descargar el JSON.")
    return JSONResponse(document["result"], headers={"Content-Disposition": 'attachment; filename="tablas.json"'})
