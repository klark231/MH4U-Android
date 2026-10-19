package org.mh4u.port

import android.app.Activity
import android.app.AlertDialog
import android.text.InputFilter
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import java.util.concurrent.CountDownLatch

/**
 * The 3DS software keyboard (chat, names, quest search) as a dialog. The game's thread waits in
 * [ask] until it is answered, like the console's own applet.
 */
object Keyboard {
    @Volatile var host: Activity? = null

    /** Returns [text, button index as a string]. */
    @JvmStatic
    fun ask(hint: String, maxLength: Int, multiline: Boolean, buttons: Array<String>): Array<String> {
        val activity = host ?: return arrayOf("", "0")
        val latch = CountDownLatch(1)
        var text = ""
        var button = 0
        activity.runOnUiThread {
            val edit = EditText(activity).apply {
                this.hint = hint
                if (maxLength > 0) filters = arrayOf(InputFilter.LengthFilter(maxLength))
                if (multiline) {
                    setSingleLine(false); minLines = 3
                } else {
                    setSingleLine(true); imeOptions = EditorInfo.IME_ACTION_DONE
                }
            }
            val names = if (buttons.isEmpty()) arrayOf("OK") else buttons
            val dialog = AlertDialog.Builder(activity, android.R.style.Theme_Material_Dialog_Alert)
                .setView(edit)
                .setCancelable(false)
                .apply {
                    // 3DS order: the last button is the confirming one (Ok), the first cancels
                    setPositiveButton(names.last()) { _, _ -> text = edit.text.toString(); button = names.size - 1; latch.countDown() }
                    if (names.size >= 2) setNegativeButton(names.first()) { _, _ -> text = edit.text.toString(); button = 0; latch.countDown() }
                    if (names.size >= 3) setNeutralButton(names[1]) { _, _ -> text = edit.text.toString(); button = 1; latch.countDown() }
                }
                .create()
            dialog.show()
            edit.requestFocus()
        }
        latch.await()
        return arrayOf(text, button.toString())
    }

    @JvmStatic
    fun showError(message: String) {
        host?.runOnUiThread { android.widget.Toast.makeText(host, message, android.widget.Toast.LENGTH_LONG).show() }
    }
}
