"""Microsoft Office automation (Windows only) for Word/Excel/PowerPoint -> PDF."""
import os
import threading

_lock = threading.Lock()  # Office automation isn't safe to drive concurrently

APPS = {
    ".doc": "Word.Application", ".docx": "Word.Application", ".rtf": "Word.Application", ".odt": "Word.Application",
    ".xls": "Excel.Application", ".xlsx": "Excel.Application", ".ods": "Excel.Application",
    ".ppt": "PowerPoint.Application", ".pptx": "PowerPoint.Application", ".odp": "PowerPoint.Application",
}


def office_available():
    try:
        import win32com.client  # noqa: F401
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application"))
        return True
    except Exception:
        return False


def export_pdf(src, out):
    import pythoncom
    import win32com.client

    src, out = os.path.abspath(src), os.path.abspath(out)
    prog_id = APPS[os.path.splitext(src)[1].lower()]
    with _lock:
        pythoncom.CoInitialize()
        app = None
        try:
            app = win32com.client.DispatchEx(prog_id)
            if prog_id == "Word.Application":
                app.Visible = False
                app.DisplayAlerts = 0
                doc = app.Documents.Open(src, ReadOnly=True, ConfirmConversions=False, AddToRecentFiles=False)
                doc.ExportAsFixedFormat(out, 17)  # wdExportFormatPDF
                doc.Close(False)
            elif prog_id == "Excel.Application":
                app.Visible = False
                app.DisplayAlerts = False
                wb = app.Workbooks.Open(src, ReadOnly=True)
                wb.ExportAsFixedFormat(0, out)  # xlTypePDF
                wb.Close(False)
            else:
                pres = app.Presentations.Open(src, ReadOnly=True, Untitled=False, WithWindow=False)
                pres.SaveAs(out, 32)  # ppSaveAsPDF
                pres.Close()
        finally:
            if app is not None:
                app.Quit()
            pythoncom.CoUninitialize()
    return out
