"""Oberflächentests mit Playwright gegen einen echten Server.

Voraussetzung:  pip install playwright  und  python -m playwright install chromium
Aufruf:         python -m unittest discover -s tests -v
Screenshots:    landen in tests/bilder/ (nicht im Repository) – nach Änderungen an der Oberfläche ansehen.

Chart.js und Google Fonts werden abgefangen: Chart.js durch einen Platzhalter ersetzt, Schriften leer beantwortet.
Das echte Zeichnen der Diagramme ist dadurch nicht abgedeckt.
"""
import datetime as dt
import json
import re
import unittest
from pathlib import Path

from testserver import Testserver, xml

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

BILDER = Path(__file__).resolve().parent / "bilder"

CHART_STUB = """
window.Chart = class {
  constructor(ctx, cfg){ this.ctx = ctx; this.config = cfg; this.data = cfg.data; this.options = cfg.options || {}; Chart.instances.push(this); }
  update(){} destroy(){} resize(){}
  static register(){}
};
Chart.instances = []; Chart.defaults = {font:{}, plugins:{legend:{}, tooltip:{}}, scale:{grid:{}}, color:''};
"""

HANDY = {"width": 390, "height": 844}
PC = {"width": 1366, "height": 900}


def tag(n=0):
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


@unittest.skipIf(sync_playwright is None, "Playwright ist nicht installiert")
class TrackerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BILDER.mkdir(exist_ok=True)
        cls.srv = Testserver().__enter__()
        cls.token = cls.srv.einrichten("anna", "geheim123")
        werte = {tag(n): {"weight": round(92 - n * 0.1, 1), "waist": 100, "kcalIn": 2100, "kcalActive": 500,
                          "steps": 8000, "sleep": 7.2} for n in range(0, 60, 2)}
        s, _, b = cls.srv.anfrage("PUT", "/api/daten", xml(werte), cls.token, {"If-Match": '"neu"'})
        assert s == 200, b
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.__exit__(None, None, None)

    # ---- Hilfen
    def seite(self, groesse=HANDY, farben="light", token=None, sw=False):
        ctx = self.browser.new_context(viewport=groesse, color_scheme=farben, service_workers="allow" if sw else "block")
        ctx.route("**/cdnjs.cloudflare.com/**", lambda r: r.fulfill(body=CHART_STUB, content_type="text/javascript"))
        ctx.route("**/fonts.googleapis.com/**", lambda r: r.fulfill(body="", content_type="text/css"))
        ctx.route("**/fonts.gstatic.com/**", lambda r: r.fulfill(body=""))
        cfg = {"mode": "server", "url": self.srv.url, "token": token or self.token, "lastUser": "anna"}
        ctx.add_init_script(f"localStorage.setItem('koerperdaten-config', {json.dumps(json.dumps(cfg))})")
        p = ctx.new_page()
        p.fehler = []
        p.on("pageerror", lambda e: p.fehler.append(str(e)))
        p.on("console", lambda m: m.type == "error" and p.fehler.append(m.text))
        p.goto(self.srv.url + "/")
        self.warte_status(p, "ok")
        return ctx, p

    def reiter(self, p, name):
        """Reiter öffnen wie per Klick (die Funktionen des Trackers sind von außen nicht erreichbar)."""
        p.evaluate("t => document.querySelector(`nav.tabs button[data-tab='${t}']`).click()", name)

    def warte_status(self, p, status, zeit=15000):
        p.wait_for_function("s => window.__sync && window.__sync.state === s", arg=status, timeout=zeit)

    def warte_gespeichert(self, p):
        p.wait_for_function("() => window.__sync.state === 'ok' && window.__sync.diff().empty", timeout=15000)

    def server_xml(self):
        return self.srv.get("/api/daten", self.token)[2].decode()

    def eintragen(self, p, datum, gewicht):
        self.reiter(p, "entry")
        p.fill("#entryDate", datum)
        p.dispatch_event("#entryDate", "change")
        p.fill("#f-weight", str(gewicht).replace(".", ","))
        p.click("#entryForm button[type=submit]")

    def ohne_fehler(self, p):
        self.assertEqual([f for f in p.fehler if "favicon" not in f], [], "Fehler in der Browser-Konsole")

    # ---- Tests
    def test_01_uebersicht_modern(self):
        for name, groesse, farben in (("handy-hell", HANDY, "light"), ("handy-dunkel", HANDY, "dark"),
                                      ("pc-hell", PC, "light")):
            with self.subTest(name):
                ctx, p = self.seite(groesse, farben)
                self.assertEqual(p.evaluate("document.documentElement.dataset.design"), "modern")
                self.assertTrue(p.is_visible("#p-dash"))
                p.screenshot(path=str(BILDER / f"uebersicht-{name}.png"), full_page=True)
                self.ohne_fehler(p)
                ctx.close()

    def test_02_alle_reiter(self):
        ctx, p = self.seite(PC)
        for reiter in ("charts", "entry", "data", "fotos", "settings", "dash"):
            self.reiter(p, reiter)
            self.assertTrue(p.is_visible(f"#p-{reiter}"), reiter)
            p.screenshot(path=str(BILDER / f"reiter-{reiter}.png"))
        self.ohne_fehler(p)
        ctx.close()

    def test_03_eintrag_wird_gespeichert(self):
        ctx, p = self.seite()
        self.eintragen(p, tag(1), 87.3)
        self.warte_gespeichert(p)
        self.assertIn(f'datum="{tag(1)}" weight="87.3"', self.server_xml())
        self.ohne_fehler(p)
        ctx.close()

    def test_04_offline_eintragen_und_nachreichen(self):
        """Fall A: offline eintragen, Verbindung zurück → landet auf dem Server."""
        ctx, p = self.seite()
        ctx.set_offline(True)
        self.eintragen(p, tag(3), 86.1)
        self.warte_status(p, "offline")
        self.assertFalse(p.evaluate("window.__sync.diff().empty"))
        ctx.set_offline(False)
        p.evaluate("dispatchEvent(new Event('online'))")
        self.warte_gespeichert(p)
        self.assertIn(f'datum="{tag(3)}" weight="86.1"', self.server_xml())
        ctx.close()

    def test_05_offline_start_mit_kopie(self):
        """Offline-Start: die Kopie der letzten 31 Tage wird angezeigt, Änderungen werden später übertragen."""
        ctx, p = self.seite(sw=True)
        p.wait_for_function("navigator.serviceWorker.controller || navigator.serviceWorker.ready.then(()=>true)")
        p.reload()                                     # jetzt vom Service Worker gesteuert
        self.warte_status(p, "ok")
        p.wait_for_function("() => !!navigator.serviceWorker.controller")
        p.wait_for_timeout(1000)                       # Offline-Kopie geschrieben
        ctx.set_offline(True)
        p.reload()
        self.warte_status(p, "offline")
        self.assertTrue(p.evaluate("window.__sync.partial"))
        self.eintragen(p, tag(5), 85.5)
        p.wait_for_timeout(300)
        ctx.set_offline(False)
        p.evaluate("dispatchEvent(new Event('online'))")
        self.warte_gespeichert(p)
        x = self.server_xml()
        self.assertIn(f'datum="{tag(5)}" weight="85.5"', x)
        self.assertIn(f'datum="{tag(58)}"', x, "ältere Tage dürfen beim Abgleich nicht verloren gehen")
        ctx.close()

    def test_06_konflikt_tageweise(self):
        """Fall B/D: ein anderes Gerät speichert dazwischen → beide Änderungen bleiben erhalten."""
        ctx, p = self.seite()
        x = re.sub(f'(<eintrag datum="{tag(10)}"[^>]*?)waist="100"', r'\1waist="95"', self.server_xml())
        etag = self.srv.get("/api/daten", self.token)[1]["ETag"]
        s, _, _ = self.srv.anfrage("PUT", "/api/daten", x, self.token, {"If-Match": etag})
        self.assertEqual(s, 200)
        self.eintragen(p, tag(7), 84.4)
        self.warte_gespeichert(p)
        x = self.server_xml()
        self.assertIn(f'datum="{tag(7)}" weight="84.4"', x)
        self.assertRegex(x, f'datum="{tag(10)}"[^>]*waist="95"')
        ctx.close()

    def test_07_wochentag_zeigt_tag(self):
        """2.7.1: Tipp auf einen Wochentag zeigt die Übersicht dieses Tages, nicht das Formular."""
        ctx, p = self.seite()
        tage = p.locator("#p-dash .m-week button.m-day:not([disabled]):not(.today)")
        if tage.count() == 0:                          # Montag: in die Vorwoche blättern
            p.click("#p-dash button[aria-label='Vorherige Woche']")
        tage.first.click()
        self.assertTrue(p.is_visible("#p-dash"))
        self.assertFalse(p.is_visible("#p-entry"))
        self.assertTrue(p.get_by_role("button", name="Zu heute").is_visible())
        self.assertEqual(p.locator("#p-dash .m-day.sel").count(), 1)
        ctx.close()

    def test_08_klassisch(self):
        ctx, p = self.seite(PC)
        def design(name):
            self.reiter(p, "settings")
            p.locator("button.look-opt", has_text=name).first.click()
            self.warte_gespeichert(p)
        design("Klassisch")
        self.assertEqual(p.evaluate("document.documentElement.dataset.design"), "classic")
        self.assertIn('"design":"classic"', self.server_xml())       # gilt für alle Geräte
        self.reiter(p, "dash")
        p.screenshot(path=str(BILDER / "uebersicht-klassisch.png"), full_page=True)
        self.ohne_fehler(p)
        design("Modern")
        ctx.close()


if __name__ == "__main__":
    unittest.main()
