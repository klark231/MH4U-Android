// (Windows Vista's file dialogs: IFileOpenDialog picks folders)
#if defined(_WIN32) && (!defined(_WIN32_WINNT) || _WIN32_WINNT < 0x0600)
#undef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif

#include "launcher/pick.h"

#include <cstdio>
#include <iterator>
#include <SDL.h>
#include <SDL_syswm.h>

#ifdef _WIN32
#include <windows.h>
#include <commdlg.h>
#include <shobjidl.h>
#include "common/string_util.h"
#endif

namespace Launcher::Pick {

namespace {

SDL_Window* owner = nullptr;

#ifndef _WIN32
/// What a command prints (its first line), or nothing if it failed or was cancelled.
std::string Run(const std::string& command) {
    FILE* pipe = popen(command.c_str(), "r");
    if (!pipe) {
        return "";
    }
    std::string out;
    char buffer[4096];
    while (std::fgets(buffer, sizeof(buffer), pipe)) {
        out += buffer;
    }
    if (pclose(pipe) != 0) {
        return "";
    }
    if (const auto end = out.find('\n'); end != std::string::npos) {
        out.resize(end);
    }
    return out;
}

/// Single quotes for a shell.
std::string Quote(const std::string& s) {
    std::string q = "'";
    for (const char c : s) {
        q += c == '\'' ? std::string("'\\''") : std::string(1, c);
    }
    return q + "'";
}

#ifdef __APPLE__
/// Double quotes for AppleScript.
std::string AppleString(const std::string& s) {
    std::string q = "\"";
    for (const char c : s) {
        if (c == '"' || c == '\\') {
            q += '\\';
        }
        q += c;
    }
    return q + "\"";
}
#else
bool Have(const char* program) {
    return !Run(std::string("command -v ") + program).empty();
}
#endif
#else
HWND OwnerWindow() {
    SDL_SysWMinfo info;
    SDL_VERSION(&info.version);
    if (owner && SDL_GetWindowWMInfo(owner, &info) && info.subsystem == SDL_SYSWM_WINDOWS) {
        return info.info.win.window;
    }
    return nullptr;
}
#endif

} // namespace

void SetOwner(SDL_Window* window) {
    owner = window;
}

Result File(const std::string& title, const std::string& kind,
            const std::vector<std::string>& extensions) {
    Result result;
#if defined(_WIN32)
    std::wstring patterns;
    for (const auto& ext : extensions) {
        patterns += (patterns.empty() ? L"" : L";") + std::wstring(L"*.") + Common::UTF8ToUTF16W(ext);
    }
    std::wstring filter = Common::UTF8ToUTF16W(kind);
    filter.push_back(L'\0');
    filter += patterns;
    filter.push_back(L'\0');
    filter += L"All files";
    filter.push_back(L'\0');
    filter += L"*.*";
    filter.push_back(L'\0');
    filter.push_back(L'\0');
    wchar_t file[MAX_PATH * 4] = {};
    const std::wstring wtitle = Common::UTF8ToUTF16W(title);
    OPENFILENAMEW ofn{};
    ofn.lStructSize = sizeof(ofn);
    ofn.hwndOwner = OwnerWindow();
    ofn.lpstrFilter = filter.c_str();
    ofn.lpstrFile = file;
    ofn.nMaxFile = static_cast<DWORD>(std::size(file));
    ofn.lpstrTitle = wtitle.c_str();
    ofn.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
    if (GetOpenFileNameW(&ofn)) {
        result.path = Common::UTF16ToUTF8(file);
    }
#elif defined(__APPLE__)
    std::string types;
    for (const auto& ext : extensions) {
        types += (types.empty() ? "" : ", ") + AppleString(ext);
    }
    const std::string script = "POSIX path of (choose file with prompt " + AppleString(title) +
                               (types.empty() ? "" : " of type {" + types + "}") + ")";
    result.path = Run("osascript -e " + Quote(script) + " 2>/dev/null");
#else
    std::string patterns;
    for (const auto& ext : extensions) {
        patterns += (patterns.empty() ? "" : " ") + std::string("*.") + ext;
    }
    if (Have("zenity")) {
        result.path = Run("zenity --file-selection --title=" + Quote(title) + " --file-filter=" +
                          Quote(kind + " | " + patterns) + " --file-filter=" +
                          Quote("All files | *") + " 2>/dev/null");
    } else if (Have("kdialog")) {
        result.path = Run("kdialog --title " + Quote(title) + " --getopenfilename . " +
                          Quote(patterns + "|" + kind) + " 2>/dev/null");
    } else {
        result.available = false;
    }
#endif
    return result;
}

Result Folder(const std::string& title) {
    Result result;
#if defined(_WIN32)
    const HRESULT com = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    IFileOpenDialog* dialog = nullptr;
    if (SUCCEEDED(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER,
                                   IID_PPV_ARGS(&dialog)))) {
        DWORD options = 0;
        dialog->GetOptions(&options);
        dialog->SetOptions(options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);
        dialog->SetTitle(Common::UTF8ToUTF16W(title).c_str());
        if (SUCCEEDED(dialog->Show(OwnerWindow()))) {
            IShellItem* item = nullptr;
            if (SUCCEEDED(dialog->GetResult(&item))) {
                PWSTR path = nullptr;
                if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path))) {
                    result.path = Common::UTF16ToUTF8(path);
                    CoTaskMemFree(path);
                }
                item->Release();
            }
        }
        dialog->Release();
    } else {
        result.available = false;
    }
    if (SUCCEEDED(com)) {
        CoUninitialize();
    }
#elif defined(__APPLE__)
    const std::string script = "POSIX path of (choose folder with prompt " + AppleString(title) + ")";
    result.path = Run("osascript -e " + Quote(script) + " 2>/dev/null");
#else
    if (Have("zenity")) {
        result.path = Run("zenity --file-selection --directory --title=" + Quote(title) + " 2>/dev/null");
    } else if (Have("kdialog")) {
        result.path = Run("kdialog --title " + Quote(title) + " --getexistingdirectory . 2>/dev/null");
    } else {
        result.available = false;
    }
#endif
    return result;
}

} // namespace Launcher::Pick
