"""Server-Tests gegen einen echten Server.  Aufruf:  python -m unittest discover -s tests -v"""
import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path

from testserver import Testserver, xml

# kleinstes gültiges JPEG (1×1 Pixel)
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP////////////////////////////////////////////////////////////"
    "//////////////////////////2wBDAf//////////////////////////////////////////////////////////////"
    "////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACP/EABQQAQAAAAAAAAAA"
    "AAAAAAAAAAD/xAAUAQEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8AKp//2Q==")
B64 = base64.b64encode(JPEG).decode()


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = Testserver().__enter__()
        cls.token = cls.srv.einrichten("anna", "geheim123")

    @classmethod
    def tearDownClass(cls):
        cls.srv.__exit__(None, None, None)

    def test_01_status(self):
        s, _, b = self.srv.get("/api/status")
        self.assertEqual(s, 200)
        self.assertEqual(b["app"], "koerperdaten")
        self.assertFalse(b["einrichtung_noetig"])
        self.assertTrue(b["fotos"])

    def test_02_seite_und_service_worker(self):
        s, h, b = self.srv.get("/")
        self.assertEqual(s, 200)
        self.assertIn(b"TRACKER_VERSION", b)
        s, _, sw = self.srv.get("/sw.js")
        self.assertEqual(s, 200)
        self.assertIn(b"koerperdaten-", sw)

    def test_03_einrichtung_nur_einmal(self):
        s, _, _ = self.srv.anfrage("POST", "/api/einrichtung", {"name": "mallory", "passwort": "geheim123"})
        self.assertEqual(s, 403)

    def test_04_anmeldung(self):
        s, _, _ = self.srv.anfrage("POST", "/api/anmelden", {"name": "anna", "passwort": "falsch000"})
        self.assertEqual(s, 401)
        self.assertTrue(self.srv.anmelden("ANNA", "geheim123"))      # Name ohne Groß/Klein
        s, _, _ = self.srv.get("/api/daten")
        self.assertEqual(s, 401)

    def test_05_daten_und_konflikt(self):
        t = self.token
        s, _, _ = self.srv.get("/api/daten", t)
        self.assertEqual(s, 404)
        s, h, b = self.srv.anfrage("PUT", "/api/daten", xml({"2026-11-01": {"weight": 90}}), t, {"If-Match": '"neu"'})
        self.assertEqual(s, 200, b)
        etag1 = h["ETag"]
        s, h, b = self.srv.get("/api/daten", t)
        self.assertEqual((s, h["ETag"]), (200, etag1))
        self.assertIn(b'weight="90"', b)
        s, h, _ = self.srv.anfrage("PUT", "/api/daten", xml({"2026-11-01": {"weight": 89}}), t, {"If-Match": etag1})
        self.assertEqual(s, 200)
        # zweites Gerät mit altem Stand → 412, nichts überschrieben
        s, _, b = self.srv.anfrage("PUT", "/api/daten", xml({"2026-11-01": {"weight": 1}}), t, {"If-Match": etag1})
        self.assertEqual(s, 412)
        self.assertIn(b'weight="89"', self.srv.get("/api/daten", t)[2])

    def test_06_ungueltiges_xml(self):
        s, _, _ = self.srv.anfrage("PUT", "/api/daten", "<falsch>", self.token)
        self.assertEqual(s, 400)

    def test_07_sicherungen(self):
        s, _, b = self.srv.get("/api/sicherungen", self.token)
        self.assertEqual(s, 200)
        self.assertGreaterEqual(len(b), 1)

    def test_08_benutzer_getrennt(self):
        t = self.token
        s, _, _ = self.srv.anfrage("POST", "/api/benutzer", {"name": "ben", "passwort": "geheim456"}, t)
        self.assertEqual(s, 201)
        tb = self.srv.anmelden("ben", "geheim456")
        self.assertEqual(self.srv.get("/api/daten", tb)[0], 404)          # sieht Annas Daten nicht
        self.assertEqual(self.srv.get("/api/benutzer", tb)[0], 403)       # kein Administrator
        s, _, _ = self.srv.anfrage("DELETE", "/api/benutzer/ben", token=t)
        self.assertEqual(s, 200)
        self.assertEqual(self.srv.get("/api/ich", tb)[0], 401)            # Sitzung weg

    def test_09_fotos(self):
        t = self.token
        s, _, b = self.srv.anfrage("PUT", "/api/fotos/2026-11-01/vorne",
                                   {"bild": B64, "vorschau": B64, "original": B64, "zuschnitt": {"x": 0}}, t)
        self.assertEqual(s, 200, b)
        s, _, liste = self.srv.get("/api/fotos", t)
        self.assertEqual(len(liste), 1)
        fid = liste[0]["id"]
        s, h, bild = self.srv.get(f"/api/fotos/{fid}/bild", t)
        self.assertEqual((s, bild[:3]), (200, b"\xff\xd8\xff"))
        s, _, _ = self.srv.anfrage("PUT", "/api/fotos/2026-11-01/vorne", {"bild": base64.b64encode(b"kein bild").decode(),
                                                                          "vorschau": B64}, t)
        self.assertEqual(s, 400)                                          # nur JPEG, PNG, WebP
        self.assertEqual(self.srv.anfrage("DELETE", "/api/fotos/2026-11-01/vorne", token=t)[0], 200)
        self.assertEqual(self.srv.get("/api/fotos", t)[2], [])

    def test_10_befehle(self):
        r = self.srv.cli("pruefen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.srv.cli("benutzer-liste")
        self.assertIn("anna", r.stdout)


class MigrationTest(unittest.TestCase):
    def test_sicherung_vor_update(self):
        """Ändert sich die Programmversion, sichert der Server die Datenbank nach vor-update/."""
        with tempfile.TemporaryDirectory(prefix="kd-test-") as tmp:
            daten = Path(tmp)
            with Testserver(daten) as srv:
                srv.einrichten()
            con = sqlite3.connect(daten / "koerperdaten.db")
            con.execute("UPDATE meta SET wert='0.0' WHERE schluessel='programmversion'")
            con.commit()
            con.close()
            with Testserver(daten) as neu:
                self.assertEqual(neu.get("/api/status")[0], 200)
            kopien = list((daten / "vor-update").glob("koerperdaten-vor-*.db"))
            self.assertTrue(kopien, "keine Sicherung vor dem Update")


if __name__ == "__main__":
    unittest.main()
