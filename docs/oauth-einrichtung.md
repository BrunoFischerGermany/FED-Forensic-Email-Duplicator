# OAuth einrichten (Google, Microsoft 365)

Gmail/Google Workspace und Microsoft 365/Outlook.com erlauben IMAP mit modernen
Konten nur per OAuth. Dafür muss FED beim Anbieter als **App registriert** sein.
Jede Agentur legt diese Registrierung einmalig selbst an und trägt die Kennung
in die `config.toml` ein (Dauer: ca. 15 Minuten je Anbieter). Eine zentrale
Registrierung des Projekts ist im Code vorgesehen, aber derzeit nicht befüllt.

Was FED dabei macht:

- Anmeldung im **System-Browser** des Rechners (Authorization Code + PKCE).
  FED sieht das Passwort des Kontos nie; 2FA funktioniert wie gewohnt.
- Rückleitung an `http://127.0.0.1:<zufälliger Port>/` bzw. `http://localhost:<Port>/`
  auf demselben Rechner.
- Tokens bleiben **nur im Arbeitsspeicher**. Protokolliert werden Anbieter,
  angemeldetes Konto, Scopes und Zeitpunkt – nie das Token selbst.
- Weicht das angemeldete Konto vom gesicherten Postfach ab, warnt FED
  (bei geteilten Postfächern bzw. Stellvertretung gewollt).
- FED **liest nur**, obwohl die Anbieter für IMAP nur einen Vollzugriff-Scope
  anbieten. Schreibende IMAP-Befehle sind im Programm technisch gesperrt.

---

## Google (Gmail, Google Workspace)

