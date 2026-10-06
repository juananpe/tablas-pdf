import io
import unittest
import pymupdf as fitz
from fastapi.testclient import TestClient
from app import app


def table(page, rows):
    widths = [150, 80, 60, 60, 65, 80, 80]
    xs = [30]
    for width in widths:
        xs.append(xs[-1] + width)
    top, height = 130, 26
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            rect = fitz.Rect(xs[j], top + i * height, xs[j+1], top + (i+1) * height)
            page.draw_rect(rect, color=(0, 0, 0), width=1)
            page.insert_text((xs[j]+4, rect.y0+17), value, fontsize=10)


def sample_pdf():
    pdf = fitz.open()
    page = pdf.new_page(width=700, height=800)
    page.insert_text((30, 60), 'Oferta educativa 2026-27', fontsize=12)
    page.insert_text((30, 90), 'Situacion del alumnado en el curso 2025-26', fontsize=11)
    table(page, [['Asignatura', 'Ez binarioa', 'E', 'G', 'Total', 'E', 'G'],
                 ['Biola', '', '23', '4', '27', '85,19%', '14,81%'],
                 ['Dultzaina', '', '2', '3', '5', '40,00%', '60,00%']])
    page = pdf.new_page(width=700, height=800)
    table(page, [['Perkusioa', '1', '14', '28', '43', '32,56%', '65,12%'],
                 ['TOTAL', '1', '39', '35', '75', '52,00%', '46,67%']])
    page.insert_text((30, 240), 'emakumezkoak / mujeres; gizonezkoak / hombres', fontsize=11)
    data = pdf.tobytes()
    pdf.close()
    return data


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def upload(self, data):
        response = self.client.post('/api/documents', files={'file': ('sample.pdf', io.BytesIO(data), 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def test_enrollment_and_continuation(self):
        doc = self.upload(sample_pdf())
        response = self.client.post(f'/api/documents/{doc}/extract', json={'paginas':[2, 1, 2]})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['paginas_seleccionadas'], [1, 2])
        self.assertEqual(len(data['registros']), 3)
        biola, dultzaina, percussion = data['registros']
        self.assertEqual((biola['hombres'], biola['mujeres'], biola['curso_datos']), (4, 23, '2025-26'))
        self.assertIsNone(biola['no_binario'])
        self.assertEqual(dultzaina['mujeres'], 2)
        self.assertEqual(percussion['no_binario'], 1)
        self.assertEqual(len(data['totales_publicados']), 1)
        self.assertFalse(data['avisos'])
        downloaded = self.client.get(f'/api/documents/{doc}/result.json')
        self.assertEqual(downloaded.json(), data)
        self.assertIn('attachment', downloaded.headers['content-disposition'])
        continuation = self.client.post(f'/api/documents/{doc}/extract', json={'paginas':[2]}).json()
        self.assertEqual([r['asignatura'] for r in continuation['registros']], ['Perkusioa'])
        self.assertEqual(continuation['registros'][0]['curso_datos'], '2025-26')
        thumbnail = self.client.get(f'/api/documents/{doc}/pages/1/thumbnail')
        self.assertEqual(thumbnail.headers['content-type'], 'image/png')
        self.assertTrue(thumbnail.content.startswith(b'\x89PNG'))

    def test_generic_table(self):
        pdf = fitz.open(); page = pdf.new_page(width=700, height=800)
        table(page, [['Producto','Cantidad'], ['Papel','5']])
        doc = self.upload(pdf.tobytes()); pdf.close()
        data = self.client.post(f'/api/documents/{doc}/extract', json={'paginas':[1]}).json()
        self.assertFalse(data['registros'])
        self.assertEqual(data['tablas'][0]['filas'], [['Producto','Cantidad'], ['Papel','5']])
        self.assertTrue(data['avisos'])

    def test_blank_scan(self):
        pdf = fitz.open(); page = pdf.new_page()
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0,0,50,50), False); pix.clear_with(255)
        page.insert_image(page.rect, pixmap=pix)
        doc = self.upload(pdf.tobytes()); pdf.close()
        data = self.client.post(f'/api/documents/{doc}/extract', json={'paginas':[1]}).json()
        self.assertFalse(data['tablas']); self.assertTrue(data['avisos'])

    def test_invalid_inputs(self):
        response = self.client.post('/api/documents', files={'file':('bad.pdf', b'not a pdf','application/pdf')})
        self.assertEqual(response.status_code, 400)
        doc = self.upload(sample_pdf())
        self.assertEqual(self.client.post(f'/api/documents/{doc}/extract', json={'paginas':[]}).status_code, 422)
        self.assertEqual(self.client.post(f'/api/documents/{doc}/extract', json={'paginas':[3]}).status_code, 400)
        self.assertEqual(self.client.get(f'/api/documents/{doc}/pages/0/thumbnail').status_code, 400)
        self.assertEqual(self.client.post('/api/documents/missing/extract', json={'paginas':[1]}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
