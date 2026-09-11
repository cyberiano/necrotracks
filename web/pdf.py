"""La set list en PDF, para bajarla al celular.

Por qué existe: la web instalada en el inicio del iPhone **no tiene la función de imprimir de Safari**, así
que el botón Imprimir no hace nada ahí. Desde la Mac se imprime y se guarda como PDF sin esto; desde el
celular, la única forma de tener el archivo es que lo arme la Pi.

fpdf2 es Python puro (no compila nada en la Pi, ~340 KB) y no necesita internet. Usa las fuentes del propio
PDF (Helvetica), sin archivos que instalar: eso obliga a latin-1, así que el texto se pasa por `_t()` (la
raya larga y las comillas tipográficas no existen ahí).

Dos hojas, las mismas que en pantalla: "piso" (número y nombre grandes, para leerla parado) y "tecnica"
(duración, bloque, qué hace al terminar y total).
"""
from fpdf import FPDF

MARGIN = 14  # mm
ROJO = (176, 16, 28)
GRIS = (85, 85, 94)


def _t(text):
    """Lo que se puede escribir con las fuentes del PDF (latin-1)."""
    return str(text or "").encode("latin-1", "replace").decode("latin-1")


def mmss(seconds):
    s = max(0, round(seconds or 0))
    return f"{s // 60}:{s % 60:02d}"


def _behavior(item, behaviors):
    if item.get("behavior") == "wait":
        return f"Esperar {int(item.get('wait') or 0)} s y seguir"
    return (behaviors or {}).get(item.get("behavior"), item.get("behavior") or "")


def _head(pdf, sl, count, total):
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(16, 16, 19)
    pdf.cell(0, 11, _t(sl["name"].upper()), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*GRIS)
    pdf.cell(0, 6, _t(f"{count} canciones - {mmss(total)}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(16, 16, 19)
    pdf.ln(1)
    pdf.set_line_width(0.8)
    pdf.line(MARGIN, pdf.get_y(), pdf.w - MARGIN, pdf.get_y())
    pdf.ln(4)


def _floor(pdf, items, name, size):
    """Número y nombre lo más grande que entre: la hoja se lee parado, desde arriba.
    La observación va abajo, en letra chica: es para leerla de reojo, no de lejos."""
    block = None
    for i, it in enumerate(items):
        if it.get("block") != block:
            block = it.get("block")
            if block:
                pdf.ln(2)
                pdf.set_font("Helvetica", "B", max(8, round(size * 0.42)))
                pdf.set_text_color(*ROJO)
                pdf.cell(0, size * 0.48, _t(block.upper()), new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(16, 16, 19)
        note = (it.get("note") or "").strip()
        borde = "" if note else "B"  # la línea va abajo de todo: si hay observación, después de ella
        alto = size * 0.72
        pdf.set_text_color(*GRIS)
        pdf.set_font("Helvetica", "", round(size * 0.62))
        pdf.cell(size * 0.85, alto, f"{i + 1:02d}", border=borde)
        pdf.set_text_color(16, 16, 19)
        pdf.set_font("Helvetica", "B", size)
        pdf.cell(0, alto, _t(name(it).upper()), border=borde, new_x="LMARGIN", new_y="NEXT")
        if note:
            pdf.set_font("Helvetica", "I", max(8, round(size * 0.34)))
            pdf.set_text_color(*GRIS)
            pdf.cell(size * 0.85, size * 0.42, "", new_x="RIGHT", new_y="TOP")
            pdf.cell(0, size * 0.42, _t(note), border="B", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(16, 16, 19)


def _table(pdf, items, name, dur, behaviors):
    anchos = (10, 74, 28, 16, 0)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*GRIS)
    for ancho, titulo in zip(anchos, ("#", "CANCION", "BLOQUE", "DURA", "AL TERMINAR")):
        pdf.cell(ancho, 7, titulo, border="B", new_x="LMARGIN" if ancho == 0 else "RIGHT",
                 new_y="NEXT" if ancho == 0 else "TOP")
    pdf.set_text_color(16, 16, 19)
    for i, it in enumerate(items):
        note = (it.get("note") or "").strip()
        borde = "" if note else "B"  # con observación, la línea de abajo va después de ella
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*GRIS)
        pdf.cell(anchos[0], 8, f"{i + 1:02d}", border=borde)
        pdf.set_text_color(16, 16, 19)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(anchos[1], 8, _t(name(it)), border=borde)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(anchos[2], 8, _t(it.get("block") or "-"), border=borde)
        pdf.cell(anchos[3], 8, mmss(dur(it)), border=borde)
        pdf.set_text_color(*GRIS)
        pdf.cell(0, 8, _t(_behavior(it, behaviors)), border=borde, new_x="LMARGIN", new_y="NEXT")
        if note:
            pdf.set_font("Helvetica", "I", 9)
            pdf.cell(anchos[0], 6, "", new_x="RIGHT", new_y="TOP")
            pdf.cell(0, 6, _t(note), border="B", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(16, 16, 19)


def setlist_pdf(sl, mode="piso", behaviors=None):
    """La set list (tal como la devuelve /api/setlists/SLUG) en PDF, como bytes."""
    items = sl["items"]
    songs = sl.get("songs") or {}
    name = lambda it: (songs.get(it["song"]) or {}).get("name") or it["song"]  # noqa: E731
    dur = lambda it: (songs.get(it["song"]) or {}).get("duration") or 0  # noqa: E731
    total = sum(dur(it) for it in items)

    pdf = FPDF(format="A4")
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(True, MARGIN)
    pdf.add_page()
    _head(pdf, sl, len(items), total)
    if mode == "piso":
        # Que entre en una hoja: con muchas canciones, más chico (igual que en pantalla)
        size = 26 if len(items) <= 8 else 22 if len(items) <= 12 else 18 if len(items) <= 16 else 14
        _floor(pdf, items, name, size)
    else:
        _table(pdf, items, name, dur, behaviors)
    pdf.set_y(-MARGIN - 6)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GRIS)
    pdf.cell(0, 6, _t(f"Necrotracks - {sl['name']} - {len(items)} canciones - {mmss(total)}"))
    return bytes(pdf.output())
