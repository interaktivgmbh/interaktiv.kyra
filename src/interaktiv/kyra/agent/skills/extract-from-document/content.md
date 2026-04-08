# Skill: Inhalte aus Dokument extrahieren

Du extrahierst jetzt Inhalte aus einem Dokument im Portal und arbeitest sie in die aktuelle Seite ein. Arbeite die folgenden Schritte der Reihe nach ab.

## Schritt 1: Dokument und Ziel klären

1. Frage den Nutzer: Welches Dokument? Welche Abschnitte oder Themen daraus?
2. Finde das Dokument im Portal (`search_documents` oder `list_children`).
3. Lies die ersten 5 Seiten (`read_document_pages`). Dort liegt oft ein Inhaltsverzeichnis.
4. Nenne dem Nutzer die gefundene Struktur und frage, welche Abschnitte extrahiert werden sollen.

Erst weitermachen, wenn der Nutzer bestätigt hat, was extrahiert werden soll.

## Schritt 2: Zielseite und Konvention lesen

1. Lies die aktuelle Seite (`get_layout`).
2. Lies mindestens eine Geschwisterseite. Die Konvention bestimmt die Darstellungsform, nicht das Dokument.

## Schritt 3: Abschnitt für Abschnitt extrahieren

Für jeden zu extrahierenden Abschnitt:

1. Lies den relevanten Seitenbereich des Dokuments (`read_document_pages`, max. 5 Seiten).
2. Wähle die Darstellungsform nach Inhalt:
   - Fakten und Kennzahlen → Statistik-Block oder Tabelle.
   - Aufzählungen, Kriterien → Liste im rich_text-Block.
   - Zitate → Zitat-Block mit Quellenangabe.
   - Vergleiche → Tabelle oder Spalten.
   - Viele Unterkapitel → Akkordeon.
   - Zusammenfassung → Einleitungstext.
3. Baue den Inhalt in die Seite ein. Gib im Gespräch die Quelle an (Dokument, Seitenzahl).
4. Zeige dem Nutzer das Ergebnis und frage, ob es passt.

Erst den nächsten Abschnitt beginnen, wenn der aktuelle bestätigt ist.

## Schritt 4: Vollständigkeit prüfen

1. Lies die fertige Seite (`get_layout`).
2. Vergleiche mit der Geschwisterseite: Fehlen Abschnitte, die laut Konvention auf die Seite gehören?
3. Hinweise an den Nutzer: Was fehlt im Dokument? Was muss der Nutzer noch liefern?

## Prinzipien

- **Keine erfundenen Daten.** Alles muss im Dokument stehen oder vom Nutzer bestätigt sein.
- **Kein 1:1-Abtippen.** PDF-Text webgerecht aufbereiten: kürzer, scanbar, strukturiert.
- **Konvention vor Dokumentstruktur.** Die Seite richtet sich nach den Geschwisterseiten, nicht nach dem PDF.
