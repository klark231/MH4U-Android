package org.mh4u.port.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.mh4u.port.Native
import org.mh4u.port.Prefs

/**
 * The pages of the PC app's menu (Game, Display, Audio), each setting written to its ini the
 * moment it changes. Used by the launcher's Settings and by the in-game menu, which applies
 * them to the game that runs.
 */

private val PAD_BUTTONS = listOf("A", "B", "X", "Y", "L", "R", "ZL", "ZR", "START", "UP", "DOWN", "LEFT", "RIGHT").map { it to it }

@Composable
private fun Page(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), content = content)
}

/** A game option (mh4u.ini). */
@Composable
private fun GameChoice(title: String, key: String, default: String, options: List<Pair<String, String>>, hint: String? = null) {
    var v by remember { mutableStateOf(Prefs.getGame(key, default)) }
    ChoiceRow(title, hint, options, v) { v = it; Prefs.setGame(key, it) }
}

@Composable
private fun GameToggle(title: String, key: String, default: Boolean, hint: String? = null, on: String = "on", off: String = "off") {
    var v by remember { mutableStateOf(Prefs.getGame(key, if (default) on else off) == on) }
    ToggleRow(title, hint, v) { v = it; Prefs.setGame(key, if (it) on else off) }
}

private val ON_OFF = listOf("On" to "on", "Off" to "off")

@Composable
fun GamePage() = Page {
    SectionTitle("Speed")
    Panel {
        var fps by remember { mutableStateOf(Prefs.getGame("fps", "60").toFloatOrNull() ?: 60f) }
        SliderRow("Frame rate cap", "The game runs at 30 on a 3DS; its speed stays right at any rate. Above 60 a busy scene may need a higher CPU clock.",
            fps, 30f..120f, " fps", steps = 17) { fps = it; Prefs.setGame("fps", it.toInt().toString()) }
        RowDivider()
        var clock by remember { mutableStateOf(Prefs.getInt("Game", "cpu_clock", 100).toFloat()) }
        SliderRow("CPU clock", "Applies the next time the game starts.", clock, 50f..400f, " %", steps = 6) {
            clock = it; Prefs.setApp("Game", "cpu_clock", it.toInt().toString())
        }
        RowDivider()
        GameToggle("Online sync (net_sync)", "net_sync", true, "Keeps players of a room in step.")
    }

    SectionTitle("Picture")
    Panel {
        GameChoice("Top screen shape", "widescreen", "16:9",
            listOf("16:9", "16:10", "21:9", "32:9", "4:3", "Off").map { it to it.lowercase() })
        RowDivider()
        GameToggle("Draw the wide picture itself", "wide_render", true, "Off stretches a 400×240 one.", "1", "0")
        RowDivider()
        GameToggle("HUD to the screen's edges", "hud_edges", true, "Moves health, stamina and the party list out to the sides.", "1", "0")
        RowDivider()
        GameToggle("Dither", "dither", false, "Off for a clean picture above 1× resolution.")
        RowDivider()
        GameChoice("Stereoscopic 3D", "stereo_3d", "off", listOf("Off" to "off", "On" to "on"))
        RowDivider()
        GameToggle("Antialiasing", "antialiasing", true)
        RowDivider()
        GameToggle("Bloom", "bloom", false)
        RowDivider()
        GameToggle("Depth of field", "depth_of_field", false)
        RowDivider()
        GameToggle("Motion blur", "motion_blur", true)
        RowDivider()
        GameToggle("Radial blur", "radial_blur", true)
        RowDivider()
        GameToggle("Haze", "haze", true)
    }

    SectionTitle("Touch screen over the game")
    Panel { TouchOverlayEditor() }

    SectionTitle("Quest buttons")
    Panel {
        GameChoice("Kick", "kick", "ZR", PAD_BUTTONS, "The touch screen's kick panel, on a button.")
        RowDivider()
        GameChoice("Target camera", "target", "ZL", PAD_BUTTONS)
        RowDivider()
        GameChoice("Skip", "skip", "ZR", PAD_BUTTONS, "Skips the scene of the monster's end.")
        RowDivider()
        GameChoice("End the wait (hold)", "skip_end", "ZL+ZR",
            listOf("ZL + ZR" to "ZL+ZR", "L + R" to "L+R", "START + SELECT" to "START+SELECT"),
            "Held together to end the carving wait, as if it ran out.")
        RowDivider()
        var hold by remember { mutableStateOf(Prefs.getGame("skip_end_hold", "1.0").toFloatOrNull() ?: 1f) }
        SliderRow("Hold time", null, hold, 0.5f..3f, " s", steps = 4) { hold = it; Prefs.setGame("skip_end_hold", "%.1f".format(it)) }
        RowDivider()
        GameChoice("Skip hint on screen", "skip_hint", "on", listOf("On" to "on", "Flash" to "flash", "Off" to "off"))
    }
    Spacer(Modifier.height(24.dp))
}

