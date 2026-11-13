# Setting up sign-in for Google and Microsoft

Gmail/Google Workspace and Microsoft 365/Outlook.com only allow IMAP for modern accounts
via **OAuth**. For this, FED must be **registered as an app** with the provider once. Each
agency creates this registration itself (about 15 minutes per provider) and enters the ID
under **Settings → Google / Microsoft 365**.

What FED does:

- Sign-in takes place in the computer's **browser** or via **device code** on another
  device. FED never sees the account password; 2FA works as usual.
- Tokens stay **in memory only**. Provider, signed-in account, scopes and time are logged –
  never the token itself.
- If the signed-in account differs from the mailbox being acquired, FED warns (intended for
  shared mailboxes or delegation).
- FED **only reads**, even though the providers only offer a full-access scope for IMAP.
  Writing IMAP commands are technically blocked in the program.

---

## Microsoft 365 / Outlook.com

**No password, no app password.** Microsoft has switched off password sign-in (basic auth)
for IMAP – in all Microsoft 365 tenants, and for personal accounts (Outlook.com, Hotmail,
Live, MSN) on 16 Sept 2024. App passwords count as basic auth and are rejected as well, even
if some guides on the web say otherwise. FED therefore only offers **"Microsoft 365 (OAuth)"**
for Microsoft.

### No setup: FED's app registration

FED ships with its own app registration (**"FED – Forensic Email Duplicator"**, client ID
`8de90383-c0ec-4221-9ed6-bd9300cd455c`). Leave *Microsoft client ID* under **Settings → Google / Microsoft 365** **empty**
to use it. It works for personal accounts (Outlook.com, Hotmail, Live) and for accounts of
any organisation. FED only receives `IMAP.AccessAsUser.All` (read the mailbox via IMAP),
`offline_access`, `openid`, `email` and `profile`; there is no client secret.

**Organisation accounts:** many organisations only let users consent to apps they have
approved. If "Admin approval required" appears (AADSTS65001 / AADSTS90094), a tenant
administrator can approve FED once for everyone:
`https://login.microsoftonline.com/<tenant ID or domain>/adminconsent?client_id=8de90383-c0ec-4221-9ed6-bd9300cd455c`
Alternatively the organisation registers its own app (next section).

### Optional: your own app registration

For agencies that only allow apps from their own tenant:

1. Open the [Microsoft Entra admin center](https://entra.microsoft.com/) → *Entra ID* →
   *App registrations* → *New registration*.
   - **Name:** e.g. "FED – Sample Forensics Ltd" (shown in the sign-in window)
   - **Supported account types** – determines the *Microsoft tenant* entry:
     - own organization only → your tenant ID (directory ID)
     - accounts in any organization → `organizations`
     - plus personal Microsoft accounts (Outlook.com, Hotmail) → `common` (recommended)
     - personal Microsoft accounts only → `consumers`
   - **Redirect URI:** platform *Public client/native (mobile & desktop)*,
     URI `http://localhost`
2. *Authentication* → *Advanced settings* → **Allow public client flows = Yes**
   (required for the device code). Do not create a client secret.
3. *API permissions* → *Add a permission* → *Microsoft Graph* → *Delegated permissions*:
   `IMAP.AccessAsUser.All`, `offline_access`, `openid`, `email`, `profile`.
   If an organization restricts user consent, an admin must *Grant admin consent*.
4. *Overview*: copy the **Application (client) ID**.
5. In FED, under **Settings → Google / Microsoft 365**, enter:
   *Microsoft client ID* = application ID, *Microsoft tenant* = value from step 1. Save.

If the tenant is `organizations` and the mailbox is a personal address (outlook.\*,
hotmail.\*, live.\*, msn.com …), FED automatically signs in at the `consumers` endpoint.
This only works if the app registration allows personal accounts.

### Allow IMAP for the mailbox

- **Outlook.com/Hotmail:** [outlook.live.com](https://outlook.live.com) → Settings → Mail →
  *Forwarding and IMAP* → "Let devices and apps use IMAP".
- **Microsoft 365:** administrator in the admin center → Users → *Manage email apps* → IMAP.

### Acquire

Server `outlook.office365.com`, port 993, SSL – for Microsoft 365 and personal accounts.
After the address is entered, auto-detection selects **"Microsoft 365 (OAuth)"** itself.
On "Test connection" the account owner signs in in the browser. With **"Sign in with device
code"** FED shows a code that the account owner enters on their smartphone at
microsoft.com/devicelogin.

**Shared mailbox:** enter the shared mailbox as the account and an authorized user in the
"sign in as" field.

### Common error messages

| Message contains | Cause | Fix |
|---|---|---|
| AADSTS50020, AADSTS700016, "not enabled for consumers" | Account type not allowed in the app registration | Add personal accounts to the account types, tenant `common` |
| AADSTS65001, AADSTS90094 | Consent missing (organisation only allows approved apps) | Admin approves FED (link above) or *Grant admin consent* for your own app |
| AADSTS7000218 | Device code not allowed | *Allow public client flows = Yes* |
| AADSTS53003 | Conditional Access policy | Ask the tenant administrator |
| AUTHENTICATE failed after successful sign-in | IMAP disabled for the mailbox | Allow IMAP (see above) |
| “User is authenticated but not connected” | Sign-in OK, Microsoft refuses the mailbox: IMAP off or only just switched on, other account in the browser, sign-in not confirmed, too many connections | Allow IMAP and wait a while; account.live.com/activity → “This was me”; close other mail programs, retry later |

---

## Google (Gmail, Google Workspace)

1. **Create a project:** [Google Cloud Console](https://console.cloud.google.com/) →
   *New project*, e.g. "FED Sample Forensics".
2. **Google Auth Platform → Branding:** app name (e.g. "FED – Sample Forensics Ltd"),
   support email, contact. The name appears in the consent window.
3. **Audience:**
   - *Internal* – only accounts of your own Google Workspace organization, no review by
     Google needed.
   - *External* – any Google account. In **Testing** status only registered test users
     (max. 100) can consent, and consent **expires after 7 days**. For third-party accounts,
     add the account as a test user or publish the app (Google then reviews the Gmail scope).
4. **Data Access:** add the scope `https://mail.google.com/`, plus `openid` and `email`.
5. **Clients → Create client:** application type **Desktop app**. Copy client ID and
   client secret.
6. In FED, under **Settings → Google / Microsoft 365**, enter *Google client ID* and
   *Google client secret* (intended for desktop apps, not considered secret).

Server `imap.gmail.com`, port 993, SSL. IMAP must be enabled in the Gmail account.

Gmail shows labels as folders: a message with several labels is in several folders
(including "All Mail"). FED stores `X-GM-MSGID` so duplicates are recognized in the report.

### Without OAuth: Google app password

If no Google client ID is entered, FED automatically suggests **"Google (app password)"** for
Gmail.

1. Turn on **2-Step Verification** in the Google account (prerequisite).
2. Create an app password at
   [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), name e.g. "FED".
3. Enter the 16 letters in FED – FED removes the spaces shown on the page itself. The normal
   Google password is rejected for IMAP.
4. After the acquisition, **delete** the app password on the same page.

In Google Workspace the administrator must allow app passwords; accounts with "Advanced
Protection" have no app passwords.

---

## After the acquisition

The account owner or admin can revoke access at any time:

- Google: [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
- Microsoft: [myapplications.microsoft.com](https://myapplications.microsoft.com) or remove
  the enterprise application in the Entra admin center
