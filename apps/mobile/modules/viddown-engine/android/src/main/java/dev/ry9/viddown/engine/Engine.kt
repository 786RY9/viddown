package dev.ry9.viddown.engine

import android.content.Context
import android.net.Uri
import android.os.SystemClock
import android.util.Log
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLException
import com.yausername.youtubedl_android.YoutubeDLRequest
import expo.modules.kotlin.Promise
import java.io.File
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantReadWriteLock

/** Line prefixes produced by @viddown/core jobArgs(). Keep in sync with MARK in ytdlpArgs.ts. */
private const val MARK_PROGRESS = "[vd:dl] "
private const val MARK_FILE = "[vd:file] "

/** Download progress lines are coalesced to at most one per this many ms per job. */
private const val PROGRESS_INTERVAL_MS = 250L

private const val TAIL_LINES = 40

internal class Job(
  val id: String,
  val title: String,
  /** SAF tree URI chosen by the user, or null for MediaStore Download/viddown. */
  val destination: Uri?,
  val stagingDir: File,
) {
  val promises = CopyOnWriteArrayList<Promise>()

  @Volatile var canceled = false

  /** Overall 0..1 progress reported by JS (ProgressTracker), -1 when unknown. */
  @Volatile var fraction = -1.0

  /** Short status reported by JS, e.g. "Video · 12.4 MB/s · 0:42 left". */
  @Volatile var statusText: String? = null

  // Guarded by `this`.
  val files = LinkedHashSet<String>()
  val tail = ArrayDeque<String>()
  var pendingProgress: String? = null
  var lastProgressAt = 0L
}

/**
 * Process-wide engine state. Jobs outlive the JS context (a reload re-attaches
 * by calling start() with the same id), so this is a singleton rather than
 * module state.
 */
internal object Engine {
  private const val TAG = "ViddownEngine"

  interface Listener {
    fun onOutput(jobId: String, lines: List<String>)
  }

  @Volatile var listener: Listener? = null

  /** Tracked by the module so completion notices are only posted when nobody is looking. */
  @Volatile var appInForeground = false

  val jobs = ConcurrentHashMap<String, Job>()

  private lateinit var context: Context
  @Volatile private var initialized = false
  private val initLock = Any()

  /** Jobs/probes hold the read lock; replacing the yt-dlp zip takes the write lock. */
  private val binaryLock = ReentrantReadWriteLock()

  private val workers = Executors.newCachedThreadPool { r ->
    Thread(r, "viddown-worker").apply { isDaemon = true }
  }
  private val ticker = Executors.newSingleThreadScheduledExecutor { r ->
    Thread(r, "viddown-ticker").apply { isDaemon = true }
  }

  @Volatile private var cachedYtdlpVersion: String? = null
  @Volatile private var cachedFfmpegVersion: String? = null

  init {
    ticker.scheduleWithFixedDelay(::flushStaleProgress, PROGRESS_INTERVAL_MS, PROGRESS_INTERVAL_MS, TimeUnit.MILLISECONDS)
  }

  fun attach(ctx: Context) {
    synchronized(initLock) {
      if (!this::context.isInitialized) context = ctx.applicationContext
    }
  }

  /** Unpacks Python/ffmpeg on first run (a few seconds); later calls are free. Blocking. */
  fun ensureInit() {
    if (initialized) return
    synchronized(initLock) {
      if (initialized) return
      try {
        YoutubeDL.init(context)
        FFmpeg.init(context)
      } catch (e: YoutubeDLException) {
        throw EngineException("E_INIT", "Couldn't prepare the download engine: ${e.message}", e)
      }
      initialized = true
    }
  }

  fun runAsync(promise: Promise, block: () -> Any?) {
    workers.execute {
      try {
        promise.resolve(block())
      } catch (e: EngineException) {
        promise.reject(e.code, e.message, e)
      } catch (e: Throwable) {
        promise.reject("E_UNEXPECTED", e.message ?: e.javaClass.simpleName, e)
      }
    }
  }

  private fun newRequest(args: List<String>): YoutubeDLRequest {
    // An explicit --cache-dir stops the library from adding --no-cache-dir, so
    // YouTube player/signature data survives between runs (faster starts).
    val cache = File(context.cacheDir, "yt-dlp").apply { mkdirs() }
    return YoutubeDLRequest(emptyList<String>())
      .addOption("--cache-dir", cache.absolutePath)
      .addCommands(args)
  }

  // ---------------------------------------------------------------- probe

  /** Runs yt-dlp with `args` (from probeArgs) and returns stdout. Blocking. */
  fun probe(probeId: String, args: List<String>): String {
    ensureInit()
    binaryLock.readLock().lock()
    try {
      val response = YoutubeDL.execute(newRequest(args), probeId, false, null)
      return response.out
    } catch (e: YoutubeDL.CanceledException) {
      throw EngineException("E_CANCELED", "Canceled")
    } catch (e: YoutubeDLException) {
      throw EngineException("E_YTDLP", e.message?.trim().takeUnless { it.isNullOrEmpty() } ?: "yt-dlp failed")
    } finally {
      binaryLock.readLock().unlock()
    }
  }