/** touch_overlay = "<corner> <height %> <opacity %>", quest_overlay and target_overlay. */
@Composable
private fun TouchOverlayEditor() {
    val parts = Prefs.getGame("touch_overlay", "top-right 30 85").split(" ").filter { it.isNotEmpty() }
    var corner by remember { mutableStateOf(parts.getOrNull(0)?.takeIf { it != "on" } ?: if (parts.getOrNull(0) == "off") "off" else "top-right") }
    var size by remember { mutableStateOf(parts.getOrNull(1)?.toFloatOrNull() ?: 30f) }
    var opacity by remember { mutableStateOf(parts.getOrNull(2)?.toFloatOrNull() ?: 85f) }
    fun save() = Prefs.setGame("touch_overlay", if (corner == "off") "off" else "$corner ${size.toInt()} ${opacity.toInt()}")

    ChoiceRow("Corner", "Used by the Overlay screen layout: the game fills the screen and the touch screen sits in a corner.",
        listOf("Top left" to "top-left", "Top right" to "top-right", "Bottom left" to "bottom-left",
            "Bottom right" to "bottom-right", "Off" to "off"), corner) { corner = it; save() }
    SliderRow("Height", null, size, 10f..70f, " %") { size = it; save() }
    SliderRow("Opacity", null, opacity, 10f..100f, " %") { opacity = it; save() }
    RowDivider()

    // the quest's map and the target camera panel: "map 40 at 100 0", "on 12 at 100 30" or "off"
    PanelEditor("Map in quests", "quest_overlay", "map", "map", "Shows only the map while a quest's panels are up.")
    RowDivider()
    PanelEditor("Target camera panel", "target_overlay", "on", "on", "A small view of the target camera, under the map.")
}

@Composable
private fun PanelEditor(title: String, key: String, default: String, onWord: String, hint: String) {
    val parts = Prefs.getGame(key, default).split(" ").filter { it.isNotEmpty() }
    var on by remember { mutableStateOf(parts.firstOrNull() == onWord) }
    var height by remember { mutableStateOf(parts.getOrNull(1)?.toFloatOrNull() ?: 0f) }
    val at = parts.indexOf("at")
    var x by remember { mutableStateOf(if (at >= 0) parts.getOrNull(at + 1)?.toFloatOrNull() ?: 100f else 100f) }
    var y by remember { mutableStateOf(if (at >= 0) parts.getOrNull(at + 2)?.toFloatOrNull() ?: 0f else 0f) }
    var placed by remember { mutableStateOf(at >= 0) }
    fun save() {
        val text = if (!on) "off" else buildString {
            append(onWord)
            if (height > 0f) append(" ").append(height.toInt())
            if (placed) append(" at ").append(x.toInt()).append(" ").append(y.toInt())
        }
        Prefs.setGame(key, text)
    }
    ToggleRow(title, hint, on) { on = it; save() }
    if (on) {
        SliderRow("Height (0: as the touch screen)", null, height, 0f..70f, " %") { height = it; save() }
        ToggleRow("Place it myself", "Across and down in % of the room the window leaves.", placed) { placed = it; save() }
        if (placed) {
            SliderRow("Across", null, x, 0f..100f, " %") { x = it; save() }
            SliderRow("Down", null, y, 0f..100f, " %") { y = it; save() }
        }
    }
}

