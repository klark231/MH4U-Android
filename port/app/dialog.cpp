#include "dialog.h"

#include <cstdio>
#include <SDL.h>

#include "common/logging/log.h"
#include "common/string_util.h"

#ifdef _WIN32
#include <windows.h>
#include <commdlg.h>
#else
#include <cstdlib>
#endif

namespace App::Dialog {

namespace {

#ifndef _WIN32
/// The first line a command prints, or empty if it failed.
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
    const int status = pclose(pipe);
    if (status != 0) {
        return "";
    }
    while (!out.empty() && (out.back() == '\n' || out.back() == '\r')) {
        out.pop_back();
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
#endif

} // namespace

std::string OpenFile(const std::string& title, const std::vector<std::string>& extensions) {
#if defined(_WIN32)
    std::wstring filter;
    std::wstring patterns;
    for (const auto& ext : extensions) {
        patterns += (patterns.empty() ? L"" : L";") + std::wstring(L"*.") + Common::UTF8ToUTF16W(ext);
    }
    filter += L"Monster Hunter 4 Ultimate";
    filter.push_back(L'\0');
    filter += patterns;
    filter.push_back(L'\0');
    filter.push_back(L'\0');
    wchar_t file[MAX_PATH * 4] = {};
    const std::wstring wtitle = Common::UTF8ToUTF16W(title);
    OPENFILENAMEW ofn{};
    ofn.lStructSize = sizeof(ofn);
    ofn.lpstrFilter = filter.c_str();
    ofn.lpstrFile = file;
    ofn.nMaxFile = static_cast<DWORD>(std::size(file));
    ofn.lpstrTitle = wtitle.c_str();
    ofn.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
    if (!GetOpenFileNameW(&ofn)) {
        return "";
    }
    return Common::UTF16ToUTF8(file);
#elif defined(__APPLE__)
    std::string types;
    for (const auto& ext : extensions) {
        types += (types.empty() ? "" : ", ") + std::string("\"") + ext + "\"";
    }
    const std::string script = "POSIX path of (choose file with prompt \"" + title +
                               "\" of type {" + types + "})";
    return Run("osascript -e " + Quote(script) + " 2>/dev/null");
#else
    std::string zenity_filter = "--file-filter=" + Quote("Monster Hunter 4 Ultimate |");
    std::string patterns;
    for (const auto& ext : extensions) {
        patterns += " *." + ext;
    }
    zenity_filter = "--file-filter=" + Quote("Monster Hunter 4 Ultimate |" + patterns);
    if (!Run("command -v zenity").empty()) {
        return Run("zenity --file-selection --title=" + Quote(title) + " " + zenity_filter +
                   " 2>/dev/null");
    }
    if (!Run("command -v kdialog").empty()) {
        return Run("kdialog --getopenfilename . " + Quote(patterns.substr(1)) + " --title " +
                   Quote(title) + " 2>/dev/null");
    }
    return "";
#endif
}

void Message(const std::string& title, const std::string& text, bool error) {
    if (error) {
        LOG_ERROR(Frontend, "{}", text);
    } else {
        LOG_INFO(Frontend, "{}", text);
    }
    SDL_ShowSimpleMessageBox(error ? SDL_MESSAGEBOX_ERROR : SDL_MESSAGEBOX_INFORMATION,
                             title.c_str(), text.c_str(), nullptr);
}

} // namespace App::Dialog
