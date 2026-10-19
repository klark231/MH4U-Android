package org.mh4u.port.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** The PC app's colours (app/ui/ui.cpp, launcher/ui.cpp): warm black windows, amber accent. */
object Mh4uColors {
    val Amber = Color(0xFFEBA133)
    val AmberBright = Color(0xFFFFC25C)
    val Text = Color(0xFFEDEBE3)
    val TextDisabled = Color(0xFF948F85)
    val WindowBg = Color(0xFF121214)
    val PopupBg = Color(0xFF17171A)
    val FrameBg = Color(0xFF29292B)
    val FrameBgHovered = Color(0xFF453826)
    val FrameBgActive = Color(0xFF5C4726)
    val TitleBg = Color(0xFF1A1714)
    val TitleBgActive = Color(0xFF332617)
    val Button = Color(0xFF36302B)
    val ButtonHovered = Color(0xFF755426)
    val ButtonActive = Color(0xFF9E6E26)
    val Header = Color(0xFF4F3D24)
    val Tab = Color(0xFF262421)
    val TabHovered = Color(0xFF855E26)
    val TabSelected = Color(0xFF664A21)
    val Notice = Color(0xFFFFBF73)
    val Border = Amber.copy(alpha = 0.30f)
    val Error = Color(0xFFE5645A)
}

private val scheme = darkColorScheme(
    primary = Mh4uColors.Amber,
    onPrimary = Color(0xFF1A1204),
    primaryContainer = Mh4uColors.TabSelected,
    onPrimaryContainer = Mh4uColors.Text,
    secondary = Mh4uColors.ButtonHovered,
    onSecondary = Mh4uColors.Text,
    secondaryContainer = Mh4uColors.Button,
    onSecondaryContainer = Mh4uColors.Text,
    tertiary = Mh4uColors.AmberBright,
    background = Mh4uColors.WindowBg,
    onBackground = Mh4uColors.Text,
    surface = Mh4uColors.WindowBg,
    onSurface = Mh4uColors.Text,
    surfaceVariant = Mh4uColors.FrameBg,
    onSurfaceVariant = Mh4uColors.TextDisabled,
    surfaceContainer = Mh4uColors.PopupBg,
    surfaceContainerHigh = Mh4uColors.TitleBg,
    surfaceContainerHighest = Mh4uColors.TitleBgActive,
    outline = Mh4uColors.Border,
    outlineVariant = Mh4uColors.Border,
    error = Mh4uColors.Error,
)

@Composable
fun Mh4uTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = scheme, content = content)
}