@Composable
fun DisplayPage() = Page {
    SectionTitle("Screens")
    Panel {
        var screens by remember { mutableStateOf(Prefs.getApp("Display", "screens", "overlay")) }
        ChoiceRow("Layout", "Overlay: the game fills the screen, the touch screen in a corner (Game > Touch screen over the game).",
            listOf("Overlay" to "overlay", "Stacked" to "stacked", "Single screen" to "single",
                "Side by side" to "side", "Large + small" to "large", "Hybrid" to "hybrid"), screens) {
            screens = it; Prefs.setApp("Display", "screens", it); Prefs.apply()
        }
        RowDivider()
        var portrait by remember { mutableStateOf(Prefs.getApp("Android", "portrait", "top")) }
        ChoiceRow("In portrait", null, listOf("Top screen full width" to "top", "Original" to "original", "Custom" to "custom"), portrait) {
            portrait = it; Prefs.setApp("Android", "portrait", it); Prefs.apply()
        }
        RowDivider()
        var swap by remember { mutableStateOf(Prefs.getBool("Android", "swap", false)) }
        ToggleRow("Swap screens", null, swap) { swap = it; Prefs.setBool("Android", "swap", it); Prefs.apply() }
        var upright by remember { mutableStateOf(Prefs.getBool("Android", "upright", false)) }
        ToggleRow("Upright screens", "Turns the screens a quarter turn.", upright) { upright = it; Prefs.setBool("Android", "upright", it); Prefs.apply() }
        var gap by remember { mutableStateOf(Prefs.getInt("Android", "gap", 0).toFloat()) }
        SliderRow("Gap between screens", null, gap, 0f..80f) { gap = it; Prefs.setApp("Android", "gap", it.toInt().toString()); Prefs.apply() }
        var large by remember { mutableStateOf(Prefs.getApp("Android", "large_proportion", "4").toFloatOrNull() ?: 4f) }
        SliderRow("Large screen's share", "For Large + small and Hybrid.", large, 1f..8f, " ×", steps = 13) {
            large = it; Prefs.setApp("Android", "large_proportion", "%.1f".format(it)); Prefs.apply()
        }
    }

    SectionTitle("Picture")
    Panel {
        var res by remember { mutableStateOf(Prefs.getInt("Display", "resolution", 2).toFloat()) }
        SliderRow("Internal resolution", "In multiples of the 3DS's. 0: as many as the screen needs.", res, 0f..6f, " ×", steps = 5) {
            res = it; Prefs.setApp("Display", "resolution", it.toInt().toString()); Prefs.apply()
        }
        RowDivider()
        var filter by remember { mutableStateOf(Prefs.getBool("Display", "linear_filter", true)) }
        ToggleRow("Smooth scaling", null, filter) { filter = it; Prefs.setBool("Display", "linear_filter", it); Prefs.apply() }
        var vsync by remember { mutableStateOf(Prefs.getBool("Display", "vsync", true)) }
        ToggleRow("VSync", null, vsync) { vsync = it; Prefs.setBool("Display", "vsync", it); Prefs.apply() }
        var accMul by remember { mutableStateOf(Prefs.getBool("Display", "accurate_mul", false)) }
        ToggleRow("Accurate shader math", "Off is faster on most phones; applies the next time the game starts.", accMul) {
            accMul = it; Prefs.setBool("Display", "accurate_mul", it)
        }
        var tex by remember { mutableStateOf(Prefs.getBool("Display", "custom_textures", false)) }
        ToggleRow("Custom textures", "Loads replacement textures from the user folder; applies the next time the game starts.", tex) {
            tex = it; Prefs.setBool("Display", "custom_textures", it)
        }
        var fpsCounter by remember { mutableStateOf(Prefs.getApp("Display", "fps_counter", "off")) }
        ChoiceRow("Frame counter", null, ON_OFF, fpsCounter) { fpsCounter = it; Prefs.setApp("Display", "fps_counter", it) }
    }

    SectionTitle("Advanced graphics")
    Panel {
        var asyncSh by remember { mutableStateOf(Prefs.getBool("Display", "async_shaders", true)) }
        ToggleRow("Compile shaders in the background", "Stops the stutter when a new effect first appears; an effect can be missing for a moment. Applies the next time the game starts.", asyncSh) {
            asyncSh = it; Prefs.setBool("Display", "async_shaders", it)
        }
        RowDivider()
        var cache by remember { mutableStateOf(Prefs.getBool("Display", "disk_shader_cache", true)) }
        ToggleRow("Keep a shader cache on disk", "Makes later launches smoother. Applies the next time the game starts.", cache) {
            cache = it; Prefs.setBool("Display", "disk_shader_cache", it)
        }
        RowDivider()
        var skipDup by remember { mutableStateOf(Prefs.getBool("Display", "skip_duplicate_frames", false)) }
        ToggleRow("Skip repeated frames", "Saves work when the picture hasn't changed.", skipDup) {
            skipDup = it; Prefs.setBool("Display", "skip_duplicate_frames", it)
        }
        RowDivider()
        var texFilter by remember { mutableStateOf(Prefs.getApp("Display", "texture_filter", "none")) }
        ChoiceRow("Texture filter", "Smooths low-resolution textures. The heavier ones cost speed.",
            listOf("None" to "none", "Bicubic" to "bicubic", "ScaleForce" to "scaleforce", "xBRZ" to "xbrz", "MMPX" to "mmpx", "Anime4K" to "anime4k"), texFilter) {
            texFilter = it; Prefs.setApp("Display", "texture_filter", it)
        }
        RowDivider()
        var texSampling by remember { mutableStateOf(Prefs.getApp("Display", "texture_sampling", "game")) }
        ChoiceRow("Texture sampling", null, listOf("Game's choice" to "game", "Nearest" to "nearest", "Linear" to "linear"), texSampling) {
            texSampling = it; Prefs.setApp("Display", "texture_sampling", it)
        }
    }
    Spacer(Modifier.height(24.dp))
}

@Composable
fun AudioPage() = Page {
    SectionTitle("Sound")
    Panel {
        var vol by remember { mutableStateOf(Prefs.getInt("Audio", "volume", 100).toFloat()) }
        SliderRow("Volume", null, vol, 0f..100f, " %") { vol = it; Prefs.setApp("Audio", "volume", it.toInt().toString()); Prefs.apply() }
    }
    Spacer(Modifier.height(24.dp))
}
