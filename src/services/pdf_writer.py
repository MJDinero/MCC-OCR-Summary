# pdf_writer.py — stub for writing summary to PDF
def write_summary_pdf(summary: str, output_path: str) -> None:
    # TODO: implement PDF writing (e.g. using reportlab)
    with open(output_path, "w") as f:
        f.write(summary)
