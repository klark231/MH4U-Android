package org.mh4u.port.ui

import android.content.Context
import android.hardware.input.InputManager
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import org.mh4u.port.MH4UInput
import org.mh4u.port.Prefs
import org.mh4u.port.overlay.OverlayLayout

/**
 * Controls: bindings for any device Android lists (a gamepad, a keyboard, a mouse), combos, and
 * the on-screen controls. Binding listens inline (not in a dialog) so that the keys and sticks
 * reach the activity, which feeds them to the engine.
 */
@Composable
fun ControlsPage(onEditOverlay: (() -> Unit)? = null, onOverlayChanged: () -> Unit = {}) {
    val context = LocalContext.current
    var bindings by remember { mutableStateOf(MH4UInput.bindings()) }
    var devices by remember { mutableStateOf(MH4UInput.devices()) }
    var listeningFor by remember { mutableStateOf<Int?>(null) }   // 3DS button mask being bound
    var comboMask by remember { mutableIntStateOf(0) }
    var comboListening by remember { mutableStateOf(false) }
    var addMode by remember { mutableStateOf(false) }             // add a source, don't replace

    // devices come and go
    DisposableEffect(Unit) {
        val im = context.getSystemService(Context.INPUT_SERVICE) as InputManager
        val l = object : InputManager.InputDeviceListener {
            override fun onInputDeviceAdded(id: Int) { devices = MH4UInput.devices() }
            override fun onInputDeviceRemoved(id: Int) { devices = MH4UInput.devices() }
            override fun onInputDeviceChanged(id: Int) { devices = MH4UInput.devices() }
        }
        im.registerInputDeviceListener(l, null)
        onDispose { im.unregisterInputDeviceListener(l); MH4UInput.cancelLearn() }
    }

    // while listening: a chord held and let go ends it
    val target = listeningFor ?: if (comboListening) comboMask else null
    LaunchedEffect(target, comboListening) {
        if (target == null || target == 0) return@LaunchedEffect
        MH4UInput.startLearn()
        while (true) {
            delay(60)
            val chord = MH4UInput.learned()
            if (chord != null) {
                if (addMode) MH4UInput.addAlternative(target, chord) else MH4UInput.bind(target, chord)
                bindings = MH4UInput.bindings()
                listeningFor = null; comboListening = false
                break
            }
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp)) {
        SectionTitle("Devices")
        Panel {
            if (devices.isEmpty()) Hint("No gamepad or keyboard connected. The on-screen controls always work.")
            else devices.forEach { Text("•  $it", fontSize = 14.sp, modifier = Modifier.padding(vertical = 2.dp)) }
            Spacer(Modifier.height(6.dp))
            Hint("Any controller or keyboard Android lists works: Bluetooth, USB or built in. A binding can name one device, so two pads can do different things.")
        }

        SectionTitle("3DS buttons")
        Panel {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("When binding:", fontSize = 13.sp, modifier = Modifier.weight(1f))
                FilterChip(selected = !addMode, onClick = { addMode = false }, label = { Text("Replace") })
                Spacer(Modifier.width(8.dp))
                FilterChip(selected = addMode, onClick = { addMode = true }, label = { Text("Add another") })
            }
            RowDivider()
            MH4UInput.buttons.forEach { (name, mask) ->
                val mine = bindings.filter { it.mask == mask }
                val busy = listeningFor == mask
                Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(name, fontSize = 15.sp)
                        Text(
                            if (busy) "Hold the key or button, then let go…"
                            else mine.joinToString("   |   ") { it.text }.ifEmpty { "not bound" },
                            color = if (busy) Mh4uColors.Amber else Mh4uColors.TextDisabled, fontSize = 12.sp,
                        )
                    }
                    if (busy) QuietButton("Cancel") { MH4UInput.cancelLearn(); listeningFor = null }
                    else {
                        QuietButton("Bind") { comboListening = false; listeningFor = mask }
                        if (mine.isNotEmpty()) TextButton(onClick = { MH4UInput.unbind(mask); bindings = MH4UInput.bindings() }) { Text("Clear", color = Mh4uColors.TextDisabled) }
                    }
                }
                RowDivider()
            }
        }

        SectionTitle("Combos")
        Panel {
            Hint("One key or button pressing several 3DS buttons at once, or several held together pressing one. Pick the 3DS buttons, then hold the input.")
            Spacer(Modifier.height(8.dp))
            FlowChips(comboMask) { comboMask = it }
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (comboListening) {
                    Text("Hold the input, then let go…", color = Mh4uColors.Amber, modifier = Modifier.weight(1f))
                    QuietButton("Cancel") { MH4UInput.cancelLearn(); comboListening = false }
                } else {
                    Text(if (comboMask == 0) "No buttons picked" else MH4UInput.maskName(comboMask), modifier = Modifier.weight(1f), fontSize = 14.sp)
                    AmberButton("Bind combo", enabled = comboMask != 0) { listeningFor = null; comboListening = true }
                }
            }
            val combos = bindings.filter { Integer.bitCount(it.mask) > 1 || it.text.contains(" + ") }
            if (combos.isNotEmpty()) {
                RowDivider()
                combos.forEach {
                    Row(Modifier.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(MH4UInput.maskName(it.mask), fontSize = 14.sp)
                            Hint(it.text)
                        }
                    }
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        QuietButton("Reset bindings to defaults") { MH4UInput.resetToDefaults(); bindings = MH4UInput.bindings() }

        SectionTitle("On-screen controls")
        Panel {
            var show by remember { mutableStateOf(Prefs.getBool("Android", "overlay", true)) }
            ToggleRow("Show them", null, show) { show = it; Prefs.setBool("Android", "overlay", it); onOverlayChanged() }
            var autoHide by remember { mutableStateOf(Prefs.getBool("Android", "overlay_autohide", false)) }
            ToggleRow("Hide when a controller is connected", null, autoHide) { autoHide = it; Prefs.setBool("Android", "overlay_autohide", it); onOverlayChanged() }
            val layout = remember { OverlayLayout.load(false) }
            var opacity by remember { mutableFloatStateOf(layout.opacity * 100f) }
            SliderRow("Opacity", null, opacity, 10f..100f, " %") { opacity = it; layout.opacity = it / 100f; layout.save(false); onOverlayChanged() }
            var haptics by remember { mutableStateOf(layout.haptics) }
            ToggleRow("Vibrate on press", null, haptics) { haptics = it; layout.haptics = it; layout.save(false); onOverlayChanged() }
            RowDivider()
            if (onEditOverlay != null) {
                Spacer(Modifier.height(6.dp))
                AmberButton("Edit layout and combo buttons", Modifier.fillMaxWidth()) { onEditOverlay() }
            } else Hint("Layout and combo buttons are edited in the game: menu > Controls > Edit layout.")
        }
        Spacer(Modifier.height(24.dp))
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FlowChips(mask: Int, onChange: (Int) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        MH4UInput.buttons.forEach { (name, bit) ->
            FilterChip(selected = mask and bit != 0, onClick = { onChange(mask xor bit) }, label = { Text(name) })
        }
    }
}
