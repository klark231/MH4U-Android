#include "ui/options.h"

#include <set>
#include <vector>

namespace App::Ui {

namespace {

std::string Trim(const std::string& s) {
    const auto b = s.find_first_not_of(" \t\r\n");
    const auto e = s.find_last_not_of(" \t\r\n");
    return b == std::string::npos ? "" : s.substr(b, e - b + 1);
}

/// The lines of `text` without their line endings.
std::vector<std::string> Lines(const std::string& text) {
    std::vector<std::string> lines;
    std::size_t start = 0;
    while (start <= text.size()) {
        const auto end = text.find('\n', start);
        std::string line = text.substr(start, end == std::string::npos ? std::string::npos : end - start);
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        lines.push_back(std::move(line));
        if (end == std::string::npos) {
            break;
        }
        start = end + 1;
    }
    return lines;
}

/// The key a line sets, or empty.
std::string KeyOf(const std::string& line) {
    const std::string code = line.substr(0, line.find('#'));
    const auto eq = code.find('=');
    return eq == std::string::npos ? "" : Trim(code.substr(0, eq));
}

} // namespace

std::map<std::string, std::string> ReadOptions(const std::string& text) {
    std::map<std::string, std::string> values;
    for (const std::string& line : Lines(text)) {
        const std::string code = Trim(line.substr(0, line.find('#')));
        const auto eq = code.find('=');
        if (eq != std::string::npos) {
            values[Trim(code.substr(0, eq))] = Trim(code.substr(eq + 1));
        }
    }
    return values;
}

std::string WriteOptions(const std::string& text, const std::map<std::string, std::string>& values) {
    const char* eol = text.find("\r\n") != std::string::npos ? "\r\n" : "\n";
    std::vector<std::string> lines = Lines(text);
    std::set<std::string> written;
    for (std::string& line : lines) {
        const std::string key = KeyOf(line);
        const auto it = key.empty() ? values.end() : values.find(key);
        if (it == values.end()) {
            continue;
        }
        const auto indent = line.substr(0, line.find_first_not_of(" \t"));
        std::string comment;
        if (const auto hash = line.find('#'); hash != std::string::npos) {
            auto from = hash;
            while (from > 0 && (line[from - 1] == ' ' || line[from - 1] == '\t')) {
                --from;
            }
            comment = line.substr(from);
            if (from == hash) {
                comment = " " + comment;
            }
        }
        line = indent + key + " = " + it->second + comment;
        written.insert(key);
    }
    std::string out;
    for (std::size_t i = 0; i < lines.size(); ++i) {
        out += lines[i];
        if (i + 1 < lines.size()) {
            out += eol;
        }
    }
    // the keys the file has no line for yet go at the end, each on a line of its own
    for (const auto& [key, value] : values) {
        if (written.count(key)) {
            continue;
        }
        if (!out.empty() && out.back() != '\n') {
            out += eol;
        }
        out += key + " = " + value + eol;
    }
    return out;
}

} // namespace App::Ui
