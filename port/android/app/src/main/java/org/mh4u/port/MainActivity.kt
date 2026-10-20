package org.mh4u.port

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.activity.compose.BackHandler
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.mh4u.port.ui.*
import java.io.File

/**
 * The launcher (the PC app's launcher): Home (play, what is ready), Setup (your game, the code
 * library, the update), Settings, Online (the room the game starts with).
 */
class MainActivity : ComponentActivity() {
    private var lastMessage by mutableStateOf("")

    private val gameResult = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        lastMessage = it.data?.getStringExtra(EmulationActivity.EXTRA_MESSAGE).orEmpty()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { Mh4uTheme { Launcher() } }
    }

    // pads work in the menus' binding screens too
    override fun dispatchKeyEvent(e: android.view.KeyEvent): Boolean =
        (MH4UInput.learning() && MH4UInput.onKey(e)) || super.dispatchKeyEvent(e)

    override fun dispatchGenericMotionEvent(e: android.view.MotionEvent): Boolean =
        (MH4UInput.learning() && MH4UInput.onMotion(e)) || super.dispatchGenericMotionEvent(e)

    private enum class Screen(val title: String) { HOME("Home"), SETUP("Setup"), SETTINGS("Settings"), ONLINE("Online") }

    @Composable
    private fun Launcher() {
        var screen by remember { mutableStateOf(Screen.HOME) }
        var ready by remember { mutableIntStateOf(0) }   // bump to re-check files
        val gameOk = remember(ready) { Prefs.gameFile() != null }
        val codeOk = remember(ready) { File(Storage.recomp, "0004000000126300.so").exists() }

        Column(Modifier.fillMaxSize().background(Mh4uColors.WindowBg).statusBarsPadding().navigationBarsPadding()) {
            Row(Modifier.fillMaxWidth().background(Mh4uColors.TitleBgActive).padding(horizontal = 16.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text("MH4U", color = Mh4uColors.Amber, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.width(8.dp))
                Text("Monster Hunter 4 Ultimate · port", color = Mh4uColors.TextDisabled, fontSize = 13.sp)
            }
            Tabs(Screen.entries.map { it.title }, screen.ordinal) { screen = Screen.entries[it] }
            Box(Modifier.weight(1f)) {
                when (screen) {
                    Screen.HOME -> Home(gameOk, codeOk, onSetup = { screen = Screen.SETUP })
                    Screen.SETUP -> Setup(gameOk, codeOk) { ready++ }
                    Screen.SETTINGS -> SettingsScreen()
                    Screen.ONLINE -> OnlineBeforeGame()
                }
            }
        }
    }

    // ── home ──

    @Composable
    private fun Home(gameOk: Boolean, codeOk: Boolean, onSetup: () -> Unit) {
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
            if (lastMessage.isNotEmpty()) { Notice(lastMessage); Spacer(Modifier.height(12.dp)) }
            Panel {
                Text("Ready to play?", fontSize = 18.sp)
                Spacer(Modifier.height(10.dp))
                Check("Your game", gameOk, "Choose it in Setup")
                Check("The port's code library", codeOk, "Without it the game runs on the emulator's own CPU: slow, and the port's options don't apply")
                Spacer(Modifier.height(14.dp))
                AmberButton(if (gameOk) "Play" else "Set up first", Modifier.fillMaxWidth().height(52.dp)) {
                    val game = Prefs.gamePath
                    if (gameOk) { lastMessage = ""; gameResult.launch(Intent(this@MainActivity, EmulationActivity::class.java).putExtra(EmulationActivity.EXTRA_GAME, game)) }
                    else onSetup()
                }
            }
            SectionTitle("Room")
            Panel {
                val join = Prefs.getApp("Multiplayer", "join", "")
                val host = Prefs.getInt("Multiplayer", "host", 0)
                Text(when { join.isNotEmpty() -> "Joins $join when the game starts"; host > 0 -> "Hosts a room on port $host"; else -> "Solo: no room" }, fontSize = 14.sp)
                Hint("Rooms can also be joined, hosted and left in the game: menu > Multiplayer.")
            }
        }
    }

    @Composable
    private fun Check(label: String, ok: Boolean, why: String) {
        Row(Modifier.padding(vertical = 4.dp), verticalAlignment = Alignment.Top) {
            Text(if (ok) "✓" else "•", color = if (ok) Mh4uColors.Amber else Mh4uColors.Notice, fontSize = 16.sp, modifier = Modifier.width(22.dp))
            Column { Text(label, fontSize = 15.sp); if (!ok) Hint(why) }
        }
    }

    // ── setup ──

    @Composable
    private fun Setup(gameOk: Boolean, codeOk: Boolean, changed: () -> Unit) {
        val scope = rememberCoroutineScope()
        var busy by remember { mutableStateOf("") }
        var progress by remember { mutableFloatStateOf(-1f) }
        var message by remember { mutableStateOf("") }
        val ctx = LocalContext.current

        suspend fun importTo(uri: Uri, dir: File, name: String?): File? = withContext(Dispatchers.IO) {
            val shown = name ?: ctx.contentResolver.query(uri, null, null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(c.getColumnIndexOrThrow(OpenableColumns.DISPLAY_NAME)) else null
            } ?: "file"
            val out = File(dir, shown)
            val total = ctx.contentResolver.openFileDescriptor(uri, "r")?.use { it.statSize } ?: -1L
            runCatching {
                ctx.contentResolver.openInputStream(uri)!!.use { input ->
                    out.outputStream().use { o ->
                        val buf = ByteArray(1 shl 20); var done = 0L
                        while (true) {
                            val n = input.read(buf); if (n < 0) break
                            o.write(buf, 0, n); done += n
                            if (total > 0) progress = done.toFloat() / total
                        }
                    }
                }
                out
            }.getOrElse { out.delete(); message = "Couldn't copy the file: ${it.message}"; null }
        }

        /** Copies the user folder to [target] and remembers it for the next start. */
        fun moveUserFolder(target: File): String {
            val old = Storage.root
            if (target.canonicalPath == old.canonicalPath) return "That folder is already in use."
            if (!(target.isDirectory || target.mkdirs()) || !target.canWrite()) return "Couldn't write to that folder."
            // the game's path is absolute: point it at the new place before the copy, so the copy carries it
            val oldGame = Prefs.gamePath
            val prefix = "!" + old.absolutePath
            if (oldGame.startsWith(prefix)) Prefs.gamePath = "!" + target.absolutePath + oldGame.removePrefix(prefix)
            return runCatching {
                old.copyRecursively(target, overwrite = false) { _, _ -> OnErrorAction.SKIP }
                Storage.chooseRoot(target)
                "Files copied to ${target.absolutePath}. Close the app completely and open it again to use that folder. The old copy is left where it was; delete it when everything works."
            }.getOrElse { Prefs.gamePath = oldGame; "Couldn't copy the files: ${it.message}" }
        }

        val pickFolder = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
            val path = uri?.let { Storage.treeToPath(it) }
            if (uri != null && path == null) message = "That folder can't be used. Pick one on the phone's storage or an SD card."
            if (path != null) scope.launch {
                busy = "Copying your files…"; progress = -1f; message = ""
                message = withContext(Dispatchers.IO) { moveUserFolder(File(path)) }
                busy = ""
            }
        }

        fun askFolder() {
            if (!Storage.hasAllFilesAccess()) {
                message = "Allow \"All files access\" for this app on the next screen, then come back and choose the folder again."
                ctx.startActivity(Intent(android.provider.Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:" + ctx.packageName)))
            } else pickFolder.launch(null)
        }

        val pickGame = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) scope.launch {
                busy = "Copying the game…"; progress = 0f; message = ""
                importTo(uri, Storage.games, null)?.let { Prefs.gamePath = "!" + it.absolutePath; message = "Game ready: ${it.name}" }
                busy = ""; progress = -1f; changed()
            }
        }
        val pickCode = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) scope.launch {
                busy = "Copying the code library…"; progress = 0f; message = ""
                // the core looks for <program id>.so in recomp/
                importTo(uri, Storage.recomp, "0004000000126300.so")?.let { message = "Code library ready" }
                busy = ""; progress = -1f; changed()
            }
        }
        val pickCia = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) scope.launch {
                busy = "Installing the update…"; progress = -1f; message = ""
                val file = importTo(uri, ctx.cacheDir, "update.cia")
                if (file != null) {
                    val status = withContext(Dispatchers.IO) { Native.installCia("!" + file.absolutePath) }
                    file.delete()
                    message = if (status == 0) "Update installed" else "The update didn't install (status $status)"
                }
                busy = ""
            }
        }

        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
            if (busy.isNotEmpty()) {
                Panel {
                    Text(busy, color = Mh4uColors.Amber)
                    Spacer(Modifier.height(8.dp))
                    if (progress >= 0f) LinearProgressIndicator(progress = { progress }, color = Mh4uColors.Amber, trackColor = Mh4uColors.FrameBg, modifier = Modifier.fillMaxWidth())
                    else LinearProgressIndicator(color = Mh4uColors.Amber, trackColor = Mh4uColors.FrameBg, modifier = Modifier.fillMaxWidth())
                }
                Spacer(Modifier.height(12.dp))
            } else if (message.isNotEmpty()) { Notice(message); Spacer(Modifier.height(12.dp)) }

            SectionTitle("1 · Your game")
            Panel {
                Check("Decrypted Monster Hunter 4 Ultimate (.3ds, .cci or .cxi)", gameOk, "Dump your own cartridge, decrypted (GodMode9 can).")
                Hint("The app copies it into its own storage.")
                Spacer(Modifier.height(8.dp))
                AmberButton(if (gameOk) "Choose another" else "Choose the game", enabled = busy.isEmpty()) { pickGame.launch(arrayOf("*/*")) }
            }
            SectionTitle("2 · The code library")
            Panel {
                Check("0004000000126300.so", codeOk, "Built on a PC from your own game; the app contains no game code.")
                Hint("On a PC, with the Android NDK: port/android/build-code-library.sh makes the arm64 library from your dump. Copy it to this phone and choose it here.")
                Spacer(Modifier.height(8.dp))
                AmberButton(if (codeOk) "Choose another" else "Choose the library", enabled = busy.isEmpty()) { pickCode.launch(arrayOf("*/*")) }
            }
            SectionTitle("3 · The update (v1.3.0)")
            Panel {
                Hint("The port targets the USA 1.3.0 update. Install its .cia once; skip this if it is installed already.")
                Spacer(Modifier.height(8.dp))
                QuietButton("Install the update (.cia)", enabled = busy.isEmpty()) { pickCia.launch(arrayOf("*/*")) }
            }
            SectionTitle("Files")
            Panel {
                Text(Storage.root.absolutePath, fontSize = 12.sp, color = Mh4uColors.TextDisabled)
                Hint("Settings, saves and the user folder live here. mh4u-app.ini and mh4u.ini are the PC port's files and can be copied between the two.")
                Spacer(Modifier.height(8.dp))
                QuietButton("Use a folder on this phone", enabled = busy.isEmpty()) { askFolder() }
                Hint("Keeps your game, saves and settings where you can reach them, and they survive an uninstall. Needs \"All files access\".")
            }
            Spacer(Modifier.height(24.dp))
        }
    }

    // ── settings ──

    @Composable
    private fun SettingsScreen() {
        var tab by remember { mutableIntStateOf(0) }
        Column {
            Tabs(listOf("Game", "Display", "Audio", "Controls"), tab) { tab = it }
            Box(Modifier.weight(1f)) {
                when (tab) { 0 -> GamePage(); 1 -> DisplayPage(); 2 -> AudioPage(); else -> ControlsPage() }
            }
        }
    }

    // ── online, before the game: the room it starts with ──

    @Composable
    private fun OnlineBeforeGame() {
        var join by remember { mutableStateOf(Prefs.getApp("Multiplayer", "join", "")) }
        var password by remember { mutableStateOf(Prefs.getApp("Multiplayer", "password", "")) }
        var host by remember { mutableStateOf(Prefs.getInt("Multiplayer", "host", 0)) }
        var nick by remember { mutableStateOf(Prefs.getApp("Multiplayer", "nickname", "")) }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp)) {
            SectionTitle("The room the game starts with")
            Panel {
                Hint("Set a room here and the game joins or hosts it when it starts. Once the game runs, its menu > Multiplayer joins, hosts and leaves rooms at any time.")
                TextRow("Nickname", "Empty: the console's user name.", nick) { nick = it; if (it.isEmpty() || Native.validNickname(it)) Prefs.setApp("Multiplayer", "nickname", it) }
                TextRow("Invite code or address to join", "mh4u://host:port, or host or host:port. Empty: not joining.", join) {
                    join = it; Prefs.setApp("Multiplayer", "join", it); if (it.isNotEmpty()) { host = 0; Prefs.setApp("Multiplayer", "host", "0") }
                }
                TextRow("Password (when the address has none)", null, password, password = true) { password = it; Prefs.setApp("Multiplayer", "password", it) }
                ToggleRow("Host a room instead", "Friends join this phone's room, on the port below.", host > 0) {
                    host = if (it) Prefs.getInt("Multiplayer", "room_port", 24872) else 0
                    Prefs.setApp("Multiplayer", "host", host.toString()); if (it) { join = ""; Prefs.setApp("Multiplayer", "join", "") }
                }
                if (host > 0) TextRow("Port", null, host.toString()) {
                    it.filter(Char::isDigit).toIntOrNull()?.let { p -> host = p; Prefs.setApp("Multiplayer", "host", p.toString()); Prefs.setApp("Multiplayer", "room_port", p.toString()) }
                }
            }
            SectionTitle("Lobby")
            LobbyPanel(onJoin = { h, p, pw ->
                join = "$h:$p"; Prefs.setApp("Multiplayer", "join", join); Prefs.setApp("Multiplayer", "host", "0"); host = 0
                if (pw.isNotEmpty()) { password = pw; Prefs.setApp("Multiplayer", "password", pw) }
            })
            Spacer(Modifier.height(24.dp))
        }
    }
}
