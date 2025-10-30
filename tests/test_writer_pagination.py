from pypdf import PdfReader

from src.services.pdf_writer import PDFWriter, ReportLabBackend


def test_writer_produces_multiple_pages_for_long_content(tmp_path):
    writer = PDFWriter(ReportLabBackend())
    title = "Medical Summary"
    long_paras = " ".join(["para"] * 1000)
    bullets = "\n".join([f"- item {i}" for i in range(100)])
    sections = [
        ("Intro Overview", "Short intro."),
        ("Key Points", bullets),
        ("Detailed Findings", long_paras),
        (
            "Care Plan & Follow-Up",
            "Follow-up in two weeks.\nRepeat labs as needed.",
        ),
    ]
    pdf = writer.build(title, sections)
    output_path = tmp_path / "out.pdf"
    output_path.write_bytes(pdf)
    assert len(PdfReader(str(output_path)).pages) > 1
