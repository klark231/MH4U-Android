// Mods: the mod library through tools/mods.py --json (docs/MODS.md): on/off, the order, which
// mod wins a conflict, adding a folder or an archive, removing; the mods are put in Azahar's
// mods folder when the player presses Play (deploy), and a code library that the code mods made
// stale can be built again from here. The catalog (tools/mods_catalog.json, or a newer one
// from the web): mods and texture packs picked for the port, downloaded and installed here.
#include <algorithm>
#include <SDL.h>
#include <fmt/format.h>
#include <imgui.h>
#include <json.hpp>

#include "common/file_util.h"
#include "common/logging/log.h"
#include "launcher/state.h"
#include "launcher/ui.h"

namespace Launcher {

namespace {

using Json = nlohmann::json;

constexpr const char* REBUILD_TEXT =
    "The code library wasn't built with the code mods now in Azahar's mods folder, and they change "
    "the game's code: the library has to be built again from them (a few minutes).";

std::string Str(const Json& j, const char* key) {
    const auto it = j.find(key);
    return it != j.end() && it->is_string() ? it->get<std::string>() : std::string();
}

int Int(const Json& j, const char* key) {
    const auto it = j.find(key);
    return it != j.end() && it->is_number_integer() ? it->get<int>() : 0;
}

bool Bool(const Json& j, const char* key) {
    const auto it = j.find(key);
    return it != j.end() && it->is_boolean() && it->get<bool>();
}

std::vector<std::string> Strings(const Json& j, const char* key) {
    std::vector<std::string> out;
    const auto it = j.find(key);
    if (it != j.end() && it->is_array()) {
        for (const auto& item : *it) {
            if (item.is_string()) {
                out.push_back(item.get<std::string>());
            }
        }
    }
    return out;
}

/// The one JSON object mods.py --json prints (null if it printed something else: a crash),
/// without the progress lines of a download or an install.
Json Parse(const std::string& raw) {
    const std::string output = Tools::WithoutProgress(raw);
    Json j = Json::parse(output, nullptr, false);
    if (!j.is_discarded() && j.is_object()) {
        return j;
    }
    // anything printed before it (a warning from Python itself)
    if (const auto start = output.find("\n{"); start != std::string::npos) {
        j = Json::parse(output.substr(start + 1), nullptr, false);
        if (!j.is_discarded() && j.is_object()) {
            return j;
        }
    }
    return Json();
}

std::string ErrorText(const Json& j, const std::string& raw) {
    if (j.is_object() && j.contains("error") && j["error"].is_object()) {
        return Str(j["error"], "message");
    }
    const std::string output = Tools::WithoutProgress(raw);
    std::vector<std::string> lines;
    std::size_t start = 0;
    while (start < output.size()) {
        const auto end = std::min(output.find('\n', start), output.size());
        lines.push_back(output.substr(start, end - start));
        start = end + 1;
    }
    return "mods.py didn't work: " + Tools::ErrorSummary(lines);
}

void ApplyOverview(ModLibrary& lib, const Json& j) {
    lib.loaded = true;
    lib.error.clear();
    lib.library_path = Str(j, "library");
    lib.folder = Str(j, "mods_folder");
    lib.deploy_state = Str(j, "deploy_state");
    lib.problems = Strings(j, "problems");
    lib.warnings = Strings(j, "warnings");
    lib.messages = Strings(j, "messages");
    lib.mods.clear();
    if (const auto it = j.find("mods"); it != j.end() && it->is_array()) {
        for (const auto& m : *it) {
            if (!m.is_object()) {
                continue;
            }
            ModInfo mod;
            mod.id = Str(m, "id");
            mod.name = Str(m, "name");
            mod.version = Str(m, "version");
            mod.author = Str(m, "author");
            mod.description = Str(m, "description");
            mod.url = Str(m, "url");
            mod.enabled = Bool(m, "enabled");
            mod.is_new = Bool(m, "new");
            mod.position = Int(m, "position");
            mod.files = Int(m, "files");
            mod.wins = Int(m, "wins");
            mod.loses = Int(m, "loses");
            mod.warnings = Strings(m, "warnings");
            mod.errors = Strings(m, "errors");
            if (const auto code = m.find("code"); code != m.end() && code->is_object()) {
                mod.code = true;
                const auto changes = code->find("changes_code");
                mod.changes_code = changes == code->end() || !changes->is_boolean() ? -1 : changes->get<bool>() ? 1 : 0;
                mod.adds_code = Bool(*code, "adds_code");
            }
            if (mod.name.empty()) {
                mod.name = mod.id;
            }
            lib.mods.push_back(std::move(mod));
        }
    }
    const bool still_there = std::any_of(lib.mods.begin(), lib.mods.end(),
                                         [&](const ModInfo& m) { return m.id == lib.selected; });
    if (!still_there) {
        lib.selected = lib.mods.empty() ? std::string() : lib.mods.front().id;
    }
}

void ApplyConflicts(ModLibrary& lib, const Json& j) {
    lib.conflicts.clear();
    if (const auto it = j.find("conflicts"); it != j.end() && it->is_array()) {
        for (const auto& c : *it) {
            if (c.is_object()) {
                lib.conflicts.push_back({Str(c, "path"), Str(c, "winner"), Strings(c, "mods")});
            }
        }
    }
}

void ApplyStatus(ModLibrary& lib, const Json& j) {
    if (const auto deploy = j.find("deploy"); deploy != j.end() && deploy->is_object()) {
        lib.deploy_state = Str(*deploy, "state");
        lib.in_the_way = Strings(*deploy, "in_the_way");
        lib.problems = Strings(*deploy, "problems");
    }
    if (const auto code = j.find("code_library"); code != j.end() && code->is_object()) {
        lib.code_state = Str(*code, "state");
        lib.code_reason = Str(*code, "reason");
    }
}

/// The library as mods.py returns it after a command; errors to a dialog.
void HandleOverview(State& s, int exit_code, const std::string& output, bool quiet = false) {
    const Json j = Parse(output);
    if (exit_code == 0 && j.is_object() && Bool(j, "ok")) {
        ApplyOverview(s.mods, j);
        return;
    }
    const std::string error = ErrorText(j, output);
    LOG_WARNING(Frontend, "launcher: mods.py: {}", error);
    if (!s.mods.loaded) {
        s.mods.error = error.empty() ? "mods.py didn't say why." : error;
    }
    if (!quiet) {
        s.Message("The mod library", error);
    }
}

/// Conflicts and the code library's state, after the library changed.
void QueueDetails(State& s) {
    s.runner.Queue(Tools::ModsCommand(s.python, s.tools_dir, {"conflicts"}), "Reading the mod library",
                   [&s](int exit_code, const std::string& output) {
                       const Json j = Parse(output);
                       if (exit_code == 0 && j.is_object()) {
                           ApplyConflicts(s.mods, j);
                       }
                   });
    s.runner.Queue(Tools::ModsCommand(s.python, s.tools_dir, {"status"}), "Reading the mod library",
                   [&s](int exit_code, const std::string& output) {
                       const Json j = Parse(output);
                       if (exit_code == 0 && j.is_object()) {
                           ApplyStatus(s.mods, j);
                       }
                   });
    // (what of the catalog is installed follows the library)
    RefreshCatalog(s, false);
}

/// A command that changes the library (add, remove, enable, disable, order).
void Change(State& s, std::vector<std::string> args, std::string what) {
    s.runner.Queue(Tools::ModsCommand(s.python, s.tools_dir, args), std::move(what),
                   [&s](int exit_code, const std::string& output) {
                       HandleOverview(s, exit_code, output);
                   });
    QueueDetails(s);
}

const ModInfo* Selected(const State& s) {
    for (const auto& mod : s.mods.mods) {
        if (mod.id == s.mods.selected) {
            return &mod;
        }
    }
    return nullptr;
}

void Details(State& s, const ModInfo& mod) {
    Ui::PushFont(1.25f, true);
    ImGui::PushTextWrapPos(0.0f);
    ImGui::TextUnformatted(mod.name.c_str());
    ImGui::PopTextWrapPos();
    Ui::PopFont();
    std::string by;
    if (!mod.version.empty()) {
        by += "Version " + mod.version;
    }
    if (!mod.author.empty()) {
        by += (by.empty() ? "By " : ", by ") + mod.author;
    }
    if (!by.empty()) {
        Ui::TextDim(by);
    }
    if (!mod.description.empty()) {
        ImGui::Spacing();
        Ui::TextColored(Ui::Color::Text, mod.description);
    }
    if (!mod.url.empty()) {
        Ui::TextColored(Ui::Color::Info, mod.url);
    }
    ImGui::Spacing();
    std::string what = fmt::format("{} file{}", mod.files, mod.files == 1 ? "" : "s");
    if (mod.code && mod.adds_code) {
        what += ", a code mod that adds new code after the game's own (the code library has to be built again "
                "with it)";
    } else if (mod.code) {
        what += mod.changes_code == 1   ? ", a code mod that changes the game's code (the code library has to be built again)"
                : mod.changes_code == 0 ? ", a code mod that changes only data"
                                        : ", a code mod";
    }
    Ui::TextDim(what);
    for (const auto& error : mod.errors) {
        Ui::StatusLine(Ui::Status::Error, "Can't be used", error);
    }
    for (const auto& warning : mod.warnings) {
        Ui::StatusLine(Ui::Status::Warn, "Note", warning);
    }
    // the files it shares with other mods
    std::vector<const Conflict*> shared;
    for (const auto& c : s.mods.conflicts) {
        if (std::find(c.mods.begin(), c.mods.end(), mod.id) != c.mods.end()) {
            shared.push_back(&c);
        }
    }
    if (!shared.empty()) {
        ImGui::Spacing();
        Ui::PushFont(0.85f, true);
        ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "CONFLICTS (LATER MODS WIN)");
        Ui::PopFont();
        for (const auto* c : shared) {
            const bool wins = c->winner == mod.id;
            Ui::StatusLine(wins ? Ui::Status::Ok : Ui::Status::Warn, c->path,
                           wins ? "this mod's file is used" : "the file of " + c->winner + " is used");
        }
    }
}

/// A row's actions: up and down the order, remove (with a question first). True when one of
/// them has the focus.
bool RowActions(State& s, const ModInfo& mod, bool busy) {
    const int count = static_cast<int>(s.mods.mods.size());
    bool focused = false;
    ImGui::BeginDisabled(busy || mod.position <= 1);
    if (Ui::IconButton("##up", Ui::Glyph::Up)) {
        Change(s, {"order", mod.id, std::to_string(mod.position - 1)}, "Moving " + mod.name);
    }
    focused = focused || ImGui::IsItemFocused();
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled)) {
        ImGui::SetTooltip("Earlier in the order (later mods win)");
    }
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::BeginDisabled(busy || mod.position >= count);
    if (Ui::IconButton("##down", Ui::Glyph::Down)) {
        Change(s, {"order", mod.id, std::to_string(mod.position + 1)}, "Moving " + mod.name);
    }
    focused = focused || ImGui::IsItemFocused();
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled)) {
        ImGui::SetTooltip("Later in the order");
    }
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::BeginDisabled(busy);
    const bool remove = Ui::IconButton("##remove", Ui::Glyph::Remove);
    focused = focused || ImGui::IsItemFocused();
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled)) {
        ImGui::SetTooltip("Remove from the library");
    }
    if (remove) {
        Dialog d;
        d.title = "Remove " + mod.name + "?";
        d.text = "It is deleted from the mod library. Its files leave Azahar's mods folder the next "
                 "time the mods are put in place (when you play).";
        const std::string id = mod.id, name = mod.name;
        d.buttons = {{"Keep it", nullptr, true},
                     {"Remove", [&s, id, name] { Change(s, {"remove", id}, "Removing " + name); }, false}};
        s.Show(std::move(d));
    }
    ImGui::EndDisabled();
    return focused;
}

