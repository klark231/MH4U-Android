package org.mh4u.port

import android.app.Application

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Storage.init(this)
        Native.init(Storage.privateDir.absolutePath)
        MH4UInput.init(Storage.root)
    }
}
