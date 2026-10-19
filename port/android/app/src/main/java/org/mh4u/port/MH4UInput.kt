package org.mh4u.port

import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import java.io.File

/**
 * Gamepads, keyboards and mice (any device Android lists: Bluetooth, USB, built in) with combos
 * and per-device bindings. Events go to the native engine (src/input_map.cpp), which turns them
 * into 3DS buttons and sticks and hands them to the game together with the on-screen controls.
 *
 * A binding is "all of these held -> all of these 3DS buttons pressed": one key can press
 * several buttons (a combo), several keys can press one (a chord). A source can name one device
 * (its descriptor) or any.
 */
object MH4UInput {
    init { System.loadLibrary("mh4u-android") }

    // 3DS button bits, as Port::InputMap::Button
    const val A = 1 shl 0; const val B = 1 shl 1; const val X = 1 shl 2; const val Y = 1 shl 3
    const val L = 1 shl 4; const val R = 1 shl 5; const val ZL = 1 shl 6; const val ZR = 1 shl 7
    const val START = 1 shl 8; const val SELECT = 1 shl 9
    const val UP = 1 shl 10; const val DOWN = 1 shl 11; const val LEFT = 1 shl 12; const val RIGHT = 1 shl 13
    const val HOME = 1 shl 14

    /** Buttons in the order the screens list them. */
    val buttons = listOf(
        "A" to A, "B" to B, "X" to X, "Y" to Y, "L" to L, "R" to R, "ZL" to ZL, "ZR" to ZR,
        "START" to START, "SELECT" to SELECT, "D-pad up" to UP, "D-pad down" to DOWN,
        "D-pad left" to LEFT, "D-pad right" to RIGHT,
    )

    private var file: File? = null

    fun init(dir: File) {
        file = File(dir, "mh4u-input.ini")
        val text = file?.takeIf { it.exists() }?.readText()
        if (text == null || !nativeLoad(text)) nativeLoadDefaults()
    }

    fun save() { file?.writeText(nativeSave()) }

    fun releaseAll() = nativeReleaseAll()

    // ── events from the activity ──

    private fun id(d: InputDevice?) = d?.descriptor ?: ""

    /** From dispatchKeyEvent; true when the event was taken. */
    fun onKey(e: KeyEvent): Boolean {
        val src = e.source
        val pad = src and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD ||
            src and InputDevice.SOURCE_DPAD == InputDevice.SOURCE_DPAD ||
            src and InputDevice.SOURCE_KEYBOARD == InputDevice.SOURCE_KEYBOARD
        if (!pad) return false
        // the system's own Back (not a pad's B) and volume keys stay the system's
        if (e.keyCode == KeyEvent.KEYCODE_BACK && src and InputDevice.SOURCE_GAMEPAD == 0) return false
        if (e.keyCode == KeyEvent.KEYCODE_VOLUME_UP || e.keyCode == KeyEvent.KEYCODE_VOLUME_DOWN) return false
        if (e.repeatCount > 0) return true
        nativeOnKey(id(e.device), e.keyCode, e.action == KeyEvent.ACTION_DOWN)
        return true
    }

    private val axes = intArrayOf(
        MotionEvent.AXIS_X, MotionEvent.AXIS_Y, MotionEvent.AXIS_Z, MotionEvent.AXIS_RZ,
        MotionEvent.AXIS_RX, MotionEvent.AXIS_RY, MotionEvent.AXIS_HAT_X, MotionEvent.AXIS_HAT_Y,
        MotionEvent.AXIS_LTRIGGER, MotionEvent.AXIS_RTRIGGER, MotionEvent.AXIS_BRAKE, MotionEvent.AXIS_GAS,
    )

    /** From dispatchGenericMotionEvent. */
    fun onMotion(e: MotionEvent): Boolean {
        if (e.source and InputDevice.SOURCE_JOYSTICK != InputDevice.SOURCE_JOYSTICK ||
            e.action != MotionEvent.ACTION_MOVE) return false
        val dev = id(e.device)
        for (a in axes) nativeOnAxis(dev, a, e.getAxisValue(a))
        return true
    }

    /** An InputDeviceListener's onInputDeviceRemoved. */
    fun deviceRemoved(descriptor: String) = nativeOnDeviceRemoved(descriptor)

    // ── on-screen controls ──

    /** What the on-screen controls hold: 3DS buttons (a combo button holds several) and the sticks. */
    fun setOverlay(buttons: Int, circleX: Float, circleY: Float, cX: Float, cY: Float) =
        nativeSetOverlay(buttons, circleX, circleY, cX, cY)

    // ── binding ──

    /** The next keys held together and released become one chord: see [learned]. */
    fun startLearn() = nativeStartLearn()
    fun cancelLearn() = nativeCancelLearn()
    fun learning() = nativeLearning()
    /** The chord learned, as text, or null while nothing is complete. */
    fun learned(): String? = nativeTakeLearned()

