#include "launcher/tools.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fmt/format.h>
#include <json.hpp>

#include "common/file_util.h"
#include "launcher/process.h"

namespace Launcher::Tools {

namespace {

bool HasTools(const std::string& dir) {
    return !dir.empty() && FileUtil::Exists(dir + "/build_game.py") &&
           FileUtil::Exists(dir + "/mods.py");
}

std::string ParentOf(const std::string& path) {
    const auto slash = path.find_last_of("/\\");
    return slash == std::string::npos ? std::string(".") : path.substr(0, slash);
}

std::string Env(const char* name) {
    const char* value = std::getenv(name);
    return value ? std::string(value) : std::string();
}

/// Python's version line and status for one candidate command.
bool TryPython(const std::vector<std::string>& command, Python& out) {
    std::vector<std::string> args = command;
    args.insert(args.end(),
                {"-c", "import sys; print('%d.%d.%d' % sys.version_info[:3]); "
                       "sys.exit(0 if sys.version_info >= (3, 10) else 3)"});
    std::string output;
    const int code = RunToEnd(args, output);
    while (!output.empty() && (output.back() == '\n' || output.back() == '\r' || output.back() == ' ')) {
        output.pop_back();
    }
    const auto line = output.substr(output.find_last_of('\n') + 1);
    const bool looks_like_version =
        !line.empty() && line.size() < 16 && std::all_of(line.begin(), line.end(), [](char c) {
            return (c >= '0' && c <= '9') || c == '.';
        });
    if (code == 0 && looks_like_version) {
        out.command = command;
        out.version = line;
        return true;
    }
    if (code == 3 && looks_like_version && out.too_old.empty()) {
        out.too_old = line;
    }
    return false;
}

std::vector<std::vector<std::string>> PythonCandidates() {
    std::vector<std::vector<std::string>> candidates;
    if (const auto env = Env("MH4U_PYTHON"); !env.empty()) {
        candidates.push_back({env});
    }
#if defined(_WIN32)
    candidates.push_back({"py", "-3"});
    candidates.push_back({"python"});
    candidates.push_back({"python3"});
    // python.org's installer puts it here, also when "Add python.exe to PATH" wasn't ticked
    std::vector<std::string> installed;
    for (const auto& root : {Env("LOCALAPPDATA") + "\\Programs\\Python", Env("ProgramFiles")}) {
        if (root.size() < 3 || !FileUtil::IsDirectory(root)) {
            continue;
        }
        FileUtil::ForeachDirectoryEntry(
            nullptr, root, [&](u64*, const std::string& dir, const std::string& name) {
                const std::string exe = dir + "\\" + name + "\\python.exe";
                if (name.rfind("Python3", 0) == 0 && FileUtil::Exists(exe)) {
                    installed.push_back(exe);
                }
                return true;
            });
    }
    // Python313 before Python39
    std::sort(installed.begin(), installed.end(), [](const std::string& a, const std::string& b) {
        const auto version = [](const std::string& path) {
            const auto at = path.rfind("Python3");
            return std::atoi(path.c_str() + at + 7);
        };
        return version(a) > version(b);
    });
    for (const auto& path : installed) {
        candidates.push_back({path});
    }
#elif defined(__APPLE__)
    // An app started from the Finder has a short PATH, and /usr/bin/python3 asks to install
    // the command-line tools when they aren't there: the usual installs first, by path
    for (const char* path : {"/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                             "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"}) {
        if (FileUtil::Exists(path)) {
            candidates.push_back({path});
        }
    }
    const auto path_env = Env("PATH");
    std::size_t start = 0;
    while (start <= path_env.size()) {
        const auto end = std::min(path_env.find(':', start), path_env.size());
        const auto dir = path_env.substr(start, end - start);
        if (!dir.empty() && dir != "/usr/bin" && FileUtil::Exists(dir + "/python3")) {
            candidates.push_back({dir + "/python3"});
        }
        start = end + 1;
    }
    if (FileUtil::Exists("/Library/Developer/CommandLineTools/usr/bin/python3")) {
        candidates.push_back({"/usr/bin/python3"});
    }
#else
    candidates.push_back({"python3"});
    candidates.push_back({"python"});
#endif
    return candidates;
}

bool StartsWith(const std::string& s, const char* prefix) {
    return s.rfind(prefix, 0) == 0;
}

/// "  compiled 120/697 (35s)" -> 120, 697
bool Fraction(const std::string& line, const char* word, int& done, int& total) {
    const auto at = line.find(word);
    if (at == std::string::npos) {
        return false;
    }
    return std::sscanf(line.c_str() + at + std::strlen(word), " %d/%d", &done, &total) == 2 &&
           total > 0;
}

} // namespace

std::vector<std::string> ToolsCandidates() {
    std::vector<std::string> dirs;
    if (const auto env = Env("MH4U_TOOLS"); !env.empty()) {
        dirs.push_back(env);
    }
    const std::string exe_dir = ParentOf(SelfPath());
#if defined(__APPLE__)
    // MH4U.app/Contents/MacOS/mh4u: the package's mh4u-tools next to the app, or in it
    dirs.push_back(exe_dir + "/../../../mh4u-tools/tools");
    dirs.push_back(exe_dir + "/../Resources/mh4u-tools/tools");
    dirs.push_back(exe_dir + "/mh4u-tools/tools");
#else
    // an AppImage runs from a mount of its own: the package's folder is the AppImage's
    if (const auto appimage = Env("APPIMAGE"); !appimage.empty()) {
        dirs.push_back(ParentOf(appimage) + "/mh4u-tools/tools");
    }
    dirs.push_back(exe_dir + "/mh4u-tools/tools");
#endif
#ifdef MH4U_SOURCE_TOOLS_DIR
    dirs.push_back(MH4U_SOURCE_TOOLS_DIR);  // the repository this app was built from
#endif
    return dirs;
}

std::string FindToolsDir() {
    for (const auto& dir : ToolsCandidates()) {
        if (HasTools(dir)) {
            return dir;
        }
    }
    return "";
}

Python FindPython() {
    Python found;
    for (const auto& candidate : PythonCandidates()) {
        if (TryPython(candidate, found)) {
            break;
        }
    }
    return found;
}

std::string PythonHint(const Python& found) {
    std::string older;
    if (!found.too_old.empty()) {
        older = fmt::format("The Python found is {}, older than 3.10. ", found.too_old);
    }
#if defined(_WIN32)
    return older + "Install Python 3.10 or newer from python.org/downloads (tick \"Add python.exe "
                   "to PATH\"), then check again.";
#elif defined(__APPLE__)
    return older + "Install Python 3.10 or newer from python.org or Homebrew (brew install python), "
                   "and the Xcode command-line tools (xcode-select --install in Terminal), then "
                   "check again.";
#else
    return older + "Install Python 3.10 or newer with your distribution's package manager (for "
                   "example sudo apt install python3), then check again.";
#endif
}

std::string UserDir() {
    std::string dir = FileUtil::GetUserPath(FileUtil::UserPath::UserDir);
    while (dir.size() > 1 && (dir.back() == '/' || dir.back() == '\\')) {
        dir.pop_back();
    }
    return dir;
}

std::vector<std::string> BuildCommand(const Python& python, const std::string& tools,
                                      const std::string& dump) {
    std::vector<std::string> args = python.command;
    args.insert(args.end(), {"-u", tools + "/build_game.py", "--dump", dump, "--install",
                             UserDir(), "--fetch-compiler"});
    return args;
}

std::vector<std::string> ModsCommand(const Python& python, const std::string& tools,
                                     const std::vector<std::string>& extra) {
    std::vector<std::string> args = python.command;
    args.insert(args.end(), {"-u", tools + "/mods.py", "--json", "--user-dir", UserDir()});
    args.insert(args.end(), extra.begin(), extra.end());
    return args;
}

Progress BuildProgress(const std::vector<std::string>& lines) {
    Progress p{0.01f, "Starting"};
    for (const auto& line : lines) {
        int done = 0, total = 0;
        if (StartsWith(line, "extracting")) {
            p = {0.02f, "Reading the update"};
        } else if (StartsWith(line, "recompiling")) {
            p = {0.04f, "Recompiling the game's code"};
        } else if (Fraction(line, "analysed", done, total)) {
            p = {0.04f + 0.14f * done / total, fmt::format("Recompiling the game's code: {} of {} "
                                                            "functions", done, total)};
        } else if (line.find(" functions, ") != std::string::npos && line.find("hook points") != std::string::npos) {
            p = {0.19f, "Recompiled: " + line};
        } else if (StartsWith(line, "downloading")) {
            p = {0.2f, "Downloading the C compiler"};
        } else if (line.find(" MB") != std::string::npos && Fraction(line, "", done, total)) {
            p = {0.2f + 0.04f * done / total,
                 fmt::format("Downloading the C compiler: {} of {} MB", done, total)};
        } else if (StartsWith(line, "unpacking")) {
            p = {0.24f, "Unpacking the C compiler"};
        } else if (StartsWith(line, "compiling with")) {
            p = {0.25f, "Compiling the code library (this takes a while)"};
        } else if (Fraction(line, "compiled", done, total)) {
            p = {0.25f + 0.72f * done / total,
                 fmt::format("Compiling the code library: {} of {} files", done, total)};
        } else if (StartsWith(line, "linked")) {
            p = {0.98f, "Linking the code library"};
        } else if (StartsWith(line, "installed to")) {
            p = {1.0f, "Installed"};
        }
    }
    return p;
}

namespace {

std::string Size(long long n) {
    if (n >= (1LL << 30)) {
        return fmt::format("{:.2f} GB", static_cast<double>(n) / (1LL << 30));
    }
    if (n >= (1LL << 20)) {
        return fmt::format("{:.1f} MB", static_cast<double>(n) / (1LL << 20));
    }
    return fmt::format("{} KB", std::max(1LL, (n + 512) / 1024));
}

} // namespace

Progress ModsProgress(const std::vector<std::string>& lines) {
    // (only the last progress line counts: a long download prints thousands)
    Progress p{-1.0f, "Starting"};
    for (auto it = lines.rbegin(); it != lines.rend(); ++it) {
        if (it->empty() || (*it)[0] != '@') {
            continue;
        }
        const auto j = nlohmann::json::parse(it->substr(1), nullptr, false);
        if (j.is_discarded() || !j.is_object()) {
            continue;
        }
        const auto text = [&j](const char* key) {
            return j.contains(key) && j[key].is_string() ? j[key].get<std::string>() : std::string();
        };
        const auto number = [&j](const char* key) {
            return j.contains(key) && j[key].is_number() ? j[key].get<long long>() : -1LL;
        };
        const long long done = number("done"), total = number("total");
        if (done < 0) {  // a stage starts
            p.stage = text("message");
            p.fraction = total > 0 ? static_cast<float>(std::max(0LL, number("resumed"))) / static_cast<float>(total)
                                   : -1.0f;
        } else {
            const std::string what = text("what");
            if (text("event") == "install") {
                p.stage = fmt::format("{}: {} of {} files", what, done, total);
            } else {
                p.stage = total > 0 ? fmt::format("{}: {} of {}", what, Size(done), Size(total))
                                    : fmt::format("{}: {}", what, Size(done));
            }
            p.fraction = total > 0 ? std::min(1.0f, static_cast<float>(done) / static_cast<float>(total)) : -1.0f;
        }
        if (p.stage.empty()) {
            p.stage = "Working";
        }
        break;
    }
    return p;
}

std::string WithoutProgress(const std::string& output) {
    std::string out;
    std::size_t start = 0;
    while (start < output.size()) {
        const auto end = std::min(output.find('\n', start), output.size());
        if (output[start] != '@') {
            out.append(output, start, end - start + 1);
        }
        start = end + 1;
    }
    return out;
}

std::string ErrorSummary(const std::vector<std::string>& lines) {
    // the tools end with what went wrong (a SystemExit's message, an exception's last line)
    for (auto it = lines.rbegin(); it != lines.rend(); ++it) {
        const auto first = it->find_first_not_of(" \t");
        if (first != std::string::npos) {
            return it->substr(first);
        }
    }
    return "It ended without saying why.";
}

} // namespace Launcher::Tools
