#include "ui/multiplayer_tab.h"

#include <algorithm>
#include <cfloat>
#include <chrono>
#include <future>
#include <string>
#include <vector>
#include <SDL.h>
#include <fmt/format.h>
#include <imgui.h>

#include "common/logging/log.h"
#include "config.h"
#include "invite.h"
#include "launcher/lobby.h"
#include "multiplayer.h"
#include "ui/layer.h"

namespace App::Ui {

namespace {

namespace MP = App::Multiplayer;

const ImVec4 WARN(1.0f, 0.75f, 0.45f, 1.0f);
const ImVec4 GOOD(0.55f, 0.85f, 0.55f, 1.0f);

/// What the tab keeps between frames: the forms, the lobby's list, the last copy.
struct TabState {
    bool loaded = false;
    int page = 0;  // 0: join, 1: host
    std::string invite;    // an invite code or an address
    std::string password;  // for an address without one
    std::string nickname;
    int port = Invite::DEFAULT_PORT;
    std::string name;
    std::string host_password;
    int slots = 4;
    bool listed = false;
    std::string paste_note;
    // the lobby's rooms
    std::future<Launcher::Lobby::List> fetch;
    bool fetching = false;
    bool fetched = false;
    std::string fetched_from;
    Launcher::Lobby::List rooms;
    // a listed room with a password: asked for in a popup
    bool ask_password = false;
    std::string ask_host, ask_label, ask_password_text;
    int ask_port = 0;
    // the invite's address (hosting), and the last copy
    int address = 0;
    std::chrono::steady_clock::time_point copied{};
};

TabState& Tab() {
    static TabState tab;
    return tab;
}

int ResizeCallback(ImGuiInputTextCallbackData* data) {
    if (data->EventFlag == ImGuiInputTextFlags_CallbackResize) {
        auto* text = static_cast<std::string*>(data->UserData);
        text->resize(static_cast<std::size_t>(data->BufTextLen));
        data->Buf = text->data();
    }
    return 0;
}

bool InputText(const char* id, std::string& text, const char* hint, ImGuiInputTextFlags flags = 0) {
    return ImGui::InputTextWithHint(id, hint, text.data(), text.capacity() + 1,
                                    flags | ImGuiInputTextFlags_CallbackResize, ResizeCallback, &text);
}

bool BeginForm(const char* id) {
    if (!ImGui::BeginTable(id, 2, ImGuiTableFlags_SizingStretchProp)) {
        return false;
    }
    ImGui::TableSetupColumn("name", ImGuiTableColumnFlags_WidthStretch, 0.32f);
    ImGui::TableSetupColumn("value", ImGuiTableColumnFlags_WidthStretch, 0.68f);
    return true;
}

void FormRow(const char* name) {
    ImGui::TableNextRow();
    ImGui::TableSetColumnIndex(0);
    ImGui::AlignTextToFramePadding();
    ImGui::TextUnformatted(name);
    ImGui::TableSetColumnIndex(1);
    ImGui::SetNextItemWidth(-FLT_MIN);
}

bool CancelPressed() {
    return ImGui::IsKeyPressed(ImGuiKey_Escape, false) || ImGui::IsKeyPressed(ImGuiKey_GamepadFaceRight, false);
}

void LoadForms(TabState& tab, const Config& c) {
    tab.loaded = true;
    tab.nickname = c.nickname;
    tab.invite = c.join;
    tab.password = c.join.empty() ? "" : c.password;
    tab.port = c.room_port > 0 ? c.room_port : Invite::DEFAULT_PORT;
    tab.name = c.room_name;
    tab.host_password = c.host > 0 ? c.password : "";
    tab.slots = std::clamp(c.room_members, 2, 16);
    tab.listed = c.room_public && !c.lobby.empty() && !c.token.empty();
    tab.page = c.host > 0 ? 1 : 0;
}

void StartFetch(TabState& tab, const std::string& lobby) {
    if (tab.fetching || lobby.empty()) {
        return;
    }
    tab.fetching = true;
    tab.fetched_from = lobby;
    tab.fetch = std::async(std::launch::async, [lobby] { return Launcher::Lobby::Fetch(lobby); });
}

void Copy(TabState& tab, const std::string& text) {
    SDL_SetClipboardText(text.c_str());
    tab.copied = std::chrono::steady_clock::now();
}

bool JustCopied(const TabState& tab) {
    return std::chrono::steady_clock::now() - tab.copied < std::chrono::seconds(3);
}

void Join(Layer& layer, const std::string& host, int port, const std::string& password, const std::string& label) {
    MP::JoinRequest request;
    request.host = host;
    request.port = port;
    request.password = password;
    request.label = label;
    layer.Post([request] { MP::Join(request); });
}

std::string Heard(double seconds) {
    if (seconds < 0) {
        return "not yet";
    }
    if (seconds < 2) {
        return "now";
    }
    if (seconds < 90) {
        return fmt::format("{:.0f} s ago", seconds);
    }
    return fmt::format("{:.0f} min ago", seconds / 60);
}

// ── The room you're in ──

void DrawStatus(const MP::Status& st) {
    if (st.phase == MP::Phase::Busy) {
        static const char* dots[] = {"", ".", "..", "..."};
        const int frame = static_cast<int>(ImGui::GetTime() * 3) % 4;
        ImGui::TextColored(WARN, "%s%s", st.action.c_str(), dots[frame]);
        return;
    }
    if (st.phase == MP::Phase::InRoom) {
        std::string names;
        for (const auto& m : st.members) {
            names += (names.empty() ? "" : ", ") + (m.you ? "you" : m.nickname);
        }
        ImGui::TextColored(GOOD, "In the room \"%s\"%s", st.room_name.c_str(),
                           st.hosting ? " (this PC hosts it)" : "");
        ImGui::SameLine();
        ImGui::TextDisabled("%zu of %u: %s", st.members.size(), st.slots, names.c_str());
    } else {
        ImGui::TextUnformatted("Not in a room: the game's local play reaches only this PC.");
    }
    if (!st.error.empty()) {
        ImGui::PushTextWrapPos(0.0f);
        ImGui::TextColored(WARN, "%s", st.error.c_str());
        if (!st.hint.empty()) {
            ImGui::TextDisabled("%s", st.hint.c_str());
        }
        ImGui::PopTextWrapPos();
    }
}

/// The invite friends paste: the room's own address when joined, this PC's when hosting.
void DrawInvite(Layer& layer, TabState& tab, const MP::Status& st) {
    std::string host = st.host;
    if (st.hosting) {
        // the addresses friends can use: the public one (the lobby's view), then this PC's own
        std::vector<std::string> hosts, labels;
        if (!st.public_address.empty()) {
            hosts.push_back(st.public_address);
            labels.push_back(st.public_address + "  (internet: needs the port forwarded)");
        }
        for (const auto& a : st.addresses) {
            if (a.ip == st.public_address) {
                continue;
            }
            const char* what = a.kind == "lan"         ? "your home network"
                               : a.kind == "tailscale" ? "Tailscale (or your provider's shared address)"
                               : a.kind == "radmin"    ? "Radmin VPN"
                               : a.kind == "public"    ? "internet"
                                                       : "another network";
            hosts.push_back(a.ip);
            labels.push_back(fmt::format("{}  ({})", a.ip, what));
        }
        if (hosts.empty()) {
            hosts.push_back("YOUR-PUBLIC-IP");
            labels.push_back("your public IP address (see below)");
        }
        tab.address = std::clamp(tab.address, 0, static_cast<int>(hosts.size()) - 1);
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted("Friends reach you at");
        ImGui::SameLine();
        ImGui::SetNextItemWidth(-FLT_MIN);
        if (ImGui::BeginCombo("##invite_address", labels[tab.address].c_str())) {
            for (int i = 0; i < static_cast<int>(labels.size()); ++i) {
                if (ImGui::Selectable(labels[i].c_str(), i == tab.address)) {
                    tab.address = i;
                }
            }
            ImGui::EndCombo();
        }
        layer.Help("At home: your home network's address. Over the internet: your public one (port "
                   "forwarded). Tailscale, ZeroTier, Radmin VPN: your address there.");
        host = hosts[tab.address];
    }
    std::string code = Invite::Format(host, st.port, st.password);
    ImGui::AlignTextToFramePadding();
    ImGui::TextUnformatted("Invite");
    ImGui::SameLine();
    const float button = ImGui::CalcTextSize("Copied!").x + ImGui::GetStyle().FramePadding.x * 2 + 4;
    ImGui::SetNextItemWidth(std::max(80.0f, ImGui::GetContentRegionAvail().x - button - ImGui::GetStyle().ItemSpacing.x));
    ImGui::InputText("##invite_code", code.data(), code.size() + 1, ImGuiInputTextFlags_ReadOnly);
    ImGui::SameLine();
    if (ImGui::Button(JustCopied(tab) ? "Copied!" : "Copy", ImVec2(button, 0))) {
        Copy(tab, code);
        LOG_INFO(Frontend, "menu: invite copied");
    }
    layer.Help("Send it to your friends; they use Paste invite here or in the launcher's Online screen. "
               "It carries the password.");
}

/// Hosting: what friends over the internet need, and the lobby's answer.
void DrawHostNotes(const MP::Status& st) {
    if (st.hosting) {
        ImGui::PushTextWrapPos(0.0f);
        if (st.public_address.empty()) {
            ImGui::TextDisabled(st.public_checked
                                    ? "Over the internet, give your public IP address (a search for \"what is "
                                      "my IP\" shows it; the lobby, if set, couldn't tell it)."
                                    : "Looking up your addresses...");
        }
        ImGui::TextDisabled("Over the internet, forward UDP port %d on your router to this PC and allow the app "
                            "in the firewall.%s",
                            st.port, st.lobby_note.empty() ? "" : (" " + st.lobby_note).c_str());
        ImGui::PopTextWrapPos();
    }
}

void DrawRoom(Layer& layer, TabState& tab, const MP::Status& st) {
    if (st.joined_in_game) {
        ImGui::PushTextWrapPos(0.0f);
        ImGui::TextColored(GOOD, "Next, in the game:");
        ImGui::SameLine();
        ImGui::TextUnformatted("go to the Gathering Hall and choose Local Play. One player creates a hall; "
                               "the others find it in the list and join it. If you were already in a hall "
                               "or searching for one, leave it and look again, so the game sees the others.");
        ImGui::PopTextWrapPos();
        ImGui::Spacing();
    }
    if (ImGui::BeginTable("##members", 3, ImGuiTableFlags_SizingStretchProp | ImGuiTableFlags_RowBg)) {
        ImGui::TableSetupColumn("Player", ImGuiTableColumnFlags_WidthStretch, 0.36f);
        ImGui::TableSetupColumn("Local play heard", ImGuiTableColumnFlags_WidthStretch, 0.26f);
        ImGui::TableSetupColumn("Playing", ImGuiTableColumnFlags_WidthStretch, 0.38f);
        ImGui::TableNextRow(ImGuiTableRowFlags_Headers);
        ImGui::TableSetBgColor(ImGuiTableBgTarget_RowBg0, ImGui::GetColorU32(ImGuiCol_TableHeaderBg));
        for (int column = 0; column < 3; ++column) {
            ImGui::TableSetColumnIndex(column);
            ImGui::TextUnformatted(ImGui::TableGetColumnName(column));
        }
        for (const auto& m : st.members) {
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0);
            if (m.username.empty() || m.username == m.nickname) {
                ImGui::Text("%s%s", m.nickname.c_str(), m.you ? " (you)" : "");
            } else {
                ImGui::Text("%s (%s)%s", m.nickname.c_str(), m.username.c_str(), m.you ? " (you)" : "");
            }
            ImGui::TableSetColumnIndex(1);
            if (m.you) {
                ImGui::TextDisabled("-");
            } else if (m.heard >= 0 && m.heard < 10) {
                ImGui::TextColored(GOOD, "%s", Heard(m.heard).c_str());
            } else {
                ImGui::TextDisabled("%s", Heard(m.heard).c_str());
            }
            ImGui::TableSetColumnIndex(2);
            ImGui::TextDisabled("%s", m.game.empty() ? "-" : m.game.c_str());
        }
        ImGui::EndTable();
    }
    layer.Help("When that player's game last sent local-play traffic (beacons, game data): in a village or "
               "a Gathering Hall it does.");
    ImGui::Spacing();
    DrawInvite(layer, tab, st);
    ImGui::Spacing();
    if (ImGui::Button("Leave the room")) {
        ImGui::OpenPopup("Leave the room?");
    }
    layer.Help("Your game loses the others (do it outside a hall or quest). The next start is solo; Rejoin "
               "last room brings you back.");
    if (st.hosting) {
        ImGui::SameLine();
        if (ImGui::Button("Close the room")) {
            ImGui::OpenPopup("Close the room?");
        }
        layer.Help("Everyone in it leaves.");
    }
    const auto confirm = [&layer](const char* title, const char* text, const char* yes, bool close) {
        ImGui::SetNextWindowPos(ImGui::GetMainViewport()->GetCenter(), ImGuiCond_Appearing, ImVec2(0.5f, 0.5f));
        if (ImGui::BeginPopupModal(title, nullptr, ImGuiWindowFlags_AlwaysAutoResize | ImGuiWindowFlags_NoSavedSettings)) {
            ImGui::TextUnformatted(text);
            ImGui::Spacing();
            if (ImGui::Button(yes)) {
                layer.Post([close] { MP::Leave(close); });
                ImGui::CloseCurrentPopup();
            }
            ImGui::SameLine();
            if (ImGui::Button("Cancel") || CancelPressed()) {
                ImGui::CloseCurrentPopup();
            }
            ImGui::SetItemDefaultFocus();
            ImGui::EndPopup();
        }
    };
    confirm("Leave the room?",
            st.hosting ? "The others stay in the room this PC hosts, but lose you in the game."
                       : "The others in your Gathering Hall lose you.",
            "Leave", false);
    confirm("Close the room?", "Everyone in it leaves, you too.", "Close", true);
    ImGui::Spacing();
    DrawHostNotes(st);
}

// ── Join ──

void DrawLobby(Layer& layer, TabState& tab, const Config& config) {
    if (config.lobby.empty()) {
        ImGui::PushTextWrapPos(0.0f);
        ImGui::TextDisabled("No lobby set (the launcher's Online screen, or lobby in mh4u-app.ini): join by "
                            "invite or address above.");
        ImGui::PopTextWrapPos();
        return;
    }
    if (tab.fetching && tab.fetch.valid() &&
        tab.fetch.wait_for(std::chrono::seconds(0)) == std::future_status::ready) {
        tab.rooms = tab.fetch.get();
        tab.fetching = false;
        tab.fetched = true;
    }
    if ((!tab.fetched && !tab.fetching) || tab.fetched_from != config.lobby) {
        tab.fetched = false;
        StartFetch(tab, config.lobby);
    }
    ImGui::AlignTextToFramePadding();
    ImGui::TextDisabled("Rooms in the lobby at %s", Launcher::Lobby::NormalizeUrl(config.lobby).c_str());
    ImGui::SameLine();
    ImGui::BeginDisabled(tab.fetching);
    if (ImGui::SmallButton(tab.fetching ? "Loading..." : "Refresh")) {
        StartFetch(tab, config.lobby);
    }
    ImGui::EndDisabled();
    if (!tab.fetched) {
        return;
    }
    if (!tab.rooms.ok) {
        ImGui::PushTextWrapPos(0.0f);
        ImGui::TextColored(WARN, "%s", tab.rooms.error.c_str());
        ImGui::PopTextWrapPos();
        return;
    }
    if (tab.rooms.rooms.empty()) {
        ImGui::TextDisabled("No rooms are open there right now. Host one (Host a room).");
        return;
    }
    if (ImGui::BeginTable("##lobby", 4, ImGuiTableFlags_SizingStretchProp | ImGuiTableFlags_RowBg)) {
        ImGui::TableSetupColumn("Room", ImGuiTableColumnFlags_WidthStretch, 0.46f);
        ImGui::TableSetupColumn("Owner", ImGuiTableColumnFlags_WidthStretch, 0.24f);
        ImGui::TableSetupColumn("Players", ImGuiTableColumnFlags_WidthStretch, 0.14f);
        ImGui::TableSetupColumn("##join", ImGuiTableColumnFlags_WidthStretch, 0.16f);
        int i = 0;
        for (const auto& room : tab.rooms.rooms) {
            ImGui::PushID(i++);
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0);
            ImGui::AlignTextToFramePadding();
            if (room.mh4u) {
                ImGui::Text("%s%s", room.name.c_str(), room.password ? " (password)" : "");
            } else {
                ImGui::TextDisabled("%s (%s)", room.name.c_str(), room.game.empty() ? "another game" : room.game.c_str());
            }
            ImGui::TableSetColumnIndex(1);
            ImGui::AlignTextToFramePadding();
            ImGui::TextUnformatted(room.owner.c_str());
            ImGui::TableSetColumnIndex(2);
            ImGui::AlignTextToFramePadding();
            ImGui::Text("%zu/%u", room.players.size(), room.max_players);
            ImGui::TableSetColumnIndex(3);
            const bool full = room.max_players > 0 && room.players.size() >= room.max_players;
            ImGui::BeginDisabled(full);
            if (ImGui::SmallButton(full ? "Full" : "Join")) {
                if (room.password) {
                    tab.ask_password = true;
                    tab.ask_host = room.address;
                    tab.ask_port = room.port;
                    tab.ask_label = room.name;
                    tab.ask_password_text.clear();
                } else {
                    Join(layer, room.address, room.port, "", room.name);
                }
            }
            ImGui::EndDisabled();
            ImGui::PopID();
        }
        ImGui::EndTable();
    }
    if (tab.ask_password) {
        ImGui::OpenPopup("The room's password");
        tab.ask_password = false;
    }
    ImGui::SetNextWindowPos(ImGui::GetMainViewport()->GetCenter(), ImGuiCond_Appearing, ImVec2(0.5f, 0.5f));
    if (ImGui::BeginPopupModal("The room's password", nullptr,
                               ImGuiWindowFlags_AlwaysAutoResize | ImGuiWindowFlags_NoSavedSettings)) {
        ImGui::Text("\"%s\" has a password.", tab.ask_label.c_str());
        if (ImGui::IsWindowAppearing()) {
            ImGui::SetKeyboardFocusHere();
        }
        ImGui::SetNextItemWidth(ImGui::GetFontSize() * 16);
        const bool enter = InputText("##ask_password", tab.ask_password_text, "password",
                                     ImGuiInputTextFlags_Password | ImGuiInputTextFlags_EnterReturnsTrue);
        if (ImGui::Button("Join") || enter) {
            Join(layer, tab.ask_host, tab.ask_port, tab.ask_password_text, tab.ask_label);
            ImGui::CloseCurrentPopup();
        }
        ImGui::SameLine();
        if (ImGui::Button("Cancel") || (CancelPressed() && !ImGui::IsItemActive())) {
            ImGui::CloseCurrentPopup();
        }
        ImGui::EndPopup();
    }
}

