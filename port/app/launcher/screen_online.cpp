// Online: the room to play in. Solo, join (a lobby's list of rooms, or an address and a
// password) or host (port, name, password, players, listed in the lobby or not); your name in
// rooms, the lobby's address and your token there. The choice goes to mh4u-app.ini's
// [Multiplayer], which main.cpp's StartMultiplayer uses before the game starts.
#include <algorithm>
#include <cctype>
#include <fmt/format.h>
#include <imgui.h>
#include <SDL.h>

#include "invite.h"
#include "launcher/state.h"
#include "launcher/ui.h"

namespace Launcher {

namespace {

constexpr u16 DEFAULT_PORT = 24872;

/// Azahar's rule for nicknames (and the lobby's for names): 4 to 20 letters, digits, spaces,
/// '.', '_' or '-'.
bool ValidNickname(const std::string& name) {
    return name.size() >= 4 && name.size() <= 20 && std::all_of(name.begin(), name.end(), [](char c) {
               return std::isalnum(static_cast<unsigned char>(c)) || c == ' ' || c == '.' || c == '_' ||
                      c == '-';
           });
}

void LoadForm(State& s) {
    auto& f = s.online;
    const auto& c = s.config;
    f.loaded = true;
    f.address = c.join;
    f.address_password = c.join.empty() ? "" : c.password;
    f.port = c.host > 0 ? c.host : c.room_port > 0 ? c.room_port : DEFAULT_PORT;
    f.name = c.room_name;
    f.password = c.host > 0 ? c.password : "";
    f.members = c.room_members;
    f.listed = c.room_public;
    f.nickname = c.nickname;
    f.lobby = c.lobby;
    f.token = c.token;
    f.tab = c.host > 0 ? 1 : 0;
}

void JoinRoom(State& s, const std::string& address, const std::string& password, const std::string& label) {
    s.Change([&](App::Config& c) {
        c.join = address;
        c.password = password;
        c.host = 0;
    });
    s.room_label = label;
    s.online.address = address;
    s.online.address_password = password;
    s.GoTo(Screen::Home);
}

/// A text field that saves its setting when it is left after an edit.
void Field(State& s, const char* label, const char* hint, std::string& value, bool password,
           const std::function<void(App::Config&, const std::string&)>& save) {
    ImGui::AlignTextToFramePadding();
    ImGui::TextUnformatted(label);
    ImGui::TableNextColumn();
    ImGui::SetNextItemWidth(-FLT_MIN);
    Ui::InputText((std::string("##") + label).c_str(), &value, password ? ImGuiInputTextFlags_Password : 0, hint);
    if (ImGui::IsItemDeactivatedAfterEdit()) {
        const std::string v = value;
        s.Change([&](App::Config& c) { save(c, v); });
    }
}

void DrawJoin(State& s) {
    auto& f = s.online;
    const float scale = Ui::Scale();
    // the lobby's rooms
    if (s.config.lobby.empty()) {
        Ui::TextDim("No lobby is set: put one's address under Your name and lobby to see its rooms, or "
                    "join a room by its address below.");
    } else {
        ImGui::AlignTextToFramePadding();
        Ui::TextDim(fmt::format("Rooms in the lobby at {}", Lobby::NormalizeUrl(s.config.lobby)));
        ImGui::SameLine();
        ImGui::BeginDisabled(s.rooms_loading);
        if (ImGui::Button("Refresh")) {
            s.FetchRooms();
        }
        ImGui::EndDisabled();
        if (s.rooms_loading) {
            ImGui::SameLine();
            Ui::Spinner();
        }
        if (!s.rooms_fetched && !s.rooms_loading) {
            s.FetchRooms();
        }
        if (s.rooms_fetched && !s.rooms.ok) {
            Ui::StatusLine(Ui::Status::Error, "No list", s.rooms.error);
        } else if (s.rooms_fetched && s.rooms.rooms.empty()) {
            Ui::TextDim("No rooms are open in this lobby right now.");
        } else if (s.rooms_fetched) {
            // (no scrolling table: a controller would have to enter it with A; the screen scrolls)
            if (ImGui::BeginTable("##rooms", 5, ImGuiTableFlags_RowBg | ImGuiTableFlags_PadOuterX)) {
                ImGui::TableSetupColumn("##lock", ImGuiTableColumnFlags_WidthFixed, ImGui::GetTextLineHeight());
                ImGui::TableSetupColumn("Room", ImGuiTableColumnFlags_WidthStretch);
                ImGui::TableSetupColumn("Owner", ImGuiTableColumnFlags_WidthFixed, 160.0f * scale);
                ImGui::TableSetupColumn("Players", ImGuiTableColumnFlags_WidthFixed, 90.0f * scale);
                ImGui::TableSetupColumn("##join", ImGuiTableColumnFlags_WidthFixed);
                Ui::TableHeaders({"", "ROOM", "OWNER", "PLAYERS", ""});
                bool others = false;
                for (std::size_t i = 0; i < s.rooms.rooms.size(); ++i) {
                    const auto& room = s.rooms.rooms[i];
                    if (!room.mh4u && !others) {
                        others = true;
                        ImGui::TableNextRow();
                        ImGui::TableSetColumnIndex(1);
                        Ui::PushFont(0.85f, true);
                        ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "ROOMS FOR OTHER GAMES");
                        Ui::PopFont();
                    }
                    ImGui::PushID(static_cast<int>(i));
                    ImGui::TableNextRow();
                    ImGui::TableNextColumn();
                    ImGui::AlignTextToFramePadding();
                    if (room.password) {
                        Ui::Lock(Ui::Color::Warn);
                    }
                    ImGui::TableNextColumn();
                    ImGui::AlignTextToFramePadding();
                    ImGui::PushStyleColor(ImGuiCol_Text, room.mh4u ? Ui::Color::Text : Ui::Color::Dim);
                    ImGui::TextUnformatted(room.name.c_str());
                    ImGui::PopStyleColor();
                    if (!room.mh4u && !room.game.empty()) {
                        ImGui::SameLine();
                        Ui::TextDim("(" + room.game + ")");
                    } else if (!room.description.empty() && ImGui::IsItemHovered()) {
                        ImGui::SetTooltip("%s", room.description.c_str());
                    }
                    ImGui::TableNextColumn();
                    ImGui::AlignTextToFramePadding();
                    ImGui::TextUnformatted(room.owner.c_str());
                    ImGui::TableNextColumn();
                    ImGui::AlignTextToFramePadding();
                    const bool full = room.max_players > 0 && room.players.size() >= room.max_players;
                    ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(full ? Ui::Color::Error : Ui::Color::Text),
                                       "%u / %u", static_cast<unsigned>(room.players.size()),
                                       static_cast<unsigned>(room.max_players));
                    if (!room.players.empty() && ImGui::IsItemHovered()) {
                        std::string names;
                        for (const auto& p : room.players) {
                            names += (names.empty() ? "" : "\n") + p;
                        }
                        ImGui::SetTooltip("%s", names.c_str());
                    }
                    ImGui::TableNextColumn();
                    const std::string address = fmt::format("{}:{}", room.address, room.port);
                    const bool chosen = s.config.join == address && s.config.host == 0;
                    if (chosen ? Ui::AccentButton("Joining") : ImGui::Button("Join")) {
                        const std::string name = room.name;
                        if (room.password) {
                            Dialog d;
                            d.title = "Join " + room.name;
                            d.text = "This room has a password.";
                            d.input = true;
                            d.password = true;
                            d.input_hint = "the room's password";
                            d.on_input = [&s, address, name](const std::string& password) {
                                JoinRoom(s, address, password, name);
                            };
                            d.buttons = {{"Join", nullptr, true}, {"Cancel", nullptr, false}};
                            s.Show(std::move(d));
                        } else {
                            JoinRoom(s, address, "", name);
                        }
                    }
                    ImGui::PopID();
                }
                ImGui::EndTable();
            }
        }
    }
    ImGui::Spacing();
    // a room by its address
    Ui::PushFont(0.85f, true);
    ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "A ROOM BY ITS ADDRESS");
    Ui::PopFont();
    const float field = std::min(ImGui::GetContentRegionAvail().x * 0.4f, 380.0f * scale);
    ImGui::SetNextItemWidth(field);
    Ui::InputText("##address", &f.address, 0, "an invite (mh4u://...) or host:port");
    const auto room = App::Invite::Parse(f.address);
    if (!room.code) {
        ImGui::SameLine();
        ImGui::SetNextItemWidth(field * 0.6f);
        Ui::InputText("##address_password", &f.address_password, ImGuiInputTextFlags_Password, "password, if any");
    }
    ImGui::SameLine();
    if (ImGui::Button("Paste invite")) {
        f.paste_note.clear();
        if (char* text = SDL_GetClipboardText()) {
            const auto pasted = App::Invite::Parse(text);
            if (pasted.ok) {
                f.address = pasted.code ? App::Invite::Format(pasted.host, pasted.port, pasted.password)
                                        : App::Invite::Address(pasted.host, pasted.port);
            } else {
                f.paste_note = "The clipboard has no invite: " + pasted.error;
            }
            SDL_free(text);
        }
    }
    ImGui::SameLine();
    ImGui::BeginDisabled(!room.ok);
    if (ImGui::Button("Join this room")) {
        // an invite brings its password; mh4u-app.ini keeps the address and the password
        JoinRoom(s, App::Invite::Address(room.host, room.port), room.code ? room.password : f.address_password, "");
        f.address = room.code ? App::Invite::Format(room.host, room.port, room.password)
                              : App::Invite::Address(room.host, room.port);
    }
    ImGui::EndDisabled();
    if (!f.paste_note.empty()) {
        Ui::StatusLine(Ui::Status::Error, "No invite", f.paste_note);
    } else if (f.address.find_first_not_of(" ") != std::string::npos && !room.ok) {
        Ui::TextColored(Ui::Color::Warn, room.error);
    } else {
        Ui::TextDim("A host's invite (their menu's Multiplayer tab has one to copy) carries the address and "
                    "the password.");
    }
}

