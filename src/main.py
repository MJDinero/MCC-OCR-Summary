# main.py — FastAPI entrypoint stub
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse
import tempfile
from src.services.docai_helper import process_document
from src.services.summariser import summarise
from src.services.pdf_writer import write_summary_pdf

app = FastAPI()

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.post("/process")
async def process(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(await file.read())
        tmp.flush()
    file_path = tmp.name

    doc = process_document(file_path)
    text = " ".join(page.get("text", "") for page in doc.get("pages", []))
    summary = summarise(text)

    out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf").name
    write_summary_pdf(summary, out_path)

    return FileResponse(out_path, media_type="application/pdf", filename="summary.pdf")