void DrawJoin(Layer& layer, TabState& tab, const Config& config, bool busy) {
    const MP::Status st = MP::GetStatus();
    const std::string last = MP::LastRoom();
    if (!last.empty()) {
        ImGui::BeginDisabled(busy);
        if (ImGui::Button("Rejoin last room")) {
            layer.Post([] { MP::Rejoin(); });
        }
        ImGui::EndDisabled();
        ImGui::SameLine();
        ImGui::TextDisabled("%s", last.c_str());
        layer.Help("The last room you joined or hosted, again.");
        ImGui::Spacing();
    }
    if (BeginForm("##join")) {
        FormRow("Invite or address");
        const float paste = ImGui::CalcTextSize("Paste invite").x + ImGui::GetStyle().FramePadding.x * 2;
        ImGui::SetNextItemWidth(std::max(80.0f, ImGui::GetContentRegionAvail().x - paste - ImGui::GetStyle().ItemSpacing.x));
        InputText("##invite", tab.invite, "mh4u://... or HOST:PORT");
        layer.Help("Paste the invite a host gave you, or type their address (port 24872 if left out).");
        ImGui::SameLine();
        if (ImGui::Button("Paste invite")) {
            tab.paste_note.clear();
            if (char* text = SDL_GetClipboardText()) {
                const auto room = Invite::Parse(text);
                if (room.ok) {
                    tab.invite = room.code ? Invite::Format(room.host, room.port, room.password)
                                           : Invite::Address(room.host, room.port);
                } else {
                    tab.paste_note = "The clipboard has no invite: " + room.error;
                }
                SDL_free(text);
            }
        }
        layer.Help("Takes the invite from the clipboard (it can sit in a longer message).");
        const auto room = Invite::Parse(tab.invite);
        if (!room.code) {
            FormRow("Password");
            InputText("##password", tab.password, "if the room has one", ImGuiInputTextFlags_Password);
        }
        FormRow("Your name");
        InputText("##nickname", tab.nickname, "the console's user name");
        if (ImGui::IsItemDeactivatedAfterEdit()) {
            const std::string name = tab.nickname;
            layer.config.nickname = name;
            layer.Post([name] {
                Config file = App::Load();
                file.nickname = name;
                App::Save(file);
            });
        }
        layer.Help("Your name in rooms: 4 to 20 letters, digits, spaces, '.', '_' or '-'; one player per name.");
        ImGui::EndTable();
    }
    if (!tab.paste_note.empty()) {
        ImGui::TextColored(WARN, "%s", tab.paste_note.c_str());
    }
    if (!tab.nickname.empty() && !MP::ValidNickname(tab.nickname)) {
        ImGui::TextColored(WARN, "Rooms take names of 4 to 20 letters, digits, spaces, '.', '_' or '-'.");
    }
    const auto room = Invite::Parse(tab.invite);
    if (!tab.invite.empty() && !room.ok) {
        ImGui::TextColored(WARN, "%s", room.error.c_str());
    }
    ImGui::BeginDisabled(busy || !room.ok);
    if (ImGui::Button(st.phase == MP::Phase::InRoom ? "Join this room instead" : "Join")) {
        Join(layer, room.host, room.port, room.code ? room.password : tab.password, "");
    }
    ImGui::EndDisabled();
    layer.Help("Joins while the game runs; the next start joins it again.");
    ImGui::Spacing();
    ImGui::SeparatorText("Lobby");
    DrawLobby(layer, tab, config);
}

