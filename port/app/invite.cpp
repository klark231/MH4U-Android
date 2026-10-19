#include "invite.h"

#include <cctype>
#include <cstdlib>

namespace App::Invite {

namespace {

constexpr const char SCHEME[] = "mh4u://";
constexpr std::size_t SCHEME_LENGTH = sizeof(SCHEME) - 1;

bool IsSpace(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

std::string Trim(const std::string& s) {
    std::size_t a = 0, b = s.size();
    while (a < b && IsSpace(s[a])) {
        ++a;
    }
    while (b > a && IsSpace(s[b - 1])) {
        --b;
    }
    return s.substr(a, b - a);
}

std::string Lower(std::string s) {
    for (char& c : s) {
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return s;
}

int HexDigit(char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
}

std::string PercentDecode(const std::string& s) {
    std::string out;
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '%' && i + 2 < s.size() && HexDigit(s[i + 1]) >= 0 && HexDigit(s[i + 2]) >= 0) {
            out += static_cast<char>(HexDigit(s[i + 1]) * 16 + HexDigit(s[i + 2]));
            i += 2;
        } else if (s[i] == '+') {
            out += ' ';
        } else {
            out += s[i];
        }
    }
    return out;
}

std::string PercentEncode(const std::string& s) {
    static const char* HEX = "0123456789ABCDEF";
    std::string out;
    for (const char ch : s) {
        const auto c = static_cast<unsigned char>(ch);
        if (std::isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
            out += ch;
        } else {
            out += '%';
            out += HEX[c >> 4];
            out += HEX[c & 15];
        }
    }
    return out;
}

/// A host name or IPv4 address: letters, digits, '-', '.', '_'.
bool ValidHost(const std::string& host) {
    if (host.empty() || host.size() > 253) {
        return false;
    }
    for (const char ch : host) {
        const auto c = static_cast<unsigned char>(ch);
        if (!std::isalnum(c) && c != '-' && c != '.' && c != '_') {
            return false;
        }
    }
    return true;
}

/// HOST[:PORT] into `room`.
bool ParseAddress(const std::string& text, Room& room) {
    std::string host = text;
    if (const auto colon = host.rfind(':'); colon != std::string::npos) {
        const std::string port = host.substr(colon + 1);
        host = host.substr(0, colon);
        if (port.empty() || port.size() > 5 ||
            port.find_first_not_of("0123456789") != std::string::npos) {
            room.error = "The port after the ':' must be a number (24872 unless the host changed it).";
            return false;
        }
        room.port = std::atoi(port.c_str());
        if (room.port < 1 || room.port > 65535) {
            room.error = "The port must be between 1 and 65535.";
            return false;
        }
    }
    if (host.find(':') != std::string::npos || host.find('[') != std::string::npos) {
        room.error = "Rooms take IPv4 addresses or host names, not IPv6 addresses.";
        return false;
    }
    if (!ValidHost(host)) {
        room.error = host.empty() ? "There is no address in it." : "\"" + host + "\" isn't an address.";
        return false;
    }
    room.host = host;
    return true;
}

} // namespace

Room Parse(const std::string& input) {
    Room room;
    const std::string text = Trim(input);
    if (text.empty()) {
        room.error = "Nothing to join: paste an invite (mh4u://...) or type an address.";
        return room;
    }
    const auto at = Lower(text).find(SCHEME);
    if (at == std::string::npos) {
        // a plain address: the whole text
        if (text.find_first_of(" \t/?") != std::string::npos) {
            room.error = "That isn't an invite (mh4u://...) or an address (HOST or HOST:PORT).";
            return room;
        }
        room.ok = ParseAddress(text, room);
        return room;
    }
    // the code runs to the next space (or quote, or bracket: it may sit in a chat message)
    std::size_t end = at + SCHEME_LENGTH;
    while (end < text.size() && !IsSpace(text[end]) && text[end] != '"' && text[end] != '\'' &&
           text[end] != '<' && text[end] != '>' && text[end] != ')') {
        ++end;
    }
    std::string rest = text.substr(at + SCHEME_LENGTH, end - at - SCHEME_LENGTH);
    std::string query;
    if (const auto q = rest.find('?'); q != std::string::npos) {
        query = rest.substr(q + 1);
        rest = rest.substr(0, q);
    }
    while (!rest.empty() && rest.back() == '/') {
        rest.pop_back();
    }
    room.code = true;
    if (!ParseAddress(rest, room)) {
        return room;
    }
    // query: password=... (also pw=...), other keys ignored (later versions may add some)
    std::size_t start = 0;
    while (!query.empty()) {
        const auto amp = query.find('&', start);
        const std::string pair = query.substr(start, amp == std::string::npos ? std::string::npos : amp - start);
        const auto eq = pair.find('=');
        const std::string key = Lower(pair.substr(0, eq));
        if (eq != std::string::npos && (key == "password" || key == "pw")) {
            room.password = PercentDecode(pair.substr(eq + 1));
        }
        if (amp == std::string::npos) {
            break;
        }
        start = amp + 1;
    }
    room.ok = true;
    return room;
}

std::string Format(const std::string& host, int port, const std::string& password) {
    std::string code = std::string(SCHEME) + Address(host, port);
    if (!password.empty()) {
        code += "?password=" + PercentEncode(password);
    }
    return code;
}

std::string Address(const std::string& host, int port) {
    return host + ":" + std::to_string(port);
}

std::string NetworkKind(const std::string& ipv4) {
    int part[4];
    std::size_t at = 0;
    for (int i = 0; i < 4; ++i) {
        std::size_t end = at;
        while (end < ipv4.size() && std::isdigit(static_cast<unsigned char>(ipv4[end]))) {
            ++end;
        }
        if (end == at || end - at > 3 || (i < 3 && (end >= ipv4.size() || ipv4[end] != '.'))) {
            return "";
        }
        part[i] = std::atoi(ipv4.substr(at, end - at).c_str());
        if (part[i] > 255) {
            return "";
        }
        at = end + 1;
        if (i == 3 && end != ipv4.size()) {
            return "";
        }
    }
    const int a = part[0], b = part[1];
    if (a == 127) {
        return "loopback";
    }
    if (a == 10 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168)) {
        return "lan";
    }
    if (a == 100 && b >= 64 && b <= 127) {
        return "tailscale";
    }
    if (a == 26) {
        return "radmin";
    }
    if (a == 169 && b == 254) {
        return "link-local";
    }
    return "public";
}

} // namespace App::Invite