void DrawHost(State& s) {
    auto& f = s.online;
    const float scale = Ui::Scale();
    Ui::TextDim("Your PC hosts the room and plays in it. The others reach it at your address: forward "
                "UDP port 24872 (or the one below) on your router to this PC, or play in one virtual "
                "network (Tailscale, ZeroTier, Radmin VPN).");
    if (ImGui::BeginTable("##host", 2, ImGuiTableFlags_SizingStretchProp)) {
        ImGui::TableSetupColumn("label", ImGuiTableColumnFlags_WidthFixed, 200.0f * scale);
        ImGui::TableSetupColumn("value", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableNextRow();
        ImGui::TableNextColumn();
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted("Port (UDP)");
        ImGui::TableNextColumn();
        ImGui::SetNextItemWidth(160.0f * scale);
        if (ImGui::InputInt("##port", &f.port, 1, 10)) {
            f.port = std::clamp(f.port, 1, 65535);
        }
        ImGui::TableNextRow();
        ImGui::TableNextColumn();
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted("Room name");
        ImGui::TableNextColumn();
        ImGui::SetNextItemWidth(-FLT_MIN);
        Ui::InputText("##name", &f.name, 0, "MH4U");
        ImGui::TableNextRow();
        ImGui::TableNextColumn();
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted("Password");
        ImGui::TableNextColumn();
        ImGui::SetNextItemWidth(-FLT_MIN);
        Ui::InputText("##password", &f.password, ImGuiInputTextFlags_Password, "none: anyone may join");
        ImGui::TableNextRow();
        ImGui::TableNextColumn();
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted("Players");
        ImGui::TableNextColumn();
        ImGui::SetNextItemWidth(260.0f * scale);
        ImGui::SliderInt("##members", &f.members, 2, 16);
        ImGui::TableNextRow();
        ImGui::TableNextColumn();
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted("In the lobby");
        ImGui::TableNextColumn();
        const bool can_list = !s.config.lobby.empty() && !s.config.token.empty();
        ImGui::BeginDisabled(!can_list);
        bool listed = f.listed && can_list;
        if (Ui::Toggle(listed ? "Listed: players see it in the lobby##listed" : "Unlisted: players join by address##listed",
                       &listed)) {
            f.listed = listed;
        }
        ImGui::EndDisabled();
        if (!can_list) {
            Ui::TextDim("Listing it needs a lobby and your token there (Your name and lobby).");
        }
        ImGui::EndTable();
    }
    ImGui::Spacing();
    const bool hosting = s.config.host > 0;
    if (Ui::AccentButton(hosting ? "Host with these settings" : "Host this room")) {
        const auto form = f;
        s.Change([&](App::Config& c) {
            c.host = form.port;
            c.room_port = form.port;
            c.room_name = form.name.empty() ? "MH4U" : form.name;
            c.password = form.password;
            c.room_members = form.members;
            c.room_public = form.listed;
            c.join.clear();
        });
        s.room_label.clear();
        s.GoTo(Screen::Home);
    }
}

void DrawYou(State& s) {
    auto& f = s.online;
    const float scale = Ui::Scale();
    if (ImGui::BeginTable("##you", 2, ImGuiTableFlags_SizingStretchProp)) {
        ImGui::TableSetupColumn("label", ImGuiTableColumnFlags_WidthFixed, 200.0f * scale);
        ImGui::TableSetupColumn("value", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableNextRow();
        ImGui::TableNextColumn();
        Field(s, "Your name", "the console's user name", f.nickname, false,
              [](App::Config& c, const std::string& v) { c.nickname = v; });
        if (!f.nickname.empty() && !ValidNickname(f.nickname)) {
            Ui::TextColored(Ui::Color::Warn, "Rooms take 4 to 20 letters, digits, spaces, '.', '_' or '-'.");
        } else {
            Ui::TextDim("Your name in rooms, and in a lobby. A room lets in one player per name.");
        }
        ImGui::TableNextRow();
        ImGui::TableNextColumn();
        Field(s, "Lobby", "http://lobby.example.org:24872", f.lobby, false,
              [&s](App::Config& c, const std::string& v) {
                  c.lobby = v;
                  s.rooms_fetched = false;
              });
        Ui::TextDim("A lobby lists rooms (docs/SERVER.md). Without one, rooms are joined by address.");
        ImGui::TableNextRow();
        ImGui::TableNextColumn();
        Field(s, "Token", "your password in that lobby", f.token, true,
              [](App::Config& c, const std::string& v) { c.token = v; });
        Ui::TextDim("In an open lobby, the first use claims your name with it: keep using the same one, "
                    "and not one you use elsewhere (lobbies speak plain HTTP). Hosting a listed room "
                    "needs it; browsing and joining don't.");
        ImGui::EndTable();
    }
}

} // namespace

void State::FetchRooms() {
    if (rooms_loading || config.lobby.empty()) {
        return;
    }
    rooms_loading = true;
    rooms_future = std::async(std::launch::async, [url = config.lobby] { return Lobby::Fetch(url); });
}

void DrawOnline(State& s) {
    if (!s.online.loaded) {
        LoadForm(s);
    }
    ImGui::BeginChild("##online", ImVec2(0, 0), ImGuiChildFlags_NavFlattened);
    Ui::Heading("Online");
    Ui::TextDim("MH4U's local play, the Gathering Hall, over the internet through a room. The room chosen "
                "here is entered as the game starts; then go to the Gathering Hall's Local Play. In the game, "
                "Esc > Multiplayer joins, hosts and leaves rooms too, and has the invite to send friends.");
    ImGui::Spacing();
    Ui::BeginCard("##now", ImVec2(0, 0), s.config.host > 0 || !s.config.join.empty());
    ImGui::AlignTextToFramePadding();
    Ui::PushFont(0.85f, true);
    ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "WHEN YOU PLAY");
    Ui::PopFont();
    ImGui::SameLine();
    ImGui::AlignTextToFramePadding();
    ImGui::TextUnformatted(RoomSummary(s).c_str());
    if (s.config.host > 0 || !s.config.join.empty()) {
        ImGui::SameLine();
        if (ImGui::Button("Play solo")) {
            s.Change([](App::Config& c) {
                c.join.clear();
                c.host = 0;
            });
            s.room_label.clear();
        }
    }
    Ui::EndCard();
    ImGui::Spacing();
    const char* tabs[] = {"Join a room", "Host a room", "Your name and lobby"};
    for (int i = 0; i < 3; ++i) {
        if (i > 0) {
            ImGui::SameLine();
        }
        if (i == s.online.tab) {
            FocusNext(s);
        }
        if (Ui::Tab(tabs[i], s.online.tab == i)) {
            s.online.tab = i;
        }
    }
    ImGui::Spacing();
    switch (s.online.tab) {
    case 0:
        DrawJoin(s);
        break;
    case 1:
        DrawHost(s);
        break;
    default:
        DrawYou(s);
        break;
    }
    ImGui::EndChild();
}

} // namespace Launcher
