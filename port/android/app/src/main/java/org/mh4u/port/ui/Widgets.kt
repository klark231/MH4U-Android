package org.mh4u.port.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Shared building blocks, in the PC app's look: framed sections, amber headers. */

@Composable
fun SectionTitle(text: String) {
    Text(
        text, color = Mh4uColors.Amber, fontWeight = FontWeight.SemiBold, fontSize = 13.sp,
        modifier = Modifier.padding(start = 4.dp, top = 18.dp, bottom = 6.dp),
    )
}

@Composable
fun Panel(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Surface(
        modifier = modifier.fillMaxWidth(), color = Mh4uColors.PopupBg,
        shape = RoundedCornerShape(10.dp), border = BorderStroke(1.dp, Mh4uColors.Border),
    ) { Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp), content = content) }
}

@Composable
fun Hint(text: String) {
    Text(text, color = Mh4uColors.TextDisabled, fontSize = 12.sp, lineHeight = 16.sp)
}

@Composable
fun Notice(text: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier.fillMaxWidth(), color = Mh4uColors.TitleBg,
        shape = RoundedCornerShape(8.dp), border = BorderStroke(1.dp, Mh4uColors.Notice.copy(alpha = .5f)),
    ) { Text(text, color = Mh4uColors.Notice, fontSize = 13.sp, modifier = Modifier.padding(12.dp)) }
}

@Composable
fun AmberButton(text: String, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    Button(
        onClick = onClick, enabled = enabled, modifier = modifier,
        colors = ButtonDefaults.buttonColors(
            containerColor = Mh4uColors.ButtonHovered, contentColor = Mh4uColors.Text,
            disabledContainerColor = Mh4uColors.Button, disabledContentColor = Mh4uColors.TextDisabled,
        ),
        shape = RoundedCornerShape(8.dp),
    ) { Text(text) }
}

@Composable
fun QuietButton(text: String, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    OutlinedButton(
        onClick = onClick, enabled = enabled, modifier = modifier,
        border = BorderStroke(1.dp, Mh4uColors.Border), shape = RoundedCornerShape(8.dp),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = Mh4uColors.Text),
    ) { Text(text) }
}

/** The tab bar of the app's windows. */
@Composable
fun Tabs(titles: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    ScrollableTabRow(
        selectedTabIndex = selected, containerColor = Mh4uColors.TitleBg, edgePadding = 8.dp,
        indicator = { positions ->
            TabRowDefaults.SecondaryIndicator(with(TabRowDefaults) { Modifier.tabIndicatorOffset(positions[selected]) }, color = Mh4uColors.Amber)
        },
        divider = {},
    ) {
        titles.forEachIndexed { i, t ->
            Tab(
                selected = i == selected, onClick = { onSelect(i) },
                text = { Text(t, color = if (i == selected) Mh4uColors.Amber else Mh4uColors.Text) },
            )
        }
    }
}

// ── one setting per row, written to the ini as soon as it changes ──

@Composable
fun ChoiceRow(title: String, hint: String?, options: List<Pair<String, String>>, value: String, onChange: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    val label = options.firstOrNull { it.second == value }?.first ?: value
    Row(
        Modifier.fillMaxWidth().clickable { open = true }.padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 15.sp)
            if (hint != null) Hint(hint)
        }
        Text(label, color = Mh4uColors.Amber, fontSize = 14.sp)
        DropdownMenu(expanded = open, onDismissRequest = { open = false }, containerColor = Mh4uColors.PopupBg) {
            options.forEach { (name, v) ->
                DropdownMenuItem(
                    text = { Text(name, color = if (v == value) Mh4uColors.Amber else Mh4uColors.Text) },
                    onClick = { onChange(v); open = false },
                )
            }
        }
    }
}

@Composable
fun ToggleRow(title: String, hint: String?, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable { onChange(!checked) }.padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 15.sp)
            if (hint != null) Hint(hint)
        }
        Switch(
            checked = checked, onCheckedChange = onChange,
            colors = SwitchDefaults.colors(
                checkedThumbColor = Mh4uColors.AmberBright, checkedTrackColor = Mh4uColors.TabSelected,
                uncheckedThumbColor = Mh4uColors.TextDisabled, uncheckedTrackColor = Mh4uColors.FrameBg,
                uncheckedBorderColor = Mh4uColors.Border,
            ),
        )
    }
}

@Composable
fun SliderRow(
    title: String, hint: String?, value: Float, range: ClosedFloatingPointRange<Float>, unit: String = "",
    steps: Int = 0, onChange: (Float) -> Unit,
) {
    var local by remember(value) { mutableFloatStateOf(value) }
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(title, fontSize = 15.sp, modifier = Modifier.weight(1f))
            Text(
                (if (local % 1f == 0f) local.toInt().toString() else "%.1f".format(local)) + unit,
                color = Mh4uColors.Amber, fontSize = 14.sp,
            )
        }
        Slider(
            value = local, onValueChange = { local = it }, onValueChangeFinished = { onChange(local) },
            valueRange = range, steps = steps,
            colors = SliderDefaults.colors(
                thumbColor = Mh4uColors.Amber, activeTrackColor = Mh4uColors.Amber,
                inactiveTrackColor = Mh4uColors.FrameBg,
            ),
        )
        if (hint != null) Hint(hint)
    }
}

@Composable
fun TextRow(title: String, hint: String?, value: String, password: Boolean = false, onChange: (String) -> Unit) {
    var local by remember(value) { mutableStateOf(value) }
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        OutlinedTextField(
            value = local, onValueChange = { local = it; onChange(it) }, singleLine = true,
            label = { Text(title) }, modifier = Modifier.fillMaxWidth(),
            visualTransformation = if (password) androidx.compose.ui.text.input.PasswordVisualTransformation()
            else androidx.compose.ui.text.input.VisualTransformation.None,
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Mh4uColors.Amber, unfocusedBorderColor = Mh4uColors.Border,
                focusedLabelColor = Mh4uColors.Amber, cursorColor = Mh4uColors.Amber,
            ),
        )
        if (hint != null) Hint(hint)
    }
}

@Composable
fun RowDivider() = HorizontalDivider(color = Mh4uColors.Border.copy(alpha = .15f))