// ── the catalog ──────────────────────────────────────────────────────────────

long long Long(const Json& j, const char* key, long long def = -1) {
    const auto it = j.find(key);
    return it != j.end() && it->is_number_integer() ? it->get<long long>() : def;
}

bool Has(const std::vector<std::string>& list, const char* item) {
    return std::find(list.begin(), list.end(), item) != list.end();
}

std::string SizeText(long long n) {
    if (n < 0) {
        return "size not known";
    }
    if (n >= (1LL << 30)) {
        return fmt::format("{:.1f} GB", static_cast<double>(n) / (1LL << 30));
    }
    if (n >= (1LL << 20)) {
        return fmt::format("{:.1f} MB", static_cast<double>(n) / (1LL << 20));
    }
    return fmt::format("{} KB", std::max(1LL, (n + 512) / 1024));
}

void ApplyCatalog(ModCatalog& c, const Json& j) {
    c.loaded = true;
    c.error.clear();
    if (const auto info = j.find("catalog"); info != j.end() && info->is_object()) {
        c.source = Str(*info, "source");
        c.updated = Str(*info, "updated");
    }
    c.warnings = Strings(j, "warnings");
    c.textures_folder = Str(j, "textures_folder");
    c.downloads_folder = Str(j, "downloads_folder");
    c.entries.clear();
    if (const auto it = j.find("entries"); it != j.end() && it->is_array()) {
        for (const auto& e : *it) {
            if (!e.is_object()) {
                continue;
            }
            CatalogEntry x;
            x.id = Str(e, "id");
            x.kind = Str(e, "kind");
            x.name = Str(e, "name");
            x.author = Str(e, "author");
            x.version = Str(e, "version");
            x.description = Str(e, "description");
            x.details = Str(e, "details");
            x.category = Str(e, "category");
            x.region = Str(e, "region");
            x.region_note = Str(e, "region_note");
            x.license = Str(e, "license");
            x.page_url = Str(e, "page_url");
            x.note = Str(e, "note");
            x.recommended = Bool(e, "recommended");
            x.direct = Bool(e, "direct");
            x.update = Bool(e, "update");
            x.needs = Strings(e, "needs");
            x.conflicts = Strings(e, "conflicts");
            x.size = Long(e, "size");
            x.installed_size = Long(e, "installed_size");
            x.downloaded = Long(e, "downloaded", 0);
            if (const auto inst = e.find("installed"); inst != e.end() && inst->is_object()) {
                x.installed = true;
                x.installed_as = Str(*inst, "as");
                x.installed_id = Str(*inst, "id");
                x.installed_version = Str(*inst, "version");
                x.enabled = Bool(*inst, "enabled");
            }
            if (!x.id.empty()) {
                c.entries.push_back(std::move(x));
            }
        }
    }
    c.textures.clear();
    if (const auto it = j.find("textures"); it != j.end() && it->is_array()) {
        for (const auto& t : *it) {
            if (t.is_object()) {
                c.textures.push_back({Str(t, "id"), Str(t, "name"), Str(t, "version"), Str(t, "catalog"),
                                      Int(t, "files"), Long(t, "size")});
            }
        }
    }
    const bool still_there = std::any_of(c.entries.begin(), c.entries.end(),
                                         [&](const CatalogEntry& e) { return e.id == c.selected; });
    if (!still_there) {
        const auto rec = std::find_if(c.entries.begin(), c.entries.end(),
                                      [](const CatalogEntry& e) { return e.recommended; });
        c.selected = rec != c.entries.end() ? rec->id : c.entries.empty() ? std::string() : c.entries.front().id;
    }
}

