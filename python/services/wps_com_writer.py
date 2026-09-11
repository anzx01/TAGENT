"""Create and verify a WPS Writer document through the registered COM API."""
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

def create_document(output, text="您好"):
    target = Path(output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(str(target))
    import win32com.client
    app = win32com.client.DispatchEx("KWPS.Application")
    app.Visible = False
    doc = app.Documents.Add()
    try:
        doc.Content.Text = text
        try:
            doc.SaveAs2(str(target), 12)
        except AttributeError:
            doc.SaveAs(str(target), 12)
    finally:
        doc.Close(False)
        app.Quit()
    with zipfile.ZipFile(target) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    actual = "".join(root.itertext()).strip()
    if actual != text:
        raise RuntimeError(f"WPS document content mismatch: {actual!r}")
    return target