  // ------------------------------------------------------------- download

  /**
   * Starts (or re-attaches to) a download. `promise` settles when the file has
   * been copied to its destination.
   */
  fun start(jobId: String, args: List<String>, title: String, destination: Uri?, promise: Promise) {
    val fresh = Job(jobId, title, destination, Storage.jobDir(context, jobId))
    val job = jobs.putIfAbsent(jobId, fresh)
    if (job != null) {
      job.promises.add(promise) // already running: re-attach (e.g. after a JS reload)
      return
    }
    fresh.promises.add(promise)
    DownloadService.onJobsChanged(context)
    workers.execute { run(fresh, args) }
  }

  private fun run(job: Job, args: List<String>) {
    var result: Any? = null
    var error: EngineException? = null
    try {
      ensureInit()
      job.stagingDir.mkdirs()
      if (job.destination != null) Storage.checkTreeAccess(context, job.destination)
      binaryLock.readLock().lock()
      try {
        if (job.canceled) throw YoutubeDL.CanceledException()
        YoutubeDL.execute(newRequest(args), job.id, true) { _, _, line -> onLine(job, line) }
      } finally {
        binaryLock.readLock().unlock()
      }
      flush(job)
      if (job.canceled) throw YoutubeDL.CanceledException()
      job.statusText = "Saving…"
      DownloadService.refresh()
      val saved = Storage.export(context, job)
      Notifier.completed(context, job, saved.firstOrNull())
      result = mapOf("files" to saved.map { it.toMap() })
    } catch (e: YoutubeDL.CanceledException) {
      error = EngineException("E_CANCELED", "Canceled")
    } catch (e: YoutubeDLException) {
      error = if (job.canceled) {
        EngineException("E_CANCELED", "Canceled")
      } else {
        // stderr is merged into stdout, so the message is in our tail buffer.
        val tail = synchronized(job) { job.tail.joinToString("\n") }
        EngineException("E_YTDLP", tail.ifEmpty { e.message ?: "yt-dlp failed" })
      }
    } catch (e: InterruptedException) {
      error = EngineException("E_CANCELED", "Canceled")
    } catch (e: EngineException) {
      error = e
    } catch (e: Throwable) {
      Log.e(TAG, "job ${job.id} failed", e)
      error = EngineException("E_UNEXPECTED", e.message ?: e.javaClass.simpleName, e)
    } finally {
      flush(job)
      jobs.remove(job.id, job)
      DownloadService.onJobsChanged(context)
    }
    for (p in job.promises) {
      if (error != null) p.reject(error.code, error.message, error) else p.resolve(result)
    }
  }

  private fun onLine(job: Job, raw: String) {
    val line = raw.trimEnd()
    if (line.isBlank()) return
    var out: List<String>? = null
    synchronized(job) {
      if (line.startsWith(MARK_FILE)) job.files.add(line.substring(MARK_FILE.length))
      // Download progress and ffmpeg status lines ("frame=… time=00:00:12.34 …",
      // printed while cutting clips) are both "latest wins": coalesce them.
      val isProgress = (line.startsWith(MARK_PROGRESS) && line.contains("\"downloading\"")) || isFfmpegStatus(line)
      if (isProgress) {
        val now = SystemClock.elapsedRealtime()
        if (now - job.lastProgressAt >= PROGRESS_INTERVAL_MS) {
          job.lastProgressAt = now
          job.pendingProgress = null
          out = listOf(line)
        } else {
          job.pendingProgress = line
        }
      } else {
        if (!line.startsWith(MARK_PROGRESS) && !isFfmpegStatus(line)) {
          job.tail.addLast(line)
          while (job.tail.size > TAIL_LINES) job.tail.removeFirst()
        }
        val pending = job.pendingProgress
        job.pendingProgress = null
        out = if (pending != null) listOf(pending, line) else listOf(line)
      }
    }
    out?.let { emit(job.id, it) }
  }

  private fun isFfmpegStatus(line: String) =
    line.contains("time=") && (line.startsWith("frame=") || line.startsWith("size="))

  private fun flush(job: Job) {
    val pending = synchronized(job) {
      job.pendingProgress.also {
        job.pendingProgress = null
        if (it != null) job.lastProgressAt = SystemClock.elapsedRealtime()
      }
    }
    if (pending != null) emit(job.id, listOf(pending))
  }

  private fun flushStaleProgress() {
    val now = SystemClock.elapsedRealtime()
    for (job in jobs.values) {
      val due = synchronized(job) { job.pendingProgress != null && now - job.lastProgressAt >= PROGRESS_INTERVAL_MS }
      if (due) flush(job)
    }
  }

  private fun emit(jobId: String, lines: List<String>) {
    try {
      listener?.onOutput(jobId, lines)
    } catch (e: Throwable) {
      Log.w(TAG, "dropping output for $jobId", e)
    }
  }