void OpenUrl(State& s, const std::string& url) {
    if (SDL_OpenURL(url.c_str()) != 0) {
        s.Message("It can't be opened from here", "Open it yourself: " + url);
    }
}

void OpenFolder(State& s, const std::string& path) {
    FileUtil::CreateFullPath(path + "/");
    std::string url = path;
    std::replace(url.begin(), url.end(), '\\', '/');
    OpenUrl(s, (url.rfind('/', 0) == 0 ? "file://" : "file:///") + url);
}

std::string ParentOf(const std::string& path) {
    const auto slash = path.find_last_of("/\\");
    return slash == std::string::npos ? path : path.substr(0, slash);
}

void TurnOnTextures(State& s) {
    s.Change([](App::Config& c) { c.custom_textures = true; });
}

/// A download and install by mods.py in the job window, with its progress; `ok` gets its
/// result. A refusal that --force overcomes (another pack's files, files of the player's own)
/// asks first and runs `force` (if any).
void StartModsJob(State& s, std::vector<std::string> args, std::string title, std::function<void(const Json&)> ok,
                  std::function<void()> force, std::string page_url) {
    if (s.job.Active() || !s.ToolsReady()) {
        return;
    }
    auto process = std::make_unique<Process>();
    std::string error;
    if (!process->Start(Tools::ModsCommand(s.python, s.tools_dir, args), error, true)) {
        s.Message(title, error);
        return;
    }
    s.job = Job{};
    s.job.kind = Job::Kind::Download;
    s.job.title = title;
    s.job.cancellable = true;
    s.job.close_when_done = true;
    s.job.stop_question = "Stop it? A download goes on from where it stopped the next time.";
    s.job.keep_label = "Keep going";
    s.job.stop_label = "Stop it";
    s.job.process = std::move(process);
    s.job.on_done = [&s, title, ok, force, page_url](int exit_code, const std::string& output) {
        if (s.job.process && s.job.process->WasStopped()) {
            RefreshCatalog(s, false);
            return;
        }
        const Json j = Parse(output);
        if (exit_code == 0 && j.is_object() && Bool(j, "ok")) {
            LOG_INFO(Frontend, "launcher: {}: done", title);
            ok(j);
            return;
        }
        const Json err = j.is_object() && j.contains("error") && j["error"].is_object() ? j["error"] : Json::object();
        const std::string code = Str(err, "code");
        Dialog d;
        d.title = "It wasn't installed";
        d.text = ErrorText(j, output);
        LOG_WARNING(Frontend, "launcher: {}: {}", title, d.text);
        if (code == "manual_extract") {
            const std::string download = Str(err, "download"), target = Str(err, "target");
            d.title = "Unpack it yourself";
            d.text += fmt::format("\n\nThe download: {}\nIts files go in: {}", download, target);
            d.buttons = {{"Open the download's folder", [&s, download] { OpenFolder(s, ParentOf(download)); }, true},
                         {"Open where they go", [&s, target] { OpenFolder(s, target); }, false}};
            if (!page_url.empty()) {
                d.buttons.push_back({"Its page", [&s, page_url] { OpenUrl(s, page_url); }, false});
            }
            d.buttons.push_back({"Back", nullptr, false});
        } else if ((code == "conflict" || code == "foreign_files") && force) {
            d.details = Strings(err, "files");
            d.buttons = {{"Back", nullptr, true}, {"Install it anyway (replace them)", force, false}};
        } else {
            d.details = Strings(err, "problems");
            d.buttons = {{"Back", nullptr, true}};
            if (!page_url.empty()) {
                d.buttons.push_back({"Its page", [&s, page_url] { OpenUrl(s, page_url); }, false});
            }
        }
        s.Show(std::move(d));
        RefreshCatalog(s, false);
    };
}

