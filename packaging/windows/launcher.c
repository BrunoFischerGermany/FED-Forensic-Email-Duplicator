/*
 * FED – Startprogramm für Windows (GPL-3.0-only)
 *
 * Liegt als FED.exe oben im Ordner FED\ und startet app\FED.exe mit denselben
 * Argumenten. So bleibt der Ordner übersichtlich (FED.exe, config.toml, Dokumente),
 * ohne dass sich das Programm beim Start in den Temp-Ordner entpacken muss – das
 * hatte bei Virenscannern Fehlalarme ausgelöst.
 *
 * FED erkennt am Unterordner app\, dass config.toml und FED.ini hier oben liegen.
 * Wartet auf das Programm und gibt dessen Rückgabewert weiter.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>

#define BUF 32768

static int fail(const wchar_t *text)
{
    MessageBoxW(NULL, text, L"FED", MB_OK | MB_ICONERROR);
    return 1;
}

/* Zeiger hinter den Programmnamen der Befehlszeile (Argumente unverändert weiterreichen). */
static const wchar_t *args_of(const wchar_t *cmd)
{
    if (*cmd == L'"') {
        cmd++;
        while (*cmd && *cmd != L'"')
            cmd++;
        if (*cmd == L'"')
            cmd++;
    } else {
        while (*cmd && *cmd != L' ' && *cmd != L'\t')
            cmd++;
    }
    while (*cmd == L' ' || *cmd == L'\t')
        cmd++;
    return cmd;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR cmdline, int show)
{
    static wchar_t home[BUF], target[BUF], line[BUF];
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD code = 1, n;
    wchar_t *slash;
    (void)inst; (void)prev; (void)cmdline; (void)show;

    n = GetModuleFileNameW(NULL, home, BUF);
    if (n == 0 || n >= BUF)
        return fail(L"FED: Programmpfad nicht ermittelbar / program path not available.");
    slash = wcsrchr(home, L'\\');
    if (!slash)
        return fail(L"FED: Programmpfad ungültig / invalid program path.");
    *slash = L'\0';

    if (_snwprintf_s(target, BUF, _TRUNCATE, L"%s\\app\\FED.exe", home) < 0)
        return fail(L"FED: Pfad zu lang / path too long.");
    if (GetFileAttributesW(target) == INVALID_FILE_ATTRIBUTES)
        return fail(L"FED: app\\FED.exe fehlt. Bitte das ZIP vollständig entpacken.\n\n"
                    L"app\\FED.exe is missing. Please extract the complete ZIP.");
    if (_snwprintf_s(line, BUF, _TRUNCATE, L"\"%s\" %s", target, args_of(GetCommandLineW())) < 0)
        return fail(L"FED: Befehlszeile zu lang / command line too long.");

    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    ZeroMemory(&pi, sizeof pi);
    if (!CreateProcessW(target, line, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
        return fail(L"FED: app\\FED.exe konnte nicht gestartet werden / could not be started.");
    CloseHandle(pi.hThread);
    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    return (int)code;
}
