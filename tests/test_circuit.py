import os
import tempfile
import unittest

from core.circuit import (
    CircuitDocument,
    circuitikz_source,
    export_circuit_pdf,
    export_circuit_tex,
)


class CircuitDocumentTests(unittest.TestCase):
    def test_labels_are_incremental_and_rotation_changes_terminals(self):
        document = CircuitDocument()
        first = document.add_component("resistor", 4, 3)
        second = document.add_component("resistor", 8, 3)

        self.assertEqual((first.label, second.label), ("R1", "R2"))
        self.assertEqual(first.terminals(), [(3.0, 3.0), (5.0, 3.0)])
        first.rotation = 90
        terminals = first.terminals()
        self.assertAlmostEqual(terminals[0][0], 4.0)
        self.assertAlmostEqual(terminals[0][1], 2.0)
        self.assertAlmostEqual(terminals[1][1], 4.0)

    def test_json_roundtrip_preserves_document(self):
        document = CircuitDocument(title="Filtro RC")
        resistor = document.add_component("resistor", 4, 3)
        resistor.value = "4.7 kΩ"
        document.add_component("capacitor", 7, 4)
        document.add_wire((1, 3), (3, 3))

        restored = CircuitDocument.from_dict(document.to_dict())

        self.assertEqual(restored.to_dict(), document.to_dict())

    def test_invalid_component_is_rejected(self):
        document = CircuitDocument()
        with self.assertRaises(ValueError):
            document.add_component("unknown", 1, 1)

    def test_circuitikz_is_standalone_and_escapes_labels(self):
        document = CircuitDocument(title="Filtro #1")
        component = document.add_component("resistor", 3, 2)
        component.label = "R_out"
        source = circuitikz_source(document)

        self.assertIn(r"\usepackage[american]{circuitikz}", source)
        self.assertIn(r"to[R,l={R\_out / 1 k$\Omega$}]", source)
        self.assertTrue(source.endswith("\\end{document}\n"))

    def test_export_tex_pdf_and_project_file(self):
        document = CircuitDocument(title="Fuente y carga")
        document.add_component("voltage", 3, 4)
        document.add_component("resistor", 7, 4)
        document.add_wire((4, 4), (6, 4))

        with tempfile.TemporaryDirectory() as directory:
            project_path = os.path.join(directory, "sample.labcircuit.json")
            tex_path = os.path.join(directory, "sample.tex")
            pdf_path = os.path.join(directory, "sample.pdf")

            document.save(project_path)
            export_circuit_tex(document, tex_path)
            export_circuit_pdf(document, pdf_path)

            self.assertEqual(CircuitDocument.load(project_path).title,
                             document.title)
            self.assertGreater(os.path.getsize(tex_path), 100)
            self.assertGreater(os.path.getsize(pdf_path), 500)
            with open(pdf_path, "rb") as stream:
                self.assertEqual(stream.read(4), b"%PDF")


if __name__ == "__main__":
    unittest.main()