void InstallFromCatalog(State& s, const CatalogEntry& e, bool force) {
    std::vector<std::string> args{"install-from-catalog", e.id};
    if (force) {
        args.push_back("--force");
    }
    const CatalogEntry entry = e;  // (the catalog is read again meanwhile)
    StartModsJob(
        s, args, "Installing " + e.name,
        [&s, entry](const Json& j) {
            RefreshMods(s);
            // where it went: the texture folder, or the library (on, or off behind another code mod)
            std::string text = "Its files are in the game's texture folder.";
            if (const auto mods = j.find("mods"); mods != j.end() && mods->is_array()) {
                const std::string id = Str(j, "mod");
                for (const auto& m : *mods) {
                    if (m.is_object() && Str(m, "id") == id) {
                        text = Bool(m, "enabled")
                                   ? "It is in your mods, turned on: it is put in place when you play."
                                   : "It is in your mods, turned off: another code mod is on, and only one can "
                                     "be (Your mods).";
                    }
                }
            }
            Dialog d;
            d.title = entry.name + " is installed";
            if (Has(entry.needs, "custom_textures") && !s.config.custom_textures) {
                d.text = text + " Texture packs show only with custom textures on (Settings). Turn them on now?";
                d.buttons = {{"Turn them on", [&s] { TurnOnTextures(s); }, true}, {"Not now", nullptr, false}};
            } else {
                d.text = text;
                if (Has(entry.needs, "cpu_emulation")) {
                    d.text += " With it on, the game runs on Azahar's own CPU emulation, without the port's code.";
                }
                d.buttons = {{"OK", nullptr, true}};
            }
            s.Show(std::move(d));
        },
        force ? std::function<void()>() : [&s, entry] { InstallFromCatalog(s, entry, true); }, e.page_url);
}

/// Install, asking first for a big download.
void AskInstall(State& s, const CatalogEntry& e) {
    if (e.size < (512LL << 20)) {
        InstallFromCatalog(s, e, false);
        return;
    }
    Dialog d;
    d.title = "Download " + e.name + "?";
    const long long left = std::max(0LL, e.size - e.downloaded);
    d.text = fmt::format("It is {} to download{}, and {} once installed: about {} have to be free while it "
                         "unpacks. It takes a while; you can stop it, and it goes on from there the next time.",
                         SizeText(left), e.downloaded > 0 ? " (the rest of it)" : "",
                         SizeText(e.installed_size >= 0 ? e.installed_size : e.size),
                         SizeText(e.size + std::max(0LL, e.installed_size)));
    const CatalogEntry entry = e;
    d.buttons = {{"Download and install", [&s, entry] { InstallFromCatalog(s, entry, false); }, true},
                 {"Back", nullptr, false}};
    s.Show(std::move(d));
}

void AskRemove(State& s, const std::string& id, const std::string& name, bool textures) {
    Dialog d;
    d.title = "Remove " + name + "?";
    d.text = textures ? "Its files are taken out of the game's texture folder (files of your own there stay)."
                      : "It is deleted from the mod library. Its files leave Azahar's mods folder the next time "
                        "the mods are put in place (when you play).";
    d.buttons = {{"Keep it", nullptr, true},
                 {"Remove", [&s, id, name] { Change(s, {"remove", id}, "Removing " + name); }, false}};
    s.Show(std::move(d));
}

/// A small label in a frame ("RECOMMENDED"), on the current line.
void Badge(const char* text, ImU32 color) {
    const float scale = Ui::Scale();
    const float line = ImGui::GetTextLineHeight();
    Ui::PushFont(0.68f, true);
    const ImVec2 size = ImGui::CalcTextSize(text);
    const ImVec2 pad(6.0f * scale, 2.0f * scale);
    ImVec2 pos = ImGui::GetCursorScreenPos();
    pos.y += std::max(0.0f, (line - size.y - pad.y * 2.0f) * 0.5f);
    const ImVec2 end(pos.x + size.x + pad.x * 2.0f, pos.y + size.y + pad.y * 2.0f);
    ImDrawList* draw = ImGui::GetWindowDrawList();
    draw->AddRectFilled(pos, end, (color & 0x00FFFFFF) | (40u << 24), 4.0f * scale);
    draw->AddRect(pos, end, color, 4.0f * scale);
    draw->AddText(ImVec2(pos.x + pad.x, pos.y + pad.y), color, text);
    Ui::PopFont();
    ImGui::Dummy(ImVec2(end.x - ImGui::GetCursorScreenPos().x, line));
}

