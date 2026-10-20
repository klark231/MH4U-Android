package org.mh4u.port

import android.content.Context
import java.io.File

/**
 * What Azahar's core asks its Android host for (common/android_utils.h): the root of the user
 * folder, and file operations. The user folder is the app's own external files folder, a real
 * directory, so the core uses plain paths ("vanilla" flavour) and most of these are only here
 * to answer. Paths are relative to the root, as the core writes them; "!" in front means an
 * absolute one.
 *
 * Layout under the root: mh4u-app.ini, mh4u.ini, mh4u-input.ini, games/ (the game file),
 * recomp/<program id>.so (the code library), sdmc/ nand/ (Azahar's own).
 */
object Storage {
    private lateinit var appContext: Context
    private lateinit var rootDir: File

    private const val PREFS = "mh4u_storage"

    fun init(context: Context) {
        appContext = context.applicationContext
        // a folder the person picked on shared storage, if it can still be used
        val chosen = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("root", null)?.let { File(it) }
        rootDir = (chosen?.takeIf { hasAllFilesAccess() && (it.isDirectory || it.mkdirs()) && it.canWrite() }
            ?: defaultRoot).also { it.mkdirs() }
    }

    /** The app's own external files folder: where everything lives unless another is chosen. */
    val defaultRoot: File get() = appContext.getExternalFilesDir(null) ?: appContext.filesDir

    fun hasAllFilesAccess(): Boolean = android.os.Environment.isExternalStorageManager()

    /** Remembers [dir] as the user folder from the next start (null: the default). */
    fun chooseRoot(dir: File?) {
        appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().apply {
            if (dir == null) remove("root") else putString("root", dir.absolutePath)
        }.apply()
    }

    /** The path of a folder picked with the system's folder picker (internal storage or an SD card). */
    fun treeToPath(uri: android.net.Uri): String? = runCatching {
        val id = android.provider.DocumentsContract.getTreeDocumentId(uri)   // "primary:MH4U"
        val volume = id.substringBefore(':')
        val rel = id.substringAfter(':', "")
        val base = if (volume == "primary") android.os.Environment.getExternalStorageDirectory().path else "/storage/$volume"
        if (rel.isEmpty()) base else "$base/$rel"
    }.getOrNull()

    val root: File get() = rootDir
    val games: File get() = File(rootDir, "games").also { it.mkdirs() }
    val recomp: File get() = File(rootDir, "recomp").also { it.mkdirs() }
    /** Where the library runs from: shared storage can't execute. */
    val privateDir: File get() = File(appContext.codeCacheDir, "mh4u").also { it.mkdirs() }

    private fun file(path: String): File =
        if (path.startsWith("!")) File(path.substring(1)) else File(rootDir, path.trimStart('/'))

    // ── called by the core ──

    @JvmStatic fun getUserDirectory(): String? = rootDir.absolutePath
    @JvmStatic fun getBuildFlavor(): String = "vanilla"
    @JvmStatic fun isPortraitMode(): Boolean =
        appContext.resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_PORTRAIT
    @JvmStatic fun isUsingAngleForOpenGL(): Boolean = false

    @JvmStatic fun isDirectory(path: String): Boolean = file(path).isDirectory
    @JvmStatic fun fileExists(path: String): Boolean = file(path).exists()
    @JvmStatic fun getSize(path: String): Long = file(path).length()
    @JvmStatic fun deleteDocument(path: String): Boolean = file(path).deleteRecursively()

    @JvmStatic fun createDir(directory: String, name: String): Boolean =
        File(file(directory), name).let { it.isDirectory || it.mkdirs() }

    @JvmStatic fun createFile(directory: String, name: String): Boolean = runCatching {
        File(file(directory), name).let { it.parentFile?.mkdirs(); it.exists() || it.createNewFile() }
    }.getOrDefault(false)

    @JvmStatic fun openContentUri(path: String, mode: String): Int = -1   // nothing is a content URI here

    @JvmStatic fun getFilesName(path: String): Array<String> =
        file(path).list() ?: emptyArray()

    @JvmStatic fun copyFile(source: String, destinationPath: String, destinationName: String): Boolean =
        runCatching {
            file(source).copyTo(File(file(destinationPath), destinationName), overwrite = true); true
        }.getOrDefault(false)

    @JvmStatic fun renameFile(source: String, newName: String): Boolean =
        file(source).let { it.renameTo(File(it.parentFile, newName)) }

    @JvmStatic fun moveFile(name: String, sourceDir: String, destinationDir: String): Boolean =
        File(file(sourceDir), name).renameTo(File(file(destinationDir), name))

    @JvmStatic fun updateDocumentLocation(source: String, destination: String): Boolean =
        file(source).renameTo(file(destination))
}
