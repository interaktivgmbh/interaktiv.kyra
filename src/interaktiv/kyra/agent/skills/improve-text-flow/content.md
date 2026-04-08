# Skill: Textfluss verbessern

Du verbesserst jetzt die Lesbarkeit bestehender Textblöcke auf dieser Seite. Arbeite jeden Textblock der Reihe nach ab.

## Pflichtschritte pro Textblock

Lies den Block (`get_layout` mit `name`). Dann führe **jeden** der folgenden Schritte der Reihe nach aus. Überspringe einen Schritt nur, wenn er nachweislich nicht anwendbar ist.

### 1. Absätze prüfen

Jeder Absatz darf maximal einen Gedanken enthalten. Absätze über 3–4 Zeilen aufteilen. Nutze `<p>`-Tags.

### 2. Zwischenüberschriften einfügen

Wenn der Textblock mehr als zwei Themen oder Abschnitte abdeckt, füge heading-Blöcke (level 3 oder 4) vor den jeweiligen Abschnitten ein. Der Text muss dann auf mehrere rich_text-Blöcke aufgeteilt werden.

### 3. Aufzählungen erkennen und umsetzen

Wenn ein Absatz 3 oder mehr gleichwertige Punkte aufzählt (auch als Fließtext), wandle sie in eine `<ul>`- oder `<ol>`-Liste um. Geordnet, wenn die Reihenfolge relevant ist.

### 4. Zitate herauslösen

Wenn im Text ein wörtliches Zitat eingebettet ist, erstelle einen eigenen Zitat-Block (`create_quote`) mit Quellenangabe und entferne das Zitat aus dem Fließtext.

### 5. Schlüsselbegriffe hervorheben

Setze zentrale Kennzahlen, Eigennamen und Schlüsselbegriffe mit `<strong>` fett. Maximal 2–3 Hervorhebungen pro Absatz. Nutze HtmlPatch (`{old, new}`).

### 6. Tabellen prüfen

Wenn der Text Vergleichsdaten enthält (Zahlen, Termine, Gegenüberstellungen), schlage dem Nutzer eine Tabelle vor.

## Arbeitsweise

- **Inhalt nicht ändern.** Du formatierst um, du schreibst nicht um. Kein Wort hinzufügen, kein Fakt ändern.
- **HtmlPatch verwenden.** Nutze `{old, new}` für gezielte Änderungen statt den ganzen HTML-Block zu ersetzen.
- **Konvention beachten.** Wenn die Änderung vom Stil der Geschwisterseiten abweicht, den Nutzer darauf hinweisen.
- **Alle Blöcke in einem Durchgang.** Nicht nach jedem Block anhalten. Alle Textblöcke der Seite durcharbeiten, dann das Gesamtergebnis berichten.
- **Nicht auf Bestätigung warten.** Der Nutzer hat den Skill aufgerufen — das ist der Auftrag. Lies, analysiere, setze um, berichte. Erst am Ende fragen, ob Anpassungen nötig sind.