  fun setProgress(jobId: String, fraction: Double, text: String?) {
    val job = jobs[jobId] ?: return
    job.fraction = fraction.coerceIn(-1.0, 1.0)
    job.statusText = text
    DownloadService.refresh()
  }

  /** Kills the yt-dlp process (and its ffmpeg children). Partial files stay for --continue. */
  fun cancel(id: String): Boolean {
    val job = jobs[id]
    job?.canceled = true
    val killed = YoutubeDL.destroyProcessById(id)
    if (job != null) {
      Processes.killByArgument(job.stagingDir.absolutePath)
      // The process may not be registered yet (still initialising); retry briefly.
      if (!killed) {
        var attempts = 0
        lateinit var retry: Runnable
        retry = Runnable {
          if (jobs[id] === job && attempts++ < 20) {
            if (!YoutubeDL.destroyProcessById(id)) ticker.schedule(retry, 250, TimeUnit.MILLISECONDS)
            else Processes.killByArgument(job.stagingDir.absolutePath)
          }
        }
        ticker.schedule(retry, 250, TimeUnit.MILLISECONDS)
      }
    }
    return job != null || killed
  }

  fun cancelAll() {
    for (id in jobs.keys.toList()) cancel(id)
  }

  /**
   * Deletes staging dirs (partial downloads) of jobs that will never resume.
   * `keep` = ids of queued/paused/failed jobs; running jobs are always kept.
   * Returns the number of bytes freed.
   */
  fun cleanStaging(keep: List<String>): Double {
    val keepNames = (keep + jobs.keys).map { Storage.jobDir(context, it).name }.toSet()
    var freed = 0L
    Storage.stagingRoot(context).listFiles()?.forEach { dir ->
      if (dir.name in keepNames) return@forEach
      freed += dir.walkBottomUp().filter { it.isFile }.sumOf { it.length() }
      dir.deleteRecursively()
    }
    return freed.toDouble()
  }

  // ------------------------------------------------------- versions/update

  fun versions(): Map<String, String?> {
    ensureInit()
    if (cachedYtdlpVersion == null) {
      binaryLock.readLock().lock()
      try {
        val r = YoutubeDL.execute(YoutubeDLRequest(emptyList<String>()).addOption("--version"), null, false, null)
        cachedYtdlpVersion = r.out.trim().lineSequence().firstOrNull()?.trim()?.ifEmpty { null }
      } catch (e: Exception) {
        Log.w(TAG, "yt-dlp --version failed", e)
        cachedYtdlpVersion = YoutubeDL.versionName(context)
      } finally {
        binaryLock.readLock().unlock()
      }
    }
    if (cachedFfmpegVersion == null) cachedFfmpegVersion = Processes.ffmpegVersion(context)
    return mapOf("ytdlp" to cachedYtdlpVersion, "ffmpeg" to cachedFfmpegVersion)
  }

  private val VERSION_RE = Regex("^\\d{4}\\.\\d{2}\\.\\d{2}(\\.\\d+)?$")

  /**
   * Replaces the yt-dlp zip. `version` pins to a release tag (the server-validated
   * one from the manifest); null takes the channel's latest.
   */
  fun selfUpdate(channel: String, version: String?): Map<String, Any?> {
    ensureInit()
    val repo = when (channel) {
      "nightly" -> "yt-dlp/yt-dlp-nightly-builds"
      "master" -> "yt-dlp/yt-dlp-master-builds"
      "stable" -> "yt-dlp/yt-dlp"
      else -> throw EngineException("E_ARGS", "Unknown channel '$channel'")
    }
    val updateChannel = if (version.isNullOrEmpty()) {
      when (channel) {
        "nightly" -> YoutubeDL.UpdateChannel.NIGHTLY
        "master" -> YoutubeDL.UpdateChannel.MASTER
        else -> YoutubeDL.UpdateChannel.STABLE
      }
    } else {
      if (!VERSION_RE.matches(version)) throw EngineException("E_ARGS", "Bad yt-dlp version '$version'")
      YoutubeDL.UpdateChannel("https://api.github.com/repos/$repo/releases/tags/$version")
    }
    // The yt-dlp zip is read lazily by running interpreters, so never swap it under them.
    if (!binaryLock.writeLock().tryLock(2, TimeUnit.SECONDS)) {
      throw EngineException("E_BUSY", "Downloads are running; update yt-dlp when they finish")
    }
    val status = try {
      YoutubeDL.updateYoutubeDL(context, updateChannel)
    } catch (e: YoutubeDLException) {
      throw EngineException("E_UPDATE", e.cause?.message ?: e.message ?: "Update failed", e)
    } finally {
      cachedYtdlpVersion = null
      binaryLock.writeLock().unlock()
    }
    val v = versions()
    return mapOf(
      "status" to if (status == YoutubeDL.UpdateStatus.ALREADY_UP_TO_DATE) "up-to-date" else "updated",
      "ytdlp" to v["ytdlp"],
      "ffmpeg" to v["ffmpeg"],
    )
  }
}

internal class EngineException(val code: String, message: String, cause: Throwable? = null) :
  Exception(message, cause)
