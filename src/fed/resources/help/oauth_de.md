# Anmeldung bei Google und Microsoft einrichten

Gmail/Google Workspace und Microsoft 365/Outlook.com lassen IMAP mit modernen Konten nur
per **OAuth** zu. Dafür muss FED beim Anbieter einmalig als **App registriert** sein. Jede
Agentur legt diese Registrierung selbst an (ca. 15 Minuten je Anbieter) und trägt die
Kennung unter **Einstellungen → Google / Microsoft 365** ein.

Was FED dabei macht:

- Die Anmeldung läuft im **Browser** des Rechners oder per **Gerätecode** auf einem anderen
  Gerät. FED sieht das Passwort des Kontos nie, 2FA funktioniert wie gewohnt.
- Tokens bleiben **nur im Arbeitsspeicher**. Protokolliert werden Anbieter, angemeldetes
  Konto, Scopes und Zeitpunkt – nie das Token selbst.
- Weicht das angemeldete Konto vom gesicherten Postfach ab, warnt FED (bei geteilten
  Postfächern bzw. Stellvertretung gewollt).
- FED **liest nur**, obwohl die Anbieter für IMAP nur einen Vollzugriff-Scope anbieten.
  Schreibende IMAP-Befehle sind im Programm technisch gesperrt.

---

## Microsoft 365 / Outlook.com

**Kein Passwort, kein App-Passwort.** Microsoft hat die Anmeldung mit Passwort (Basic Auth)
für IMAP abgeschaltet – in Microsoft 365 in allen Tenants, bei privaten Konten (Outlook.com,
Hotmail, Live, MSN) am 16.09.2024. App-Passwörter zählen dazu und werden ebenfalls
abgelehnt, auch wenn manche Anleitungen im Netz etwas anderes sagen. Für Microsoft gibt es
in FED daher nur **„Microsoft 365 (OAuth)“**.

### Ohne Einrichtung: App-Registrierung von FED

FED bringt eine eigene App-Registrierung mit (**„FED – Forensic Email Duplicator“**,
Client-ID `8de90383-c0ec-4221-9ed6-bd9300cd455c`). Lassen Sie unter **Einstellungen → Google / Microsoft 365** das Feld
*Microsoft Client-ID* **leer** – dann wird sie genutzt. Sie gilt für private Konten
(Outlook.com, Hotmail, Live) und für Konten jeder Organisation. FED erhält dabei nur die
Rechte `IMAP.AccessAsUser.All` (Postfach per IMAP lesen), `offline_access`, `openid`,
`email` und `profile`; ein Client-Secret gibt es nicht.

**Konten einer Organisation:** Viele Organisationen lassen Benutzer nur Apps zustimmen, die
sie freigegeben haben. Erscheint „Administratorgenehmigung erforderlich“ (AADSTS65001 /
AADSTS90094), kann ein Administrator des Tenants FED einmalig für alle freigeben:
`https://login.microsoftonline.com/<Tenant-ID oder Domain>/adminconsent?client_id=8de90383-c0ec-4221-9ed6-bd9300cd455c`
Alternativ registriert die Organisation eine eigene App (nächster Abschnitt).

### Optional: eigene App-Registrierung

Für Behörden, die nur Apps aus dem eigenen Tenant zulassen:

