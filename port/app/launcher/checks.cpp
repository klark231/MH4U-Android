#include "launcher/checks.h"

#include <fmt/format.h>

#include "common/file_util.h"
#include "common/logging/log.h"
#include "core/file_sys/ncch_container.h"
#include "core/file_sys/title_metadata.h"
#include "core/hle/service/am/am.h"
#include "core/hle/service/fs/archive.h"
#include "core/loader/loader.h"

namespace Launcher::Checks {

Game CheckGame(const std::string& path) {
    Game game;
    if (path.empty()) {
        return game;
    }
    if (!FileUtil::Exists(path) || FileUtil::IsDirectory(path)) {
        game.state = Game::State::Missing;
        return game;
    }
    FileSys::NCCHContainer ncch(path);
    const auto status = ncch.LoadHeader();
    switch (status) {
    case Loader::ResultStatus::Success:
        game.program_id = ncch.ncch_header.program_id;
        game.state = game.program_id == MH4U_USA ? Game::State::Ok : Game::State::OtherGame;
        break;
    case Loader::ResultStatus::ErrorEncrypted:
        // the header itself is readable: it says which game it is
        game.program_id = ncch.ncch_header.program_id;
        game.state = Game::State::Encrypted;
        break;
    default:
        game.state = Game::State::NotA3ds;
        break;
    }
    return game;
}

std::string Describe(const Game& game) {
    using State = Game::State;
    switch (game.state) {
    case State::Unset:
        return "Choose your decrypted Monster Hunter 4 Ultimate (.3ds, .cci or .cxi).";
    case State::Missing:
        return "The game file isn't there any more: choose it again.";
    case State::NotA3ds:
        return "This file isn't a 3DS game the app can read: choose your decrypted .3ds, .cci or "
               ".cxi.";
    case State::Encrypted:
        return fmt::format("This {} is encrypted. The port needs a decrypted dump of your own copy "
                           "(GodMode9 on your 3DS can make one).",
                           game.program_id == MH4U_USA ? "copy of Monster Hunter 4 Ultimate"
                                                       : "game file");
    case State::OtherGame:
        return fmt::format("This is another 3DS game (program id {:016X}), not Monster Hunter 4 "
                           "Ultimate for the USA ({:016X}), the version the port is made for.",
                           game.program_id, MH4U_USA);
    case State::Ok:
        return "Monster Hunter 4 Ultimate (USA), decrypted.";
    }
    return "";
}

Update CheckUpdate() {
    Update update;
    const auto media = Service::FS::MediaType::SDMC;
    FileSys::TitleMetadata tmd;
    if (tmd.Load(Service::AM::GetTitleMetadataPath(media, MH4U_UPDATE)) !=
        Loader::ResultStatus::Success) {
        return update;
    }
    update.version = tmd.GetTitleVersion();
    update.content = Service::AM::GetTitleContentPath(media, MH4U_UPDATE, 0);
    if (update.content.empty() || !FileUtil::Exists(update.content)) {
        update.content.clear();
        return update;
    }
    update.state = update.version == UPDATE_VERSION ? Update::State::Ok : Update::State::OtherVersion;
    return update;
}

std::string VersionText(u16 version) {
    // the 3DS's title versions: major in bits 10-15, minor in 4-9, micro in 0-3
    return fmt::format("{}.{}.{}", version >> 10, (version >> 4) & 0x3F, version & 0xF);
}

std::string CheckUpdateCia(const std::string& path) {
    bool compressed = false;
    switch (Service::AM::CheckCIAToInstall(path, compressed, true)) {
    case Service::AM::InstallStatus::Success:
        break;
    case Service::AM::InstallStatus::ErrorEncrypted:
        return "This .cia is encrypted. The port needs a decrypted one (GodMode9 can build one from "
               "the update installed on your 3DS).";
    case Service::AM::InstallStatus::ErrorFileNotFound:
    case Service::AM::InstallStatus::ErrorFailedToOpenFile:
        return "The file can't be read.";
    default:
        return "This file isn't a CIA the app can read.";
    }
    if (const auto infos = Service::AM::GetCIAInfos(path)) {
        const auto& info = infos->first;
        if (info.tid != MH4U_UPDATE) {
            return fmt::format("This .cia is title {:016X}, not Monster Hunter 4 Ultimate's update "
                               "({:016X}).",
                               info.tid, MH4U_UPDATE);
        }
        if (info.version != UPDATE_VERSION) {
            return fmt::format("This is Monster Hunter 4 Ultimate's update v{}; the port is made for "
                               "v{}.",
                               VersionText(info.version), VersionText(UPDATE_VERSION));
        }
    }
    return "";
}

std::string LibraryPath() {
#if defined(_WIN32)
    constexpr const char* ext = "dll";
#elif defined(__APPLE__)
    constexpr const char* ext = "dylib";
#else
    constexpr const char* ext = "so";
#endif
    return fmt::format("{}recomp/{:016X}.{}", FileUtil::GetUserPath(FileUtil::UserPath::UserDir),
                       MH4U_USA, ext);
}

Code CodeFromCheck(int exit_code, const std::string& output) {
    Code code;
    std::string notice;
    if (const auto at = output.find("notice:\n"); at != std::string::npos) {
        notice = output.substr(at + 8);
        // the first paragraph: what happened (the rest says how to build the library, which
        // the launcher's own button does)
        if (const auto end = notice.find("\n\n"); end != std::string::npos) {
            notice.resize(end);
        }
        while (!notice.empty() && (notice.back() == '\n' || notice.back() == '\r')) {
            notice.pop_back();
        }
    }
    switch (exit_code) {
    case 0:
        code.state = Code::State::Ok;
        break;
    case 3:
        code.state = Code::State::Partial;
        code.notice = notice;
        break;
    case 2:
        code.state = Code::State::NotUsed;
        code.notice = notice.empty() ? "The game would run without the port's code library."
                                     : notice;
        break;
    default:
        code.state = Code::State::Failed;
        code.notice = output.empty() ? fmt::format("The check ended with status {}.", exit_code)
                                     : output.substr(0, 400);
        break;
    }
    return code;
}

Install::~Install() {
    if (thread.joinable()) {
        thread.join();
    }
}

void Install::Start(const std::string& path) {
    if (thread.joinable()) {
        thread.join();
    }
    error.clear();
    progress = 0.0f;
    running = true;
    thread = std::thread([this, path] {
        const auto status =
            Service::AM::InstallCIA(path, [this](std::size_t done, std::size_t total) {
                if (total > 0) {
                    progress = static_cast<float>(static_cast<double>(done) / static_cast<double>(total));
                }
            });
        switch (status) {
        case Service::AM::InstallStatus::Success:
            break;
        case Service::AM::InstallStatus::ErrorEncrypted:
            error = "The .cia is encrypted: the port needs a decrypted one.";
            break;
        case Service::AM::InstallStatus::ErrorFileNotFound:
        case Service::AM::InstallStatus::ErrorFailedToOpenFile:
            error = "The .cia can't be read.";
            break;
        case Service::AM::InstallStatus::ErrorAborted:
            error = "Installing stopped half-way (is the disk full?). The log has the details.";
            break;
        default:
            error = "This file isn't a CIA that can be installed.";
            break;
        }
        LOG_INFO(Frontend, "installing {}: {}", path, error.empty() ? "done" : error);
        progress = 1.0f;
        running = false;
    });
}

} // namespace Launcher::Checks