const CatalogEntry* SelectedEntry(const State& s) {
    for (const auto& e : s.mods.catalog.entries) {
        if (e.id == s.mods.catalog.selected) {
            return &e;
        }
    }
    return nullptr;
}

std::string EntryLine(const CatalogEntry& e) {
    std::string line = e.category.empty() ? (e.kind == "textures" ? "Texture pack" : "Mod") : e.category;
    if (e.size >= 0) {
        line += " · " + SizeText(e.size);
    }
    if (!e.author.empty()) {
        line += " · " + e.author;
    }
    return line;
}

void CatalogDetails(State& s, const CatalogEntry& e, bool busy) {
    const auto& c = s.mods.catalog;
    Ui::PushFont(1.25f, true);
    ImGui::PushTextWrapPos(0.0f);
    ImGui::TextUnformatted(e.name.c_str());
    ImGui::PopTextWrapPos();
    Ui::PopFont();
    if (e.recommended) {
        Badge("RECOMMENDED", Ui::Color::Accent);
        ImGui::SameLine();
    }
    Badge(e.kind == "textures" ? "TEXTURE PACK" : "MOD", Ui::Color::Dim);
    if (!e.region.empty()) {
        ImGui::SameLine();
        Badge(e.region.c_str(), Ui::Color::Info);
    }
    std::string by;
    if (!e.version.empty()) {
        by += "Version " + e.version;
    }
    if (!e.author.empty()) {
        by += (by.empty() ? "By " : ", by ") + e.author;
    }
    if (!by.empty()) {
        Ui::TextDim(by);
    }
    ImGui::Spacing();

    // what can be done, first
    const bool textures_off = Has(e.needs, "custom_textures") && !s.config.custom_textures;
    ImGui::BeginDisabled(busy);
    if (e.direct) {
        const char* label = e.update ? "Update" : e.installed ? "Install again" : e.downloaded > 0 ? "Go on installing"
                                                                                                    : "Install";
        if (Ui::AccentButton(label)) {
            AskInstall(s, e);
        }
        ImGui::SameLine();
    }
    if (e.direct ? ImGui::Button("Open its page") : Ui::AccentButton("Open its page")) {
        OpenUrl(s, e.page_url);
    }
    if (e.installed) {
        ImGui::SameLine();
        if (ImGui::Button("Remove")) {
            AskRemove(s, e.installed_id.empty() ? e.id : e.installed_id, e.name, e.installed_as == "textures");
        }
        if (e.kind == "textures") {
            ImGui::SameLine();
            if (ImGui::Button("Open the texture folder")) {
                OpenFolder(s, c.textures_folder);
            }
        }
    }
    ImGui::EndDisabled();
    if (!e.direct) {
        Ui::TextDim("Installed from its page (its site asks you to sign in or to choose a file).");
    }
    if (e.installed) {
        std::string where = e.installed_as == "textures" ? "In the game's texture folder"
                            : e.enabled                  ? "In your mods, turned on"
                                                         : "In your mods, turned off (Your mods)";
        if (e.update) {
            where += fmt::format(" (version {}; the catalog has {})", e.installed_version, e.version);
        }
        Ui::StatusLine(Ui::Status::Ok, "Installed", where);
    } else if (e.downloaded > 0) {
        Ui::StatusLine(Ui::Status::Off, "Partly downloaded",
                       SizeText(e.downloaded) + " so far: installing goes on from there");
    }
    if (textures_off) {
        Ui::StatusLine(Ui::Status::Warn, "Custom textures are off", "Texture packs show only with them on.");
        ImGui::BeginDisabled(busy);
        if (ImGui::Button("Turn on custom textures")) {
            TurnOnTextures(s);
        }
        ImGui::EndDisabled();
    }
    std::string clash;
    for (const auto& other : c.entries) {
        if (other.installed && Has(e.conflicts, other.id.c_str())) {
            clash += (clash.empty() ? "" : ", ") + other.name;
        }
    }
    if (!clash.empty() && !e.installed) {
        Ui::StatusLine(Ui::Status::Warn, "Doesn't go with", clash + ", which is installed: remove it first");
    }

    // what it is
    ImGui::Spacing();
    if (!e.description.empty()) {
        Ui::TextColored(Ui::Color::Text, e.description);
    }
    if (!e.details.empty()) {
        Ui::PushFont(0.9f);
        Ui::TextDim(e.details);
        Ui::PopFont();
    }
    ImGui::Spacing();
    if (!e.note.empty()) {
        Ui::StatusLine(Ui::Status::Warn, "Note", e.note);
    }
    if (!e.region_note.empty()) {
        Ui::StatusLine(Ui::Status::Off, "Made for", e.region_note);
    }
    std::string size = e.size >= 0 ? SizeText(e.size) + " to download" : "Download size not known";
    if (e.installed_size >= 0) {
        size += ", " + SizeText(e.installed_size) + " installed";
    }
    Ui::StatusLine(Ui::Status::Off, "Size", size);
    if (!e.license.empty()) {
        Ui::StatusLine(Ui::Status::Off, "Licence", e.license);
    }
}

