package org.mh4u.port.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject
import org.mh4u.port.Native
import org.mh4u.port.Prefs

/**
 * Rooms: join with an invite code or an address, host one, or pick one from a lobby, before the
 * game starts or while it runs (Azahar's multiplayer carries MH4U's local play). The same
 * rooms, invite codes (mh4u://host:port) and lobby as the PC app's Online screen.
 */
@Composable
fun MultiplayerPage() {
    val context = LocalContext.current
    var status by remember { mutableStateOf(JSONObject(Native.roomStatus())) }
    LaunchedEffect(Unit) { while (true) { status = JSONObject(Native.roomStatus()); delay(1000) } }

    val phase = status.optString("phase")
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp)) {
        SectionTitle("You")
        Panel {
            var nick by remember { mutableStateOf(Prefs.getApp("Multiplayer", "nickname", "")) }
            TextRow("Nickname", "4 to 20 letters, digits, spaces, '.', '_' or '-'. Empty: the console's user name.", nick) {
                nick = it; if (it.isEmpty() || Native.validNickname(it)) Prefs.setApp("Multiplayer", "nickname", it)
            }
            if (nick.isNotEmpty() && !Native.validNickname(nick)) Text("This nickname isn't accepted by rooms.", color = Mh4uColors.Notice, fontSize = 12.sp)
        }

        if (status.optString("error").isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Notice(status.optString("error") + status.optString("hint").let { if (it.isEmpty()) "" else "\n$it" })
        }

        when (phase) {
            "busy" -> { SectionTitle("Room"); Panel { Text(status.optString("action"), color = Mh4uColors.Amber) } }
            "room" -> RoomPanel(context, status)
            else -> OfflinePanels()
        }
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun RoomPanel(context: Context, s: JSONObject) {
    SectionTitle(if (s.optBoolean("hosting")) "Your room" else "In a room")
    Panel {
        Text(s.optString("room_name").ifEmpty { "${s.optString("host")}:${s.optInt("port")}" }, fontSize = 17.sp)
        Hint("${s.optString("host")}:${s.optInt("port")}  ·  ${s.optJSONArray("members")?.length() ?: 0} / ${s.optInt("slots")} players")
        if (s.optBoolean("joined_in_game")) {
            Spacer(Modifier.height(8.dp))
            Hint("You joined while the game runs: go to the game's multiplayer menu and look for the room's quest board.")
        }
        val members = s.optJSONArray("members")
        if (members != null) {
            RowDivider()
            for (i in 0 until members.length()) {
                val m = members.getJSONObject(i)
                Row(Modifier.padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(m.optString("nickname") + if (m.optBoolean("you")) "  (you)" else "", modifier = Modifier.weight(1f), fontSize = 14.sp)
                    val heard = m.optDouble("heard", -1.0)
                    Hint(if (heard < 0) "no game traffic yet" else "heard %.0f s ago".format(heard))
                }
            }
        }
    }

    val invite = s.optString("invite")
    if (s.optBoolean("hosting")) {
        SectionTitle("Invite friends")
        Panel {
            val addresses = s.optJSONArray("addresses")
            if (invite.isNotEmpty()) {
                Text(invite, color = Mh4uColors.Amber, fontSize = 14.sp)
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    QuietButton("Copy") {
                        (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("MH4U invite", invite))
                    }
                    AmberButton("Share") {
                        context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain")
                            .putExtra(Intent.EXTRA_TEXT, "Join my MH4U room: $invite"), "Invite"))
                    }
                }
            }
            if (addresses != null && addresses.length() > 1) {
                RowDivider()
                Hint("Addresses friends can reach this phone at:")
                for (i in 0 until addresses.length()) {
                    val a = addresses.getJSONObject(i)
                    Text("${a.optString("ip")}  (${a.optString("kind")})", fontSize = 13.sp)
                }
            }
            if (s.optString("lobby_note").isNotEmpty()) { Spacer(Modifier.height(6.dp)); Hint(s.optString("lobby_note")) }
        }
    }

    Spacer(Modifier.height(12.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        AmberButton(if (s.optBoolean("hosting")) "Leave" else "Leave room") { Native.roomLeave(false) }
        if (s.optBoolean("hosting")) QuietButton("Close room") { Native.roomLeave(true) }
    }
}