// ── Host ──

void DrawHost(Layer& layer, TabState& tab, const Config& config, bool busy) {
    ImGui::PushTextWrapPos(0.0f);
    ImGui::TextDisabled("This PC runs the room and plays in it; you get an invite to send. Friends over the "
                        "internet need the port forwarded on your router to this PC (or one virtual network).");
    ImGui::PopTextWrapPos();
    if (BeginForm("##host")) {
        FormRow("Room name");
        InputText("##name", tab.name, "MH4U");
        FormRow("Port (UDP)");
        if (ImGui::InputInt("##port", &tab.port, 1, 10)) {
            tab.port = std::clamp(tab.port, 1, 65535);
        }
        layer.Help("24872 unless something else uses it. Friends over the internet need it forwarded.");
        FormRow("Players");
        ImGui::SliderInt("##slots", &tab.slots, 2, 16);
        layer.Help("A Gathering Hall takes 4 hunters; more room members can be in other halls.");
        FormRow("Password");
        InputText("##host_password", tab.host_password, "none: anyone with the address may join",
                  ImGuiInputTextFlags_Password);
        FormRow("In the lobby");
        const bool can_list = !config.lobby.empty() && !config.token.empty();
        bool listed = tab.listed && can_list;
        ImGui::BeginDisabled(!can_list);
        if (ImGui::Checkbox(can_list ? "Listed: players see it in the lobby" : "Needs a lobby and your token there",
                            &listed)) {
            tab.listed = listed;
        }
        ImGui::EndDisabled();
        layer.Help("The lobby and your token are set in the launcher's Online screen (or mh4u-app.ini).");
        ImGui::EndTable();
    }
    ImGui::BeginDisabled(busy);
    if (ImGui::Button("Host this room")) {
        MP::HostRequest request;
        request.port = tab.port;
        request.name = tab.name.empty() ? "MH4U" : tab.name;
        request.slots = tab.slots;
        request.password = tab.host_password;
        request.listed = tab.listed && !config.lobby.empty() && !config.token.empty();
        layer.Post([request] { MP::Host(request); });
    }
    ImGui::EndDisabled();
    layer.Help("Opens the room now (leaving the one you're in); the next start hosts it again.");
}

} // namespace

