package dev.ry9.viddown.engine

import android.content.Context
import android.os.Process
import android.util.Log
import java.io.File

internal object Processes {
  private const val TAG = "ViddownEngine"

  /**
   * YoutubeDL.destroyProcessById() only SIGTERMs the Python interpreter, which
   * can leave an ffmpeg child running (merging, HLS). Every process belonging to
   * a job has the job's staging dir on its command line, so kill those too.
   * Only processes of our own uid are visible/killable, which is what we want.
   */
  fun killByArgument(needle: String) {
    if (needle.isEmpty()) return
    val self = Process.myPid()
    val bytes = needle.toByteArray()
    File("/proc").listFiles()?.forEach { dir ->
      val pid = dir.name.toIntOrNull() ?: return@forEach
      if (pid == self) return@forEach
      val cmdline = runCatching { File(dir, "cmdline").readBytes() }.getOrNull() ?: return@forEach
      if (indexOf(cmdline, bytes) >= 0) {
        Log.i(TAG, "killing straggler $pid")
        runCatching { Process.killProcess(pid) }
      }
    }
  }

  private fun indexOf(haystack: ByteArray, needle: ByteArray): Int {
    if (needle.isEmpty() || haystack.size < needle.size) return -1
    outer@ for (i in 0..haystack.size - needle.size) {
      for (j in needle.indices) if (haystack[i + j] != needle[j]) continue@outer
      return i
    }
    return -1
  }

  /** Runs the bundled ffmpeg with -version; mirrors the environment youtubedl-android sets up. */
  fun ffmpegVersion(ctx: Context): String? = runCatching {
    val bin = File(ctx.applicationInfo.nativeLibraryDir, "libffmpeg.so")
    if (!bin.exists()) return@runCatching null
    val packages = File(File(ctx.noBackupFilesDir, "youtubedl-android"), "packages")
    val pb = ProcessBuilder(bin.absolutePath, "-version").redirectErrorStream(true)
    pb.environment()["LD_LIBRARY_PATH"] =
      listOf("python", "ffmpeg").joinToString(":") { File(packages, it).absolutePath + "/usr/lib" }
    val p = pb.start()
    // Output is a few hundred bytes; read it all so ffmpeg exits normally (waitFor(timeout) is API 26+).
    val first = p.inputStream.bufferedReader().use { it.readText() }.lineSequence().firstOrNull()
    p.waitFor()
    // "ffmpeg version n7.1 Copyright (c) ..." -> "n7.1"
    first?.split(' ')?.getOrNull(2)?.trim()?.ifEmpty { null }
  }.onFailure { Log.w(TAG, "ffmpeg -version failed", it) }.getOrNull()
}
