"""Tiny dependency-free single-page PDF writer for receipts.

Avoids pulling in reportlab/weasyprint. Renders a list of text lines in Helvetica.
Sufficient for a plain payment receipt; if richer layout is ever needed, swap this
for the project's PDF tooling without touching callers (they only see bytes).
"""


def _escape(text):
    return text.replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)')


def render_lines(lines, title='Receipt'):
    """Return PDF bytes for ``lines`` (a list of strings), one per row."""
    y = 770
    content_parts = ['BT', '/F1 16 Tf', f'1 0 0 1 56 {y} Tm', f'({_escape(title)}) Tj', 'ET']
    y -= 30
    for line in lines:
        content_parts += ['BT', '/F1 11 Tf', f'1 0 0 1 56 {y} Tm', f'({_escape(line)}) Tj', 'ET']
        y -= 18
    content = '\n'.join(content_parts).encode('latin-1', 'replace')

    objects = []
    objects.append(b'<< /Type /Catalog /Pages 2 0 R >>')
    objects.append(b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>')
    objects.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
                   b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>')
    objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    objects.append(b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n'
                   + content + b'\nendstream')

    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref_pos = len(out)
    out += f'xref\n0 {len(objects)+1}\n'.encode()
    out += b'0000000000 65535 f \n'
    for off in offsets:
        out += f'{off:010d} 00000 n \n'.encode()
    out += (f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\n'
            f'startxref\n{xref_pos}\n%%EOF').encode()
    return bytes(out)