void DrawCatalog(State& s, bool busy) {
    auto& c = s.mods.catalog;
    const float scale = Ui::Scale();
    Ui::TextDim("Mods and texture packs picked for the port, installed with one click from their authors' own "
                "downloads. Texture packs go in the game's texture folder; mods join your mods.");
    ImGui::BeginDisabled(busy);
    FocusNext(s, !busy);
    if (ImGui::Button("Add a texture pack...")) {
        s.Pick(false, "A texture pack's archive", "Archives", {"zip", "7z", "tar", "gz", "tgz"},
               [&s](const std::string& path) {
                   StartModsJob(
                       s, {"install", "--textures", path}, "Installing a texture pack",
                       [&s](const Json&) {
                           RefreshCatalog(s, false);
                           if (!s.config.custom_textures) {
                               Dialog d;
                               d.title = "The texture pack is installed";
                               d.text = "Texture packs show only with custom textures on. Turn them on now?";
                               d.buttons = {{"Turn them on", [&s] { TurnOnTextures(s); }, true},
                                            {"Not now", nullptr, false}};
                               s.Show(std::move(d));
                           }
                       },
                       [&s, path] {
                           StartModsJob(s, {"install", "--textures", "--force", path}, "Installing a texture pack",
                                        [&s](const Json&) { RefreshCatalog(s, false); }, nullptr, "");
                       },
                       "");
               });
    }
    ImGui::SameLine();
    if (ImGui::Button("Open the texture folder")) {
        OpenFolder(s, c.textures_folder.empty() ? Tools::UserDir() + "/load/textures/0004000000126300"
                                                : c.textures_folder);
    }
    ImGui::SameLine();
    if (ImGui::Button("Check for a newer catalog")) {
        c.fetched = false;
        RefreshCatalog(s, true);
    }
    ImGui::EndDisabled();
    if (!c.loaded) {
        if (!c.error.empty()) {
            Ui::StatusLine(Ui::Status::Error, "The catalog can't be read", c.error);
        } else {
            Ui::Spinner();
            ImGui::SameLine();
            Ui::TextDim("Reading the catalog...");
        }
        return;
    }
    ImGui::Spacing();
    const ImVec2 avail = ImGui::GetContentRegionAvail();
    ImGui::BeginChild("##catalog_list", ImVec2(avail.x * 0.42f, 0), ImGuiChildFlags_NavFlattened);
    const float pad = 8.0f * scale;
    for (const auto& e : c.entries) {
        ImGui::PushID(e.id.c_str());
        const ImVec2 start = ImGui::GetCursorPos();
        const float h = ImGui::GetTextLineHeightWithSpacing() * 1.9f + pad;
        const bool selected = c.selected == e.id;
        if (ImGui::Selectable("##row", selected, ImGuiSelectableFlags_AllowOverlap, ImVec2(0, h))) {
            c.selected = e.id;
        }
        if (ImGui::IsItemFocused()) {
            c.selected = e.id;
        }
        const ImVec2 end = ImGui::GetCursorPos();
        ImGui::SetCursorPos(ImVec2(start.x + pad, start.y + pad * 0.6f));
        Ui::PushFont(1.0f, true);
        ImGui::TextUnformatted(e.name.c_str());
        Ui::PopFont();
        if (e.recommended) {
            ImGui::SameLine();
            Badge("RECOMMENDED", Ui::Color::Accent);
        }
        if (e.installed) {
            ImGui::SameLine();
            Badge(e.update ? "UPDATE" : "INSTALLED", e.update ? Ui::Color::Info : Ui::Color::Ok);
        }
        ImGui::SetCursorPosX(start.x + pad);
        Ui::PushFont(0.85f);
        ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "%s", EntryLine(e).c_str());
        Ui::PopFont();
        ImGui::SetCursorPos(end);
        ImGui::Dummy(ImVec2(0, 0));  // (an item where the cursor was moved: the list's extent)
        ImGui::PopID();
    }
    // texture packs installed otherwise (Add a texture pack...)
    bool heading = false;
    for (const auto& t : c.textures) {
        if (!t.catalog.empty()) {
            continue;
        }
        if (!heading) {
            ImGui::Spacing();
            Ui::PushFont(0.85f, true);
            ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "OTHER TEXTURE PACKS");
            Ui::PopFont();
            heading = true;
        }
        ImGui::PushID(t.id.c_str());
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted(fmt::format("{} ({} files)", t.name, t.files).c_str());
        ImGui::SameLine();
        ImGui::BeginDisabled(busy);
        if (Ui::IconButton("##remove", Ui::Glyph::Remove)) {
            AskRemove(s, t.id, t.name, true);
        }
        ImGui::EndDisabled();
        ImGui::PopID();
    }
    ImGui::EndChild();
    ImGui::SameLine(0, 20.0f * scale);
    ImGui::BeginChild("##catalog_details", ImVec2(0, 0), ImGuiChildFlags_NavFlattened);
    if (const CatalogEntry* e = SelectedEntry(s)) {
        Ui::BeginCard("##catalog_card", ImVec2(0, 0));
        CatalogDetails(s, *e, busy);
        Ui::EndCard();
    }
    // where the catalog came from (a catalog on the web that can't be had is no trouble: the
    // app's own is used)
    Ui::PushFont(0.8f);
    Ui::TextDim(fmt::format("Catalog of {}{}.", c.updated.empty() ? "an unknown date" : c.updated,
                            c.source == "remote" || c.source == "cache" ? ", from the web" : ", as it came with the app"));
    for (const auto& w : c.warnings) {
        Ui::TextDim(w);
    }
    Ui::PopFont();
    ImGui::EndChild();
}

} // namespace

void RefreshCatalog(State& s, bool fetch) {
    if (!s.ToolsReady()) {
        return;
    }
    std::vector<std::string> args{"catalog", "--catalog-url", s.config.catalog_url};
    if (fetch && !s.mods.catalog.fetched) {
        s.mods.catalog.fetched = true;
        if (!s.config.catalog_url.empty()) {
            args.push_back("--refresh");
        }
    }
    s.runner.Queue(Tools::ModsCommand(s.python, s.tools_dir, args), "Reading the mod catalog",
                   [&s](int exit_code, const std::string& output) {
                       const Json j = Parse(output);
                       if (exit_code == 0 && j.is_object() && Bool(j, "ok")) {
                           ApplyCatalog(s.mods.catalog, j);
                       } else if (!s.mods.catalog.loaded) {
                           s.mods.catalog.error = ErrorText(j, output);
                       }
                   });
}

void RefreshMods(State& s) {
    if (!s.ToolsReady()) {
        return;
    }
    s.runner.Queue(Tools::ModsCommand(s.python, s.tools_dir, {"list"}), "Reading the mod library",
                   [&s](int exit_code, const std::string& output) {
                       HandleOverview(s, exit_code, output, true);
                   });
    QueueDetails(s);
    if (!s.mods.catalog.fetched) {
        RefreshCatalog(s, true);  // a newer catalog from the web, once
    }
}