void DrawMultiplayerTab(Layer& layer) {
    TabState& tab = Tab();
    const Config& config = layer.config;
    if (!tab.loaded) {
        LoadForms(tab, config);
    }
    const MP::Status st = MP::GetStatus();
    const bool busy = st.phase == MP::Phase::Busy;
    DrawStatus(st);
    ImGui::Spacing();
    if (st.phase == MP::Phase::InRoom) {
        DrawRoom(layer, tab, st);
        ImGui::Spacing();
        ImGui::SeparatorText("Another room");
    } else if (st.hosting && !busy) {
        // left the room this PC hosts: it still relays the others
        ImGui::TextDisabled("This PC still runs the room on port %d for the others.", st.port);
        ImGui::SameLine();
        if (ImGui::SmallButton("Close it")) {
            layer.Post([] { MP::Leave(true); });
        }
        ImGui::Spacing();
    }
    const char* pages[] = {"Join a room", "Host a room"};
    for (int i = 0; i < 2; ++i) {
        if (i > 0) {
            ImGui::SameLine();
        }
        const bool selected = tab.page == i;
        if (selected) {
            ImGui::PushStyleColor(ImGuiCol_Button, ImGui::GetStyleColorVec4(ImGuiCol_ButtonActive));
        }
        if (ImGui::Button(pages[i])) {
            tab.page = i;
        }
        if (selected) {
            ImGui::PopStyleColor();
        }
    }
    ImGui::Spacing();
    if (tab.page == 0) {
        DrawJoin(layer, tab, config, busy);
    } else {
        DrawHost(layer, tab, config, busy);
    }
}

} // namespace App::Ui