1. **Projekt anlegen:** [Google Cloud Console](https://console.cloud.google.com/) →
   Projekt auswählen → *Neues Projekt*, z. B. „FED Muster Forensik“.
2. **Google Auth Platform → Branding:** App-Name (z. B. „FED – Muster Forensik GmbH“),
   Support-E-Mail, Kontakt. Dieser Name erscheint später im Zustimmungsfenster.
3. **Zielgruppe (Audience):**
   - *Intern* – nur Konten Ihrer eigenen Google-Workspace-Organisation. Keine Prüfung
     durch Google nötig. Ggf. muss der Workspace-Admin den Zugriff interner Apps auf
     Gmail erlauben.
   - *Extern* – beliebige Google-Konten. Im Status **Testing** dürfen nur eingetragene
     Testnutzer (max. 100) zustimmen, und die Zustimmung **läuft nach 7 Tagen ab**.
     Für den Einsatz bei fremden Konten das betroffene Konto als Testnutzer eintragen
     oder die App veröffentlichen (dann ist für den Gmail-Scope eine Prüfung durch
     Google erforderlich).
4. **Datenzugriff (Data Access):** Scope `https://mail.google.com/` hinzufügen
   (nötig für IMAP), dazu `openid` und `email`.
5. **Clients → Client erstellen:** Anwendungstyp **Desktop-App**, Name z. B. „FED“.
   Client-ID und Client-Secret kopieren.
6. In `config.toml` eintragen:

   ```toml
   [oauth.google]
   client_id     = "1234567890-abc….apps.googleusercontent.com"
   client_secret = "GOCSPX-…"   # bei Desktop-Apps vorgesehen, gilt nicht als geheim
   ```

Server: `imap.gmail.com:993`, SSL. Im Gmail-Konto muss IMAP aktiviert sein.

> Hinweis: Gmail zeigt Labels als Ordner. Eine Mail mit mehreren Labels liegt in
> mehreren Ordnern (u. a. „Alle Nachrichten“). FED speichert `X-GM-MSGID`, damit
> Duplikate im Bericht erkannt werden.

---

### Ohne OAuth: Google-App-Passwort

Ist keine Google-Client-ID eingerichtet, schlägt FED für Gmail-Konten automatisch
**„Google (App-Passwort)“** vor (CLI: `fed acquire --auth google-app`).

1. Im Google-Konto die **2-Schritt-Bestätigung** einschalten (Voraussetzung).
2. Unter <https://myaccount.google.com/apppasswords> ein App-Passwort erzeugen, Name z. B. „FED“.
3. Die 16 Buchstaben in FED eintragen – die Leerzeichen der Anzeige (`abcd efgh ijkl mnop`)
   entfernt FED selbst. Das normale Google-Passwort wird von Google für IMAP abgelehnt.
4. Nach der Sicherung das App-Passwort in derselben Google-Seite wieder **löschen**.

Hinweise: In Google Workspace muss der Administrator App-Passwörter zulassen; bei Konten mit
„Erweitertem Schutz“ gibt es keine App-Passwörter. Im Bericht steht als Anmeldung
„Google-App-Passwort“. Ein App-Passwort gewährt vollen Zugriff auf das Postfach – es wird von
FED wie jedes Passwort nur im Arbeitsspeicher gehalten.

## Microsoft 365 / Outlook.com

> **Kein Passwort, kein App-Passwort.** Microsoft hat die Anmeldung mit Passwort
> (Basic Auth) für IMAP abgeschaltet – in Exchange Online / Microsoft 365 in allen
> Tenants, bei privaten Konten (Outlook.com, Hotmail, Live, MSN) am 16.09.2024.
> App-Passwörter zählen dazu und werden ebenfalls abgelehnt, auch wenn manche
> Anleitungen im Netz etwas anderes sagen. Für Microsoft-Postfächer gibt es in FED
> daher nur **„Microsoft 365 (OAuth)“**. Wählt man bei einem Microsoft-Server
> „Passwort“, warnt FED schon vor dem Test.

**IMAP muss im Postfach erlaubt sein:**

- Outlook.com/Hotmail: <https://outlook.live.com> → Einstellungen → E-Mail →
  *Weiterleitung und IMAP* → „Geräten und Apps die Verwendung von IMAP erlauben“.
- Microsoft 365: Administrator im Admin Center → Benutzer → *E-Mail-Apps verwalten* → IMAP.

1. **App registrieren:** [Microsoft Entra Admin Center](https://entra.microsoft.com/) →
   *Entra ID* → *App-Registrierungen* → *Neue Registrierung*.
   - Name: z. B. „FED – Muster Forensik GmbH“
   - Unterstützte Kontotypen:
     - nur eigene Organisation → `tenant` = Ihre Tenant-ID
     - Konten in beliebigen Organisationen → `tenant = "organizations"`
     - zusätzlich private Microsoft-Konten (Outlook.com, Hotmail) → `tenant = "common"`
     - nur private Microsoft-Konten → `tenant = "consumers"`

     Steht `tenant = "organizations"` (Standard) und ist das Postfach eine private
     Adresse (outlook.*, hotmail.*, live.*, msn.com …), meldet FED automatisch am
     Endpunkt `consumers` an. Das klappt nur, wenn die App-Registrierung private
     Konten zulässt – sonst meldet Microsoft `AADSTS…` und FED zeigt, was zu ändern ist.
   - Umleitungs-URI: Plattform **Öffentlicher Client/nativ (mobil und Desktop)**,
     URI `http://localhost`
2. **Authentifizierung → Erweiterte Einstellungen:** *Öffentliche Clientflows zulassen*
   = **Ja** (nötig für den Gerätecode-Ablauf). Kein Client-Secret anlegen.
3. **API-Berechtigungen → Berechtigung hinzufügen → Microsoft Graph → Delegierte
   Berechtigungen:** `IMAP.AccessAsUser.All`, `offline_access`, `openid`, `email`, `profile`.
   In Organisationen, die Benutzerzustimmung einschränken, muss ein Admin
   *Administratorzustimmung erteilen*.
4. **Übersicht:** *Anwendungs-ID (Client)* und ggf. *Verzeichnis-ID (Mandant)* kopieren.
5. In `config.toml` eintragen:

   ```toml
   [oauth.microsoft]
   client_id = "00000000-0000-0000-0000-000000000000"
   tenant    = "organizations"   # oder Tenant-ID / "common"
   ```

Server: `outlook.office365.com:993`, SSL – für Microsoft 365 und private Konten.

**Geteiltes Postfach:** mit einem berechtigten Benutzer anmelden und das geteilte
Postfach als Konto angeben:

```bash
fed acquire --case-dir … --account team@firma.de --login-as max@firma.de --evidence A-02
```

**Gerätecode** (Anmeldung auf einem anderen Gerät, z. B. Smartphone des Kontoinhabers):

```bash
fed acquire --case-dir … --account max@firma.de --evidence A-01 --device-code
```

---

## Nach der Sicherung

Der Kontoinhaber bzw. Admin kann den Zugriff jederzeit widerrufen:

- Google: [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
- Microsoft: [myapplications.microsoft.com](https://myapplications.microsoft.com) bzw.
  im Entra Admin Center die Unternehmensanwendung entfernen
