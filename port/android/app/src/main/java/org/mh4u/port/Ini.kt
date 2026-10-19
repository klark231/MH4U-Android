package org.mh4u.port

import java.io.File

/**
 * A small INI file kept in the PC port's format, so a settings file can be moved between the PC
 * app and this one. `mh4u-app.ini` has sections ([Game], [Display], ...); `mh4u.ini` (the game's
 * options, read by the port's hooks) has bare `key = value` lines. Writing changes the line of a
 * key in place and keeps comments and everything else.
 */
class Ini(private val file: File) {
    fun read(section: String, key: String): String? {
        if (!file.exists()) return null
        var current = ""
        for (raw in file.readLines()) {
            val line = raw.trim()
            if (line.startsWith("[") && line.endsWith("]")) { current = line.substring(1, line.length - 1); continue }
            if (line.isEmpty() || line.startsWith("#") || line.startsWith(";")) continue
            val eq = line.indexOf('=')
            if (eq > 0 && current.equals(section, true) && line.substring(0, eq).trim().equals(key, true)) {
                return line.substring(eq + 1).substringBefore(" #").trim()
            }
        }
        return null
    }

    fun write(section: String, key: String, value: String) {
        val lines = if (file.exists()) file.readLines().toMutableList() else mutableListOf()
        var current = ""
        var sectionEnd = -1  // after the last line of the wanted section
        var found = false
        for ((i, raw) in lines.withIndex()) {
            val line = raw.trim()
            if (line.startsWith("[") && line.endsWith("]")) {
                if (current.equals(section, true) && sectionEnd < 0) sectionEnd = i
                current = line.substring(1, line.length - 1); continue
            }
            val eq = line.indexOf('=')
            if (eq > 0 && !line.startsWith("#") && !line.startsWith(";") &&
                current.equals(section, true) && line.substring(0, eq).trim().equals(key, true)) {
                lines[i] = "$key = $value"; found = true; break
            }
        }
        if (!found) {
            if (section.isEmpty()) {
                lines.add("$key = $value")
            } else if (sectionEnd >= 0 || current.equals(section, true)) {
                lines.add(if (sectionEnd >= 0) sectionEnd else lines.size, "$key = $value")
            } else {
                if (lines.isNotEmpty() && lines.last().isNotBlank()) lines.add("")
                lines.add("[$section]"); lines.add("$key = $value")
            }
        }
        file.parentFile?.mkdirs()
        file.writeText(lines.joinToString("\n") + "\n")
    }
}
