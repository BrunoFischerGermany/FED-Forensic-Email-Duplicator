# OAuth einrichten (Google, Microsoft 365)

Jede Agentur registriert FED einmalig selbst und trägt die Kennungen in die
`config.toml` ein. Eine zentrale Registrierung des Projekts ist im Code
vorgesehen, aber derzeit nicht befüllt.

## Google (Gmail, Google Workspace)

1. Google Cloud Console → neues Projekt
2. OAuth-Zustimmungsbildschirm einrichten (Name der Agentur)
3. Anmeldedaten → OAuth-Client-ID → Typ **Desktop-App**
4. Client-ID und Client-Secret in `[oauth.google]` eintragen
5. Scope: `https://mail.google.com/` (nötig für IMAP)

> TODO: Schritt-für-Schritt mit Screenshots, Hinweise zu Test-/Produktionsmodus.

## Microsoft 365 / Outlook.com

1. Microsoft Entra Admin Center → App-Registrierungen → Neue Registrierung
2. Kontotypen: je nach Bedarf (nur eigene Organisation oder alle)
3. Plattform **Mobile und Desktop**, Redirect `http://localhost`
4. „Öffentliche Clientflows zulassen“ aktivieren
5. API-Berechtigung: Office 365 Exchange Online → delegiert → `IMAP.AccessAsUser.All`
6. Anwendungs-ID in `[oauth.microsoft] client_id`, Tenant in `tenant`

> TODO: Admin-Zustimmung, IMAP für das Postfach aktivieren, geteilte Postfächer.