@Composable
private fun OfflinePanels() {
    var invite by remember { mutableStateOf(Prefs.getApp("Multiplayer", "join", "")) }
    var password by remember { mutableStateOf(Prefs.getApp("Multiplayer", "password", "")) }
    var parseError by remember { mutableStateOf("") }

    SectionTitle("Join a room")
    Panel {
        TextRow("Invite code or address", "mh4u://host:port, or host or host:port", invite) { invite = it; Prefs.setApp("Multiplayer", "join", it) }
        TextRow("Password (when the address has none)", null, password, password = true) { password = it; Prefs.setApp("Multiplayer", "password", it) }
        if (parseError.isNotEmpty()) Text(parseError, color = Mh4uColors.Notice, fontSize = 12.sp)
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            AmberButton("Join", enabled = invite.isNotBlank()) {
                val r = JSONObject(Native.parseInvite(invite))
                if (!r.optBoolean("ok")) { parseError = r.optString("error"); return@AmberButton }
                parseError = ""
                Native.roomJoin(r.optString("host"), r.optInt("port"), if (r.optBoolean("code")) r.optString("password") else password)
            }
            val last = Native.roomLast()
            if (last.isNotEmpty()) QuietButton("Rejoin last") { Native.roomRejoin() }
        }
    }

    SectionTitle("Host a room")
    Panel {
        var port by remember { mutableStateOf(Prefs.getInt("Multiplayer", "room_port", 24872).toString()) }
        var name by remember { mutableStateOf(Prefs.getApp("Multiplayer", "room_name", "MH4U")) }
        var slots by remember { mutableFloatStateOf(Prefs.getInt("Multiplayer", "room_members", 4).toFloat()) }
        var pw by remember { mutableStateOf("") }
        var listed by remember { mutableStateOf(Prefs.getBool("Multiplayer", "room_public", false)) }
        TextRow("Room name", null, name) { name = it; Prefs.setApp("Multiplayer", "room_name", it) }
        TextRow("Port", null, port) { port = it.filter(Char::isDigit); port.toIntOrNull()?.let { p -> Prefs.setApp("Multiplayer", "room_port", p.toString()) } }
        TextRow("Room password (optional)", null, pw, password = true) { pw = it }
        SliderRow("Players", "MH4U's quests hold four hunters.", slots, 2f..16f, steps = 13) { slots = it; Prefs.setApp("Multiplayer", "room_members", it.toInt().toString()) }
        ToggleRow("List it in the lobby", "Needs a lobby address and token below.", listed) { listed = it; Prefs.setBool("Multiplayer", "room_public", it) }
        Spacer(Modifier.height(6.dp))
        AmberButton("Host") { Native.roomHost(port.toIntOrNull() ?: 24872, name, slots.toInt(), pw, listed) }
    }

    SectionTitle("Lobby")
    LobbyPanel(onJoin = { host, p, pass -> Native.roomJoin(host, p, pass) })
}

@Composable
internal fun LobbyPanel(onJoin: (String, Int, String) -> Unit) {
    var url by remember { mutableStateOf(Prefs.getApp("Multiplayer", "lobby", "")) }
    var token by remember { mutableStateOf(Prefs.getApp("Multiplayer", "token", "")) }
    var result by remember { mutableStateOf<JSONObject?>(null) }
    var loading by remember { mutableStateOf(false) }
    var refresh by remember { mutableIntStateOf(0) }

    LaunchedEffect(refresh) {
        if (refresh == 0 || url.isBlank()) return@LaunchedEffect
        loading = true
        result = withContext(Dispatchers.IO) { JSONObject(Native.lobbyRooms(url)) }
        loading = false
    }

    Panel {
        TextRow("Lobby address", "A lobby that speaks Azahar's web API.", url) { url = it; Prefs.setApp("Multiplayer", "lobby", it) }
        TextRow("Token", "Only for listing a room you host.", token, password = true) { token = it; Prefs.setApp("Multiplayer", "token", it) }
        Spacer(Modifier.height(6.dp))
        AmberButton(if (loading) "Loading…" else "Show rooms", enabled = url.isNotBlank() && !loading) { refresh++ }
        val r = result
        if (r != null) {
            if (!r.optBoolean("ok")) { Spacer(Modifier.height(8.dp)); Text(r.optString("error"), color = Mh4uColors.Notice, fontSize = 13.sp) }
            val rooms = r.optJSONArray("rooms")
            if (rooms != null) for (i in 0 until rooms.length()) {
                val room = rooms.getJSONObject(i)
                RowDivider()
                Row(Modifier.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(room.optString("name") + if (room.optBoolean("mh4u")) "  ·  MH4U" else "", fontSize = 15.sp)
                        Hint("${room.optString("owner")}  ·  ${room.optJSONArray("players")?.length() ?: 0} / ${room.optInt("max_players")}" +
                            if (room.optBoolean("password")) "  ·  password" else "")
                    }
                    QuietButton("Join") { onJoin(room.optString("address"), room.optInt("port"), Prefs.getApp("Multiplayer", "password", "")) }
                }
            }
        }
    }
}
