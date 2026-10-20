package org.mh4u.port

import android.os.Bundle
import android.view.*
import android.widget.FrameLayout
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import kotlinx.coroutines.delay
import org.mh4u.port.overlay.Control
import org.mh4u.port.overlay.OverlayLayout
import org.mh4u.port.overlay.OverlayView
import org.mh4u.port.ui.*

/**
 * The game: a surface the core draws on, the on-screen controls over it, and the in-game menu
 * (the PC app's Esc menu: Game, Display, Audio, Controls, Multiplayer). The menu mutes the
 * game's input rather than pausing it, as on PC, so a room keeps running.
 */
class EmulationActivity : ComponentActivity(), SurfaceHolder.Callback {

    private lateinit var overlay: OverlayView
    private var gameThread: Thread? = null
    private var menuOpen by mutableStateOf(false)
    private var editing by mutableStateOf(false)
    private var selected by mutableStateOf<Control?>(null)
    private var ended: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        Keyboard.host = this

        val game = intent.getStringExtra(EXTRA_GAME) ?: return finish()
        val root = FrameLayout(this)
        val surface = SurfaceView(this).also { it.holder.addCallback(this) }
        overlay = OverlayView(this, OverlayLayout.load(isPortrait())).apply {
            onMenu = { openMenu() }
            onSelect = { c -> this@EmulationActivity.selected = c }   // not the view's own `selected`: that loops
        }
        applyOverlayPrefs()
        root.addView(surface, FrameLayout.LayoutParams(-1, -1))
        root.addView(overlay, FrameLayout.LayoutParams(-1, -1))
        root.addView(ComposeView(this).apply { setContent { Mh4uTheme { Hud() } } }, FrameLayout.LayoutParams(-1, -1))
        setContentView(root)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (menuOpen) closeMenu() else openMenu() }
        })

        Prefs.apply()
        gameThread = Thread({
            val message = Native.run(game)
            runOnUiThread {
                ended = message
                if (!isFinishing) { setResult(RESULT_OK, android.content.Intent().putExtra(EXTRA_MESSAGE, message)); finish() }
            }
        }, "mh4u-game").also { it.start() }
    }

    private fun isPortrait() = resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_PORTRAIT

    private fun applyOverlayPrefs() {
        val padConnected = InputDevice.getDeviceIds().any { id ->
            InputDevice.getDevice(id)?.let { d ->
                d.sources and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD && !d.isVirtual
            } == true
        }
        val hideForPad = Prefs.getBool("Android", "overlay_autohide", false) && padConnected
        overlay.controlsVisible = Prefs.getBool("Android", "overlay", true) && !hideForPad
        overlay.layout.opacity = OverlayLayout.load(false).opacity
    }

    // ── menu ──

    private fun openMenu() { menuOpen = true; overlay.shown = false; MH4UInput.releaseAll() }
    private fun closeMenu() { menuOpen = false; overlay.shown = true; applyOverlayPrefs() }

    @Composable
    private fun Hud() {
        var fps by remember { mutableStateOf("") }
        var notice by remember { mutableStateOf("") }
        val counter = remember(menuOpen) { Prefs.getApp("Display", "fps_counter", "off") == "on" }
        LaunchedEffect(Unit) {
            delay(2500); notice = Native.notice()
            while (true) {
                val p = Native.perf()
                if (counter && p.size == 2) fps = "%.0f fps  %.0f%%".format(p[0], p[1])
                delay(1000)
            }
        }
        Box(Modifier.fillMaxSize()) {
            if (counter && fps.isNotEmpty() && !menuOpen)
                Text(fps, color = Mh4uColors.Amber, fontSize = 12.sp, modifier = Modifier.align(Alignment.TopStart).statusBarsPadding().padding(8.dp)
                    .background(Color(0x99121214)).padding(horizontal = 6.dp, vertical = 2.dp))
            if (notice.isNotEmpty() && !menuOpen)
                Box(Modifier.align(Alignment.TopCenter).statusBarsPadding().padding(16.dp).clickable { notice = "" }) { Notice(notice + "\n(tap to dismiss)") }
            if (editing) EditBar()
            if (menuOpen) Menu()
        }
    }

    @Composable
    private fun Menu() {
        var tab by remember { mutableIntStateOf(0) }
        val titles = listOf("Game", "Display", "Audio", "Controls", "Multiplayer")
        Surface(Modifier.fillMaxSize(), color = Mh4uColors.WindowBg.copy(alpha = 0.97f)) {
            Column(Modifier.statusBarsPadding().navigationBarsPadding()) {
                Row(Modifier.fillMaxWidth().background(Mh4uColors.TitleBgActive).padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("MH4U", color = Mh4uColors.Amber, fontSize = 18.sp, modifier = Modifier.weight(1f))
                    QuietButton("Quest map") { Native.toggleQuestMap() }
                    Spacer(Modifier.width(8.dp))
                    AmberButton("Resume") { closeMenu() }
                    Spacer(Modifier.width(8.dp))
                    QuietButton("Quit") { Native.stop() }
                }
                Tabs(titles, tab) { tab = it }
                Box(Modifier.weight(1f)) {
                    when (tab) {
                        0 -> GamePage()
                        1 -> DisplayPage()
                        2 -> AudioPage()
                        3 -> ControlsPage(onEditOverlay = { menuOpen = false; startEditing() }, onOverlayChanged = { applyOverlayPrefs() })
                        else -> MultiplayerPage()
                    }
                }
            }
        }
    }

    // ── editing the on-screen controls ──

    private fun startEditing() { overlay.selected = null; overlay.shown = true; overlay.controlsVisible = true; overlay.editing = true; editing = true }
    private fun stopEditing() {
        overlay.editing = false; editing = false; overlay.layout.save(isPortrait()); overlay.selected = null; closeMenu()
    }

    @Composable
    private fun EditBar() {
        var comboName by remember { mutableStateOf("") }
        var mask by remember { mutableIntStateOf(0) }
        var latch by remember { mutableStateOf(false) }
        var adding by remember { mutableStateOf(false) }
        Box(Modifier.fillMaxSize()) {
            Surface(Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(8.dp), color = Mh4uColors.PopupBg.copy(alpha = .95f),
                shape = MaterialTheme.shapes.medium, border = androidx.compose.foundation.BorderStroke(1.dp, Mh4uColors.Border)) {
                Column(Modifier.padding(12.dp)) {
                    Text(selected?.let { "Selected: " + it.label.ifEmpty { it.kind.name.lowercase().replace('_', ' ') } }
                        ?: "Drag a control to move it. Pinch to resize the selected one.", fontSize = 13.sp)
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        QuietButton("−", enabled = selected != null) { selected?.let { it.size = (it.size * 0.9f).coerceAtLeast(0.04f); overlay.invalidate() } }
                        QuietButton("+", enabled = selected != null) { selected?.let { it.size = (it.size * 1.1f).coerceAtMost(0.5f); overlay.invalidate() } }
                        QuietButton("Hide", enabled = selected != null) { selected?.let { it.visible = !it.visible; overlay.invalidate() } }
                        QuietButton("Delete", enabled = selected?.kind == Control.Kind.COMBO) {
                            selected?.let { overlay.layout.controls.remove(it); overlay.selected = null; overlay.invalidate() }
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        QuietButton("Add combo") { adding = !adding }
                        QuietButton("Reset") { overlay.layout.controls.clear(); overlay.layout.controls.addAll(OverlayLayout.defaults(isPortrait()).controls); overlay.selected = null; overlay.invalidate() }
                        AmberButton("Done") { stopEditing() }
                    }
                    if (adding) {
                        Spacer(Modifier.height(8.dp))
                        TextRow("Name", null, comboName) { comboName = it }
                        FlowChips(mask) { mask = it }
                        ToggleRow("Stay pressed until tapped again", null, latch) { latch = it }
                        AmberButton("Create", enabled = mask != 0 && comboName.isNotBlank()) {
                            overlay.selected = overlay.layout.addCombo(comboName.trim().take(6), mask, latch)
                            overlay.invalidate(); adding = false; comboName = ""; mask = 0
                        }
                    }
                }
            }
        }
    }

    // ── surface ──

    override fun surfaceCreated(holder: SurfaceHolder) { Native.surfaceChanged(holder.surface) }
    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) { Native.surfaceChanged(holder.surface) }
    override fun surfaceDestroyed(holder: SurfaceHolder) { Native.surfaceDestroyed() }

    // ── input ──

    override fun dispatchKeyEvent(e: KeyEvent): Boolean {
        if (e.keyCode == KeyEvent.KEYCODE_BUTTON_MODE && e.action == KeyEvent.ACTION_DOWN && !menuOpen) { openMenu(); return true }
        // while binding in the menu the engine learns from the same events
        if (MH4UInput.onKey(e) && (!menuOpen || MH4UInput.learning())) return true
        return super.dispatchKeyEvent(e)
    }

    override fun dispatchGenericMotionEvent(e: MotionEvent): Boolean =
        MH4UInput.onMotion(e) && (!menuOpen || MH4UInput.learning()) || super.dispatchGenericMotionEvent(e)

    // ── life ──

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    override fun onPause() {
        super.onPause()
        MH4UInput.releaseAll(); overlay.releaseEverything()
        // a room goes on in the background; alone, the game waits
        if (org.json.JSONObject(Native.roomStatus()).optString("phase") != "room") Native.setPaused(true)
    }

    override fun onResume() { super.onResume(); Native.setPaused(false) }

    override fun onDestroy() {
        super.onDestroy()
        if (Keyboard.host === this) Keyboard.host = null
        if (isFinishing) { Native.stop(); gameThread?.join(5000) }
    }

    companion object {
        const val EXTRA_GAME = "game"
        const val EXTRA_MESSAGE = "message"
    }
}