void DeployMods(State& s, bool force, std::function<void(bool without_code)> then) {
    if (s.job.Active() || !s.ToolsReady()) {
        return;
    }
    std::vector<std::string> args{"deploy"};
    if (force) {
        args.push_back("--force");
    }
    auto process = std::make_unique<Process>();
    std::string error;
    if (!process->Start(Tools::ModsCommand(s.python, s.tools_dir, args), error)) {
        s.Message("The mods weren't put in place", error);
        return;
    }
    s.job = Job{};
    s.job.kind = Job::Kind::Command;
    s.job.title = "Putting the mods in place";
    s.job.close_when_done = true;
    s.job.process = std::move(process);
    s.job.on_done = [&s, then](int exit_code, const std::string& output) {
        const Json j = Parse(output);
        const auto go_on = [then](const char* label, bool without_code) -> Dialog::Button {
            return {label, [then, without_code] { then(without_code); }, false};
        };
        if (exit_code == 0 && j.is_object() && Bool(j, "ok")) {
            ApplyStatus(s.mods, j);
            LOG_INFO(Frontend, "launcher: mods deployed");
            RefreshMods(s);
            s.CheckCode();
            if (s.mods.code_state == "rebuild") {
                Dialog d;
                d.title = "Build the code library again?";
                d.text = std::string(REBUILD_TEXT) + " Until then, the game runs on Azahar's own CPU emulation, "
                                                     "without the port's options.";
                d.buttons = {{"Build it now", [&s] { s.StartBuild(); }, true}};
                if (then) {
                    d.buttons.push_back(go_on("Play anyway", true));
                }
                d.buttons.push_back({"Later", nullptr, false});
                s.Show(std::move(d));
                return;
            }
            if (then) {
                then(false);
            }
            return;
        }
        const std::string code = j.is_object() && j.contains("error") && j["error"].is_object()
                                     ? Str(j["error"], "code")
                                     : std::string();
        Dialog d;
        if (code == "foreign_files") {
            d.title = "Files of your own are in the way";
            d.text = "Azahar's mods folder has files the mod manager didn't put there, and the mods "
                     "would replace them. Replace them with the mods' files? (To keep them, put them in "
                     "a mod of their own first.)";
            d.details = Strings(j["error"], "files");
            d.buttons = {{"Replace them", [&s, then] { DeployMods(s, true, then); }, true}};
            if (then) {
                d.buttons.push_back(go_on("Play without deploying", false));
            }
            d.buttons.push_back({"Back", nullptr, false});
        } else {
            d.title = "The mods can't be put in place";
            d.text = ErrorText(j, output);
            if (j.is_object() && j.contains("error") && j["error"].is_object()) {
                d.details = Strings(j["error"], "problems");
            }
            if (then) {
                d.buttons.push_back(go_on("Play anyway", false));
            }
            d.buttons.push_back({"Back", nullptr, true});
        }
        LOG_WARNING(Frontend, "launcher: deploy: {}", d.text);
        s.Show(std::move(d));
        RefreshMods(s);
    };
}

