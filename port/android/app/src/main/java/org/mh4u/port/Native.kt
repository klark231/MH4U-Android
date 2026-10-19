package org.mh4u.port

import android.view.Surface

/** The native library (jni/native.cpp): Azahar's core with the game's recompiled code. */
object Native {
    init { System.loadLibrary("mh4u-android") }

    /** Once, before anything else. [privateDir]: where the code library is staged to run. */
    @JvmStatic external fun init(privateDir: String)

    @JvmStatic external fun surfaceChanged(surface: Surface?)
    @JvmStatic external fun surfaceDestroyed()

    /** Runs the game on this thread until it stops. "" for a normal end, else what to tell the player. */
    @JvmStatic external fun run(game: String): String
    @JvmStatic external fun stop()
    @JvmStatic external fun setPaused(paused: Boolean)
    @JvmStatic external fun isRunning(): Boolean

    @JvmStatic external fun touch(x: Int, y: Int, down: Boolean)
    @JvmStatic external fun touchMove(x: Int, y: Int)

    /** top screen l,t,r,b; bottom screen l,t,r,b; window width, height (pixels). */
    @JvmStatic external fun layout(): FloatArray

    /** Re-reads mh4u-app.ini and applies it; [extras]: "key=value" lines (see native.cpp). */
    @JvmStatic external fun applySettings(extras: String)
    /** A game option (mh4u.ini) changed while the game runs. */
    @JvmStatic external fun setGameOption(key: String, value: String): Boolean
    /** What the code library lacks or mismatches, for the player; "" when all is well. */
    @JvmStatic external fun notice(): String
    @JvmStatic external fun toggleQuestMap()
    @JvmStatic external fun optionsPath(): String
    /** game fps, emulation speed in %. */
    @JvmStatic external fun perf(): DoubleArray

    /** Installs a .cia (the game's update); 0 is success. */
    @JvmStatic external fun installCia(path: String): Int

    // ── rooms ──
    @JvmStatic external fun roomJoin(host: String, port: Int, password: String): Boolean
    @JvmStatic external fun roomHost(port: Int, name: String, slots: Int, password: String, listed: Boolean): Boolean
    @JvmStatic external fun roomLeave(close: Boolean): Boolean
    @JvmStatic external fun roomRejoin(): Boolean
    @JvmStatic external fun roomLast(): String
    @JvmStatic external fun roomStatus(): String
    @JvmStatic external fun parseInvite(text: String): String
    @JvmStatic external fun formatInvite(host: String, port: Int, password: String): String
    @JvmStatic external fun validNickname(name: String): Boolean
    @JvmStatic external fun lobbyRooms(url: String): String
}