    /** Rebinds [mask] (one button, or several for a combo) to [chord]. */
    fun bind(mask: Int, chord: String): Boolean {
        nativeRemoveFor(mask)
        return nativeAddBinding(mask, chord).also { if (it) save() }
    }

    /** A further source for a button or combo (a pad button and a key, say). */
    fun addAlternative(mask: Int, chord: String): Boolean =
        nativeAddBinding(mask, chord).also { if (it) save() }

    fun unbind(mask: Int) { nativeRemoveFor(mask); save() }

    fun resetToDefaults() { nativeLoad(""); nativeLoadDefaults(); save() }

    /** One line per binding of the engine: which buttons, and what presses them. */
    data class Binding(val mask: Int, val chord: String, val text: String)

    fun bindings(): List<Binding> =
        nativeSave().lineSequence().mapNotNull { line ->
            val l = line.trim()
            if (!l.startsWith("bind")) return@mapNotNull null
            val body = l.substringAfter("=").trim()
            val names = body.substringBefore("<=").trim()
            val chord = body.substringAfter("<=").trim()
            var mask = 0
            for (n in names.split(",")) mask = mask or buttonFromName(n.trim())
            Binding(mask, chord, describe(chord))
        }.toList()

    private fun buttonFromName(n: String): Int = when (n.uppercase()) {
        "A" -> A; "B" -> B; "X" -> X; "Y" -> Y; "L" -> L; "R" -> R; "ZL" -> ZL; "ZR" -> ZR
        "START" -> START; "SELECT" -> SELECT; "UP" -> UP; "DOWN" -> DOWN; "LEFT" -> LEFT
        "RIGHT" -> RIGHT; "HOME" -> HOME; else -> 0
    }

    fun maskName(mask: Int): String {
        val names = buttons.filter { mask and it.second != 0 }.map { it.first }
        return if (names.isEmpty()) "?" else names.joinToString(" + ")
    }

    /** "key:96@desc + axis+:18" as "A button + Right trigger (Xbox controller)". */
    fun describe(chord: String): String = chord.split("+").let { _ ->
        chord.split(" + ").joinToString(" + ") { part ->
            val dev = part.substringAfter("@", "")
            val src = part.substringBefore("@").trim()
            val what = when {
                src.startsWith("key:") -> KeyEvent.keyCodeToString(src.substring(4).toIntOrNull() ?: 0).removePrefix("KEYCODE_")
                    .replace('_', ' ').lowercase().replaceFirstChar { it.uppercase() }
                src.startsWith("axis+:") -> MotionEvent.axisToString(src.substring(6).toIntOrNull() ?: 0).removePrefix("AXIS_").lowercase() + " +"
                src.startsWith("axis-:") -> MotionEvent.axisToString(src.substring(6).toIntOrNull() ?: 0).removePrefix("AXIS_").lowercase() + " -"
                else -> src
            }
            if (dev.isEmpty()) what else "$what (${deviceName(dev)})"
        }
    }

    private fun deviceName(descriptor: String): String =
        InputDevice.getDeviceIds().asSequence().mapNotNull { InputDevice.getDevice(it) }
            .firstOrNull { it.descriptor == descriptor }?.name ?: "disconnected device"

    /** Devices that can press 3DS buttons right now. */
    fun devices(): List<String> =
        InputDevice.getDeviceIds().asSequence().mapNotNull { InputDevice.getDevice(it) }
            .filter { it.sources and (InputDevice.SOURCE_GAMEPAD or InputDevice.SOURCE_JOYSTICK or InputDevice.SOURCE_KEYBOARD) != 0 && !it.isVirtual }
            .map { it.name }.toList()

    @JvmStatic private external fun nativeLoadDefaults()
    @JvmStatic private external fun nativeOnKey(device: String, code: Int, down: Boolean)
    @JvmStatic private external fun nativeOnAxis(device: String, axis: Int, value: Float)
    @JvmStatic private external fun nativeOnDeviceRemoved(device: String)
    @JvmStatic private external fun nativeReleaseAll()
    @JvmStatic private external fun nativeSetOverlay(buttons: Int, cx: Float, cy: Float, sx: Float, sy: Float)
    @JvmStatic private external fun nativeStartLearn()
    @JvmStatic private external fun nativeCancelLearn()
    @JvmStatic private external fun nativeLearning(): Boolean
    @JvmStatic private external fun nativeTakeLearned(): String?
    @JvmStatic private external fun nativeAddBinding(mask: Int, chord: String): Boolean
    @JvmStatic private external fun nativeRemoveFor(mask: Int)
    @JvmStatic private external fun nativeLoad(text: String): Boolean
    @JvmStatic private external fun nativeSave(): String
}