void DrawMods(State& s) {
    const float scale = Ui::Scale();
    const bool busy = s.runner.Busy() || s.job.Active();
    ImGui::BeginChild("##mods_screen", ImVec2(0, 0), ImGuiChildFlags_NavFlattened);
    Ui::Heading("Mods");
    if (s.probing) {
        Ui::Spinner();
        ImGui::SameLine();
        Ui::TextDim("Looking for the port's tools and Python...");
        ImGui::EndChild();
        return;
    }
    if (!s.ToolsReady()) {
        Ui::TextDim("The mod library is run by the port's tools (tools/mods.py), which need Python 3.10 "
                    "or newer.");
        if (s.tools_dir.empty()) {
            Ui::StatusLine(Ui::Status::Error, "The port's tools aren't here",
                           "Keep the package's mh4u-tools folder next to the app.");
        } else {
            Ui::StatusLine(Ui::Status::Error, "Python 3.10 or newer is needed", Tools::PythonHint(s.python));
        }
        FocusNext(s);
        if (ImGui::Button("Check again")) {
            s.ProbeTools();
        }
        ImGui::EndChild();
        return;
    }

    // your mods, or the catalog
    if (Ui::Tab("Your mods", !s.mods.show_catalog)) {
        s.mods.show_catalog = false;
    }
    ImGui::SameLine();
    if (Ui::Tab("Catalog", s.mods.show_catalog)) {
        s.mods.show_catalog = true;
        if (!s.mods.catalog.loaded) {
            RefreshCatalog(s, true);
        }
    }
    ImGui::Spacing();
    if (s.mods.show_catalog) {
        DrawCatalog(s, busy);
        ImGui::EndChild();
        return;
    }

    // the buttons that add mods
    ImGui::BeginDisabled(busy);
    // (the callbacks may run later, from a dialog: the state outlives them)
    const auto add = [&s](const std::string& path) { Change(s, {"add", path}, "Adding a mod"); };
    FocusNext(s, !busy);
    if (ImGui::Button("Add a mod's folder...")) {
        s.Pick(true, "A mod's folder", "", {}, add);
    }
    ImGui::SameLine();
    if (ImGui::Button("Add an archive...")) {
        s.Pick(false, "A mod's archive", "Archives (.zip, .7z, .tar)", {"zip", "7z", "tar", "gz", "tgz"}, add);
    }
    ImGui::SameLine();
    if (ImGui::Button("Refresh")) {
        RefreshMods(s);
    }
    ImGui::SameLine();
    ImGui::BeginDisabled(s.mods.deploy_state == "up_to_date" || s.mods.deploy_state.empty());
    if (ImGui::Button("Put them in place now")) {
        DeployMods(s, false, nullptr);
    }
    ImGui::EndDisabled();
    ImGui::EndDisabled();

    // the state of Azahar's mods folder and of the code library
    if (s.mods.loaded) {
        if (s.mods.deploy_state == "up_to_date") {
            Ui::StatusLine(Ui::Status::Ok, "Azahar's mods folder", "Up to date with the library");
        } else if (s.mods.deploy_state == "out_of_date") {
            Ui::StatusLine(Ui::Status::Off, "Azahar's mods folder", "Changed in the library: put in place when you play");
        } else if (s.mods.deploy_state == "blocked") {
            if (!s.mods.problems.empty()) {
                Ui::StatusLine(Ui::Status::Error, "Azahar's mods folder", "The mods can't be put in place as they are:");
                for (const auto& problem : s.mods.problems) {
                    Ui::TextColored(Ui::Color::Error, problem);
                }
            } else {
                std::string files;
                for (const auto& file : s.mods.in_the_way) {
                    files += (files.empty() ? "" : ", ") + file;
                }
                Ui::StatusLine(Ui::Status::Warn, "Azahar's mods folder",
                               "Files you put there yourself are where the mods go (" + files +
                                   "): you'll be asked whether to replace them.");
            }
        }
        if (s.mods.code_state == "rebuild") {
            Ui::StatusLine(Ui::Status::Warn, "The code library needs building again", REBUILD_TEXT);
            ImGui::BeginDisabled(busy || s.update.state != Checks::Update::State::Ok);
            if (Ui::AccentButton("Build the code library again")) {
                s.StartBuild();
            }
            ImGui::EndDisabled();
        }
    } else if (!s.mods.error.empty()) {
        Ui::StatusLine(Ui::Status::Error, "The mod library can't be read", s.mods.error);
    }
    ImGui::Spacing();

    if (s.mods.loaded && s.mods.mods.empty()) {
        Ui::BeginCard("##empty", ImVec2(0, 0));
        Ui::TextColored(Ui::Color::Text, "No mods yet.");
        Ui::TextDim("Pick one from the Catalog, or add a mod's folder or its archive (.zip, .7z): Azahar's "
                    "layout (romfs/, exefs/), a title folder (0004000000126300/, luma/titles/...), or the game's "
                    "own folders (data/...). Later mods win where two change the same file.");
        Ui::EndCard();
        ImGui::EndChild();
        return;
    }

    // the list, and the selected mod's details
    const ImVec2 avail = ImGui::GetContentRegionAvail();
    // (no scrolling table: that would be a child window, which a controller enters only with A;
    // the list's own window scrolls)
    // (a row: its switch, its name, what its conflicts come to, then up, down, remove: left and
    // right move along a row, up and down between rows)
    ImGui::BeginChild("##mod_list", ImVec2(avail.x * 0.62f, 0), ImGuiChildFlags_NavFlattened);
    if (ImGui::BeginTable("##mods_table", 4, ImGuiTableFlags_RowBg | ImGuiTableFlags_PadOuterX)) {
        ImGui::TableSetupColumn("On", ImGuiTableColumnFlags_WidthFixed);
        ImGui::TableSetupColumn("Mod", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableSetupColumn("Conflicts", ImGuiTableColumnFlags_WidthFixed);
        ImGui::TableSetupColumn("Actions", ImGuiTableColumnFlags_WidthFixed);
        Ui::TableHeaders({"ON", "MOD (LATER ONES WIN)", "CONFLICTS", ""});
        for (const auto& mod : s.mods.mods) {
            ImGui::PushID(mod.id.c_str());
            ImGui::TableNextRow();
            if (s.mods.selected == mod.id) {
                ImGui::TableSetBgColor(ImGuiTableBgTarget_RowBg1, IM_COL32(232, 163, 61, 28));
            }
            ImGui::TableNextColumn();
            bool on = mod.enabled;
            ImGui::BeginDisabled(busy || !mod.errors.empty());
            if (Ui::Toggle("##on", &on)) {
                Change(s, {on ? "enable" : "disable", mod.id}, (on ? "Turning on " : "Turning off ") + mod.name);
            }
            bool row_focused = ImGui::IsItemFocused();
            ImGui::EndDisabled();
            // the name: text a click selects (no stop for the keyboard and controller, whose up and
            // down go from switch to switch; the details follow the row they are on)
            ImGui::TableNextColumn();
            ImGui::AlignTextToFramePadding();
            std::string label = fmt::format("{}. {}", mod.position, mod.name);
            if (!mod.version.empty()) {
                label += "  " + mod.version;
            }
            ImGui::TextUnformatted(label.c_str());
            if (ImGui::IsItemClicked()) {
                s.mods.selected = mod.id;
            }
            ImGui::TableNextColumn();
            ImGui::AlignTextToFramePadding();
            if (!mod.errors.empty()) {
                ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Error), "can't be used");
            } else if (mod.wins + mod.loses == 0) {
                ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "%s",
                                   mod.is_new ? "new" : mod.code ? "code mod" : "-");
            } else {
                ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(mod.loses ? Ui::Color::Warn : Ui::Color::Ok),
                                   "wins %d, loses %d", mod.wins, mod.loses);
            }
            ImGui::TableNextColumn();
            row_focused = RowActions(s, mod, busy) || row_focused;
            if (row_focused) {
                s.mods.selected = mod.id;
            }
            ImGui::PopID();
        }
        ImGui::EndTable();
    }
    ImGui::EndChild();
    ImGui::SameLine(0, 20.0f * scale);
    ImGui::BeginChild("##mod_details", ImVec2(0, 0), ImGuiChildFlags_NavFlattened);
    if (const ModInfo* mod = Selected(s)) {
        Ui::BeginCard("##details_card", ImVec2(0, 0));
        Details(s, *mod);
        Ui::EndCard();
    }
    for (const auto& warning : s.mods.warnings) {
        Ui::StatusLine(Ui::Status::Warn, "Note", warning);
    }
    ImGui::EndChild();
    ImGui::EndChild();
}

} // namespace Launcher
