package org.mh4u.port.overlay

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import org.mh4u.port.MH4UInput
import org.mh4u.port.Native
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * The controls drawn over the game, and the touches that reach them. Every finger is one of:
 * a button or combo, the circle pad or the C-stick, or the 3DS touch screen (a finger inside
 * the bottom screen's rectangle that is not on a control is the stylus). In edit mode a finger
 * drags a control and two fingers resize the selected one.
 */
class OverlayView(context: Context, var layout: OverlayLayout) : View(context) {

    var editing = false
        set(v) { field = v; if (v) releaseEverything(); invalidate() }
    var selected: Control? = null
        set(v) { if (field === v) return; field = v; onSelect?.invoke(v); invalidate() }
    var onSelect: ((Control?) -> Unit)? = null
    var onMenu: (() -> Unit)? = null
    /** The controls on screen; off, the view still passes touches to the 3DS touch screen. */
    var controlsVisible = true
        set(v) { field = v; if (!v) releaseEverything(); invalidate() }
    /** Hidden while the game's own menu or a dialog covers it. */
    var shown = true
        set(v) { field = v; if (!v) releaseEverything(); invalidate() }

    private sealed interface Target {
        data class Ctl(val c: Control) : Target
        data object Screen : Target
        data class Drag(val c: Control, val dx: Float, val dy: Float) : Target
    }

    private val targets = HashMap<Int, Target>()
    private val sticks = HashMap<Control, FloatArray>()      // circle pad, c-stick: x, y
    private val held = HashMap<Control, Int>()               // pointer count on a button
    private val latched = HashSet<Control>()
    private var screens = FloatArray(10)

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 3f }
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { textAlign = Paint.Align.CENTER; color = AMBER_TEXT }

    private val pinch = ScaleGestureDetector(context, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScale(d: ScaleGestureDetector): Boolean {
            selected?.let { it.size = (it.size * d.scaleFactor).coerceIn(0.04f, 0.5f); invalidate() }
            return true
        }
    })

    // ── geometry ──

    private fun cx(c: Control) = c.x * width
    private fun cy(c: Control) = c.y * height
    private fun radius(c: Control) = c.size * height / 2f

    private fun hit(c: Control, x: Float, y: Float): Boolean {
        val r = radius(c) * if (c.kind == Control.Kind.CIRCLE_PAD || c.kind == Control.Kind.C_STICK) 1.15f else 1.0f
        return hypot(x - cx(c), y - cy(c)) <= r + 8f
    }

    private fun onScreen(x: Float, y: Float): Boolean {
        val l = screens   // [4..7] the bottom screen
        return l[6] > l[4] && x >= l[4] && x <= l[6] && y >= l[5] && y <= l[7]
    }

    // ── touches ──

    override fun onTouchEvent(e: MotionEvent): Boolean {
        if (!shown) return false
        if (editing) return editTouch(e)
        screens = Native.layout()
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> down(e, e.actionIndex)
            MotionEvent.ACTION_MOVE -> for (i in 0 until e.pointerCount) move(e, i)
            MotionEvent.ACTION_UP, MotionEvent.ACTION_POINTER_UP -> up(e, e.actionIndex)
            MotionEvent.ACTION_CANCEL -> releaseEverything()
        }
        publish()
        invalidate()
        return true
    }

    private fun down(e: MotionEvent, i: Int) {
        val id = e.getPointerId(i)
        val x = e.getX(i); val y = e.getY(i)
        val c = if (!controlsVisible) null else layout.controls.lastOrNull { it.visible && hit(it, x, y) }
        if (c != null) {
            targets[id] = Target.Ctl(c)
            when (c.kind) {
                Control.Kind.MENU -> onMenu?.invoke()
                Control.Kind.CIRCLE_PAD, Control.Kind.C_STICK -> { sticks[c] = floatArrayOf(0f, 0f); moveStick(c, x, y) }
                Control.Kind.DPAD -> held[c] = dpadMask(c, x, y)
                Control.Kind.COMBO -> {
                    if (c.latch) { if (!latched.remove(c)) latched.add(c) } else held[c] = (held[c] ?: 0) + 1
                    buzz()
                }
                else -> { held[c] = (held[c] ?: 0) + 1; buzz() }
            }
        } else if (onScreen(x, y)) {
            targets[id] = Target.Screen
            Native.touch(x.toInt(), y.toInt(), true)
        }
    }

    private fun move(e: MotionEvent, i: Int) {
        val x = e.getX(i); val y = e.getY(i)
        when (val t = targets[e.getPointerId(i)]) {
            is Target.Ctl -> when (t.c.kind) {
                Control.Kind.CIRCLE_PAD, Control.Kind.C_STICK -> moveStick(t.c, x, y)
                Control.Kind.DPAD -> held[t.c] = dpadMask(t.c, x, y)
                else -> {}
            }
            Target.Screen -> Native.touchMove(x.toInt(), y.toInt())
            else -> {}
        }
    }

    private fun up(e: MotionEvent, i: Int) {
        when (val t = targets.remove(e.getPointerId(i))) {
            is Target.Ctl -> when (t.c.kind) {
                Control.Kind.CIRCLE_PAD, Control.Kind.C_STICK -> sticks.remove(t.c)
                Control.Kind.DPAD -> held.remove(t.c)
                Control.Kind.COMBO -> if (!t.c.latch) dec(t.c)
                else -> dec(t.c)
            }
            Target.Screen -> Native.touch(0, 0, false)
            else -> {}
        }
    }

    private fun dec(c: Control) {
        val n = (held[c] ?: 0) - 1
        if (n <= 0) held.remove(c) else held[c] = n
    }

    private fun moveStick(c: Control, x: Float, y: Float) {
        val r = radius(c)
        var dx = (x - cx(c)) / r
        var dy = (y - cy(c)) / r
        val len = hypot(dx, dy)
        if (len > 1f) { dx /= len; dy /= len }
        sticks[c] = floatArrayOf(dx, -dy)   // up positive
    }

    /** The d-pad's directions under a finger: 8-way with a dead centre. */
    private fun dpadMask(c: Control, x: Float, y: Float): Int {
        val r = radius(c)
        val dx = (x - cx(c)) / r; val dy = (y - cy(c)) / r
        var m = 0
        if (hypot(dx, dy) < 0.25f) return 0
        if (dx > 0.38f) m = m or MH4UInput.RIGHT
        if (dx < -0.38f) m = m or MH4UInput.LEFT
        if (dy > 0.38f) m = m or MH4UInput.DOWN
        if (dy < -0.38f) m = m or MH4UInput.UP
        return m
    }

    private fun buzz() {
        if (layout.haptics) performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
    }

    /** What the controls hold, handed to the game. */
    private fun publish() {
        var mask = 0
        for ((c, n) in held) {
            mask = mask or when (c.kind) { Control.Kind.DPAD -> n; else -> c.mask }
        }
        for (c in latched) mask = mask or c.mask
        val circle = layout.controls.firstOrNull { it.kind == Control.Kind.CIRCLE_PAD }?.let { sticks[it] } ?: ZERO
        val cstick = layout.controls.firstOrNull { it.kind == Control.Kind.C_STICK }?.let { sticks[it] } ?: ZERO
        MH4UInput.setOverlay(mask, circle[0], circle[1], cstick[0], cstick[1])
    }

    fun releaseEverything() {
        if (targets.values.any { it === Target.Screen }) Native.touch(0, 0, false)
        targets.clear(); sticks.clear(); held.clear(); latched.clear()
        MH4UInput.setOverlay(0, 0f, 0f, 0f, 0f)
        invalidate()
    }

    // ── editing ──

    private fun editTouch(e: MotionEvent): Boolean {
        pinch.onTouchEvent(e)
        if (pinch.isInProgress) return true
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                val c = layout.controls.lastOrNull { hit(it, e.x, e.y) }
                selected = c
                if (c != null) targets[e.getPointerId(0)] = Target.Drag(c, cx(c) - e.x, cy(c) - e.y)
            }
            MotionEvent.ACTION_MOVE -> {
                val t = targets[e.getPointerId(0)]
                if (t is Target.Drag && e.pointerCount == 1) {
                    t.c.x = ((e.x + t.dx) / width).coerceIn(0.02f, 0.98f)
                    t.c.y = ((e.y + t.dy) / height).coerceIn(0.02f, 0.98f)
                    invalidate()
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> targets.clear()
        }
        return true
    }

    // ── drawing ──

    override fun onDraw(canvas: Canvas) {
        if (!shown || (!controlsVisible && !editing)) return
        val a = (layout.opacity * 255).toInt().coerceIn(20, 255)
        for (c in layout.controls) {
            if (!c.visible && !editing) continue
            val r = radius(c)
            val pressed = (held[c] ?: 0) > 0 || c in latched || sticks.containsKey(c)
            fill.color = if (pressed) AMBER else FRAME
            fill.alpha = if (pressed) min(255, a + 60) else (a * 0.55f).toInt()
            stroke.color = if (c === selected && editing) AMBER_BRIGHT else AMBER
            stroke.alpha = a
            stroke.strokeWidth = if (c === selected && editing) 6f else 3f
            when (c.kind) {
                Control.Kind.CIRCLE_PAD, Control.Kind.C_STICK -> {
                    canvas.drawCircle(cx(c), cy(c), r, fill); canvas.drawCircle(cx(c), cy(c), r, stroke)
                    val s = sticks[c]
                    val kx = cx(c) + (s?.get(0) ?: 0f) * r * 0.6f
                    val ky = cy(c) - (s?.get(1) ?: 0f) * r * 0.6f
                    fill.color = AMBER; fill.alpha = a
                    canvas.drawCircle(kx, ky, r * 0.38f, fill)
                    label(canvas, if (c.kind == Control.Kind.C_STICK) "C" else "", cx(c), cy(c) - r - 6f, r * 0.25f, a)
                }
                Control.Kind.DPAD -> drawDpad(canvas, c, r, a)
                Control.Kind.MENU, Control.Kind.BUTTON, Control.Kind.COMBO -> {
                    if (c.kind == Control.Kind.BUTTON && c.label.length > 2) {
                        val rect = RectF(cx(c) - r * 1.3f, cy(c) - r * 0.6f, cx(c) + r * 1.3f, cy(c) + r * 0.6f)
                        canvas.drawRoundRect(rect, r, r, fill); canvas.drawRoundRect(rect, r, r, stroke)
                    } else {
                        canvas.drawCircle(cx(c), cy(c), r, fill); canvas.drawCircle(cx(c), cy(c), r, stroke)
                    }
                    label(canvas, c.label, cx(c), cy(c), r * if (c.label.length > 2) 0.55f else 0.8f, a)
                }
            }
        }
    }

    private fun drawDpad(canvas: Canvas, c: Control, r: Float, a: Int) {
        val arm = r * 0.38f
        val x = cx(c); val y = cy(c)
        val m = held[c] ?: 0
        val arms = listOf(
            RectF(x - arm, y - r, x + arm, y - arm * 0.2f) to MH4UInput.UP,
            RectF(x - arm, y + arm * 0.2f, x + arm, y + r) to MH4UInput.DOWN,
            RectF(x - r, y - arm, x - arm * 0.2f, y + arm) to MH4UInput.LEFT,
            RectF(x + arm * 0.2f, y - arm, x + r, y + arm) to MH4UInput.RIGHT,
        )
        for ((rect, bit) in arms) {
            fill.color = if (m and bit != 0) AMBER else FRAME
            fill.alpha = if (m and bit != 0) min(255, a + 60) else (a * 0.55f).toInt()
            canvas.drawRoundRect(rect, 10f, 10f, fill); canvas.drawRoundRect(rect, 10f, 10f, stroke)
        }
    }

    private fun label(canvas: Canvas, s: String, x: Float, y: Float, size: Float, a: Int) {
        if (s.isEmpty()) return
        text.textSize = max(14f, size); text.alpha = a
        canvas.drawText(s, x, y - (text.ascent() + text.descent()) / 2f, text)
    }

    companion object {
        private const val AMBER = 0xFFEBA133.toInt()
        private const val AMBER_BRIGHT = 0xFFFFC25C.toInt()
        private const val AMBER_TEXT = 0xFFEDEBE3.toInt()
        private const val FRAME = 0xFF29292B.toInt()
        private val ZERO = floatArrayOf(0f, 0f)
    }
}
