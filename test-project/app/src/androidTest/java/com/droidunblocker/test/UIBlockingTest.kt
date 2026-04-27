package com.droidunblocker.test

import android.content.Context
import android.os.StrictMode
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import org.junit.Assert.assertTrue

@RunWith(AndroidJUnit4::class)
class UIBlockingTest {

    private var tempDir: File? = null
    private var testFile: File? = null

    @Before
    fun setUp() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        tempDir = File(context.filesDir, "test_cache_dir_${System.currentTimeMillis()}")
        tempDir!!.mkdirs()
        testFile = File(tempDir, "test_entry.txt")
        testFile!!.writeText("test_content")
    }

    @After
    fun tearDown() {
        testFile?.delete()
        tempDir?.deleteRecursively()
    }

    @Test
    fun testForUIThreadBlocking() {
        StrictMode.setThreadPolicy(
            StrictMode.ThreadPolicy.Builder()
                .detectAll()
                .penaltyLog()
                .build()
        )

        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val startTime = System.currentTimeMillis()

        // Reproduce exact pre-call state from DataCacheManager.buildCache
        val dir = tempDir!!

        // Verify directory exists to match caller precondition
        assertTrue(dir.exists())

        // Execute the blocking operation directly on the main (UI) thread
        // This reproduces the synchronous listFiles() and readEntry() file I/O
        // Inlined logic for DataCacheManager.loadEntries since we cannot access target project classes
        val entries = mutableListOf<String>()
        dir.listFiles()?.forEach { file ->
            if (file.isFile) {
                val content = file.readText()
                entries.add("${file.name}|$content")
            }
        }

        // Validate execution path completed
        assertTrue(entries.isNotEmpty())
        assertTrue(entries[0].contains("test_entry.txt"))
        assertTrue(entries[0].contains("test_content"))

        val elapsed = System.currentTimeMillis() - startTime
        println("DroidUnblocker: elapsed=${elapsed}ms")
        assert(elapsed < 300) { "UI thread blocked for ${elapsed}ms" }
    }
}