1. [Microsoft Entra Admin Center](https://entra.microsoft.com/) öffnen → *Entra ID* →
   *App-Registrierungen* → *Neue Registrierung*.
   - **Name:** z. B. „FED – Muster Forensik GmbH“ (erscheint im Anmeldefenster)
   - **Unterstützte Kontotypen** – bestimmt den Eintrag *Microsoft Tenant*:
     - nur die eigene Organisation → Ihre Tenant-ID (Verzeichnis-ID)
     - Konten in beliebigen Organisationen → `organizations`
     - zusätzlich private Microsoft-Konten (Outlook.com, Hotmail) → `common` (empfohlen)
     - nur private Microsoft-Konten → `consumers`
   - **Umleitungs-URI:** Plattform *Öffentlicher Client/nativ (mobil und Desktop)*,
     URI `http://localhost`
2. *Authentifizierung* → *Erweiterte Einstellungen* → **Öffentliche Clientflows zulassen = Ja**
   (nötig für den Gerätecode). Kein Client-Secret anlegen.
3. *API-Berechtigungen* → *Berechtigung hinzufügen* → *Microsoft Graph* → *Delegierte
   Berechtigungen*: `IMAP.AccessAsUser.All`, `offline_access`, `openid`, `email`, `profile`.
   Schränkt eine Organisation die Benutzerzustimmung ein, muss ein Admin
   *Administratorzustimmung erteilen*.
4. *Übersicht*: **Anwendungs-ID (Client)** kopieren.
5. In FED unter **Einstellungen → Google / Microsoft 365** eintragen:
   *Microsoft Client-ID* = Anwendungs-ID, *Microsoft Tenant* = Wert aus Schritt 1.
   Speichern.

Steht als Tenant `organizations` und ist das Postfach eine private Adresse (outlook.\*,
hotmail.\*, live.\*, msn.com …), meldet FED automatisch am Endpunkt `consumers` an. Das
klappt nur, wenn die App-Registrierung private Konten zulässt.

### IMAP im Postfach erlauben

- **Outlook.com/Hotmail:** [outlook.live.com](https://outlook.live.com) → Einstellungen →
  E-Mail → *Weiterleitung und IMAP* → „Geräten und Apps die Verwendung von IMAP erlauben“.
- **Microsoft 365:** Administrator im Admin Center → Benutzer → *E-Mail-Apps verwalten* → IMAP.

### Sichern

Server `outlook.office365.com`, Port 993, SSL – für Microsoft 365 und private Konten. Die
Autoerkennung wählt nach Eingabe der Adresse selbst **„Microsoft 365 (OAuth)“**. Bei
„Verbindung testen“ meldet sich der Kontoinhaber im Browser an. Mit **„Anmeldung per
Gerätecode“** zeigt FED einen Code, den der Kontoinhaber auf seinem Smartphone unter
microsoft.com/devicelogin eingibt.

**Geteiltes Postfach:** als Konto das geteilte Postfach angeben und im Feld „anmelden als“
einen berechtigten Benutzer eintragen.

### Typische Fehlermeldungen

| Meldung enthält | Ursache | Abhilfe |
|---|---|---|
| AADSTS50020, AADSTS700016, „not enabled for consumers“ | Kontotyp in der App-Registrierung nicht erlaubt | Kontotypen um private Konten erweitern, Tenant `common` |
| AADSTS65001, AADSTS90094 | Zustimmung fehlt (Organisation lässt nur freigegebene Apps zu) | Admin gibt FED frei (Link oben) oder *Administratorzustimmung erteilen* bei eigener App |
| AADSTS7000218 | Gerätecode nicht erlaubt | *Öffentliche Clientflows zulassen = Ja* |
| AADSTS53003 | Richtlinie für bedingten Zugriff | Administrator des Tenants fragen |
| AUTHENTICATE failed nach erfolgreicher Anmeldung | IMAP im Postfach aus | IMAP erlauben (siehe oben) |
| „User is authenticated but not connected“ | Anmeldung ok, Microsoft lässt das Postfach nicht zu: IMAP aus oder gerade erst eingeschaltet, anderes Konto im Browser, Anmeldung nicht bestätigt, zu viele Verbindungen | IMAP erlauben und etwas warten; account.live.com/activity → „Das war ich“; andere Mail-Programme schließen, später erneut |

---

## Google (Gmail, Google Workspace)

1. **Projekt anlegen:** [Google Cloud Console](https://console.cloud.google.com/) →
   *Neues Projekt*, z. B. „FED Muster Forensik“.
2. **Google Auth Platform → Branding:** App-Name (z. B. „FED – Muster Forensik GmbH“),
   Support-E-Mail, Kontakt. Der Name erscheint im Zustimmungsfenster.
3. **Zielgruppe (Audience):**
   - *Intern* – nur Konten der eigenen Google-Workspace-Organisation, keine Prüfung durch
     Google nötig.
   - *Extern* – beliebige Google-Konten. Im Status **Testing** dürfen nur eingetragene
     Testnutzer (max. 100) zustimmen, und die Zustimmung **läuft nach 7 Tagen ab**. Für
     fremde Konten das betroffene Konto als Testnutzer eintragen oder die App
     veröffentlichen (dann prüft Google den Gmail-Scope).
4. **Datenzugriff (Data Access):** Scope `https://mail.google.com/` hinzufügen, dazu
   `openid` und `email`.
5. **Clients → Client erstellen:** Anwendungstyp **Desktop-App**. Client-ID und
   Client-Secret kopieren.
6. In FED unter **Einstellungen → Google / Microsoft 365** *Google Client-ID* und
   *Google Client-Secret* eintragen (bei Desktop-Apps vorgesehen, gilt nicht als geheim).

Server `imap.gmail.com`, Port 993, SSL. Im Gmail-Konto muss IMAP aktiviert sein.

Gmail zeigt Labels als Ordner: Eine Mail mit mehreren Labels liegt in mehreren Ordnern
(u. a. „Alle Nachrichten“). FED speichert `X-GM-MSGID`, damit Duplikate im Bericht erkannt
werden.

### Ohne OAuth: Google-App-Passwort

Ist keine Google-Client-ID eingetragen, schlägt FED für Gmail automatisch **„Google
(App-Passwort)“** vor.

1. Im Google-Konto die **2-Schritt-Bestätigung** einschalten (Voraussetzung).
2. Unter [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   ein App-Passwort erzeugen, Name z. B. „FED“.
3. Die 16 Buchstaben in FED eintragen – die Leerzeichen der Anzeige entfernt FED selbst.
   Das normale Google-Passwort wird für IMAP abgelehnt.
4. Nach der Sicherung das App-Passwort auf derselben Seite wieder **löschen**.

In Google Workspace muss der Administrator App-Passwörter zulassen; bei Konten mit
„Erweitertem Schutz“ gibt es keine App-Passwörter.

---

## Nach der Sicherung

Der Kontoinhaber bzw. Admin kann den Zugriff jederzeit widerrufen:

- Google: [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
- Microsoft: [myapplications.microsoft.com](https://myapplications.microsoft.com) bzw. im
  Entra Admin Center die Unternehmensanwendung entfernen
