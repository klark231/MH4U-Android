package org.mh4u.port.overlay

import org.json.JSONArray
import org.json.JSONObject
import org.mh4u.port.MH4UInput
import org.mh4u.port.Storage
import java.io.File

/**
 * The on-screen controls: what there is, where, how big. Positions are fractions of the
 * screen (the centre of a control), sizes fractions of the screen's height, so a layout
 * follows the window (rotation, split screen). One layout per orientation, saved as JSON.
 *
 * A COMBO presses several 3DS buttons with one touch (the hunter's dash-and-attack, an item
 * and its use...). With [latch] it stays pressed after the touch ends until touched again.
 */
data class Control(
    val id: String,
    val kind: Kind,
    val label: String,
    val mask: Int = 0,
    var x: Float,
    var y: Float,
    var size: Float,
    var latch: Boolean = false,
    var visible: Boolean = true,
) {
    enum class Kind { BUTTON, DPAD, CIRCLE_PAD, C_STICK, COMBO, MENU }

    fun toJson() = JSONObject().put("id", id).put("kind", kind.name).put("label", label).put("mask", mask)
        .put("x", x.toDouble()).put("y", y.toDouble()).put("size", size.toDouble())
        .put("latch", latch).put("visible", visible)

    companion object {
        fun fromJson(o: JSONObject) = Control(
            o.getString("id"), Kind.valueOf(o.getString("kind")), o.getString("label"), o.optInt("mask"),
            o.getDouble("x").toFloat(), o.getDouble("y").toFloat(), o.getDouble("size").toFloat(),
            o.optBoolean("latch"), o.optBoolean("visible", true),
        )
    }
}

class OverlayLayout(val controls: MutableList<Control>, var opacity: Float = 0.6f, var haptics: Boolean = true) {

    fun save(portrait: Boolean) {
        val o = JSONObject().put("opacity", opacity.toDouble()).put("haptics", haptics)
            .put("controls", JSONArray().also { a -> controls.forEach { a.put(it.toJson()) } })
        file(portrait).writeText(o.toString(1))
    }

    fun addCombo(name: String, mask: Int, latch: Boolean): Control {
        val c = Control("combo${System.nanoTime()}", Control.Kind.COMBO, name, mask, 0.5f, 0.5f, 0.11f, latch)
        controls += c
        return c
    }

    companion object {
        private fun file(portrait: Boolean) = File(Storage.root, if (portrait) "overlay-portrait.json" else "overlay.json")

        fun load(portrait: Boolean): OverlayLayout = runCatching {
            val o = JSONObject(file(portrait).readText())
            val list = o.getJSONArray("controls")
            OverlayLayout(MutableList(list.length()) { Control.fromJson(list.getJSONObject(it)) },
                o.optDouble("opacity", 0.6).toFloat(), o.optBoolean("haptics", true))
        }.getOrElse { defaults(portrait) }

        fun defaults(portrait: Boolean): OverlayLayout {
            val b = MH4UInput
            val list = mutableListOf<Control>()
            fun btn(id: String, label: String, mask: Int, x: Float, y: Float, size: Float) {
                list += Control(id, Control.Kind.BUTTON, label, mask, x, y, size)
            }
            if (!portrait) {
                btn("l", "L", b.L, .07f, .07f, .10f); btn("r", "R", b.R, .93f, .07f, .10f)
                btn("zl", "ZL", b.ZL, .07f, .19f, .10f); btn("zr", "ZR", b.ZR, .93f, .19f, .10f)
                list += Control("circle", Control.Kind.CIRCLE_PAD, "", 0, .14f, .70f, .34f)
                list += Control("dpad", Control.Kind.DPAD, "", 0, .30f, .88f, .22f)
                list += Control("cstick", Control.Kind.C_STICK, "", 0, .66f, .84f, .24f)
                btn("x", "X", b.X, .90f, .54f, .12f); btn("y", "Y", b.Y, .82f, .66f, .12f)
                btn("a", "A", b.A, .94f, .66f, .12f); btn("b", "B", b.B, .88f, .78f, .12f)
                btn("start", "START", b.START, .56f, .94f, .07f); btn("select", "SELECT", b.SELECT, .44f, .94f, .07f)
                list += Control("menu", Control.Kind.MENU, "☰", 0, .50f, .06f, .07f)
            } else {
                btn("l", "L", b.L, .10f, .46f, .07f); btn("r", "R", b.R, .90f, .46f, .07f)
                btn("zl", "ZL", b.ZL, .10f, .55f, .07f); btn("zr", "ZR", b.ZR, .90f, .55f, .07f)
                list += Control("circle", Control.Kind.CIRCLE_PAD, "", 0, .20f, .76f, .20f)
                list += Control("dpad", Control.Kind.DPAD, "", 0, .20f, .92f, .13f)
                list += Control("cstick", Control.Kind.C_STICK, "", 0, .50f, .90f, .15f)
                btn("x", "X", b.X, .85f, .66f, .08f); btn("y", "Y", b.Y, .75f, .73f, .08f)
                btn("a", "A", b.A, .93f, .73f, .08f); btn("b", "B", b.B, .85f, .80f, .08f)
                btn("start", "START", b.START, .62f, .98f, .05f); btn("select", "SELECT", b.SELECT, .38f, .98f, .05f)
                list += Control("menu", Control.Kind.MENU, "☰", 0, .50f, .44f, .05f)
            }
            return OverlayLayout(list)
        }
    }
}
