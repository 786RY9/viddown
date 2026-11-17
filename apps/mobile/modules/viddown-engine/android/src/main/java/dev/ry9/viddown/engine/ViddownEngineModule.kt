package dev.ry9.viddown.engine

import android.Manifest
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import expo.modules.interfaces.permissions.Permissions
import expo.modules.kotlin.Promise
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.functions.Queues
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

private const val REQUEST_PICK_FOLDER = 0x7D0F

class ViddownEngineModule : Module() {
  private val context: Context
    get() = appContext.reactContext?.applicationContext ?: throw Exceptions.ReactContextLost()

  private var pendingPick: Promise? = null

  // ------------------------------------------------------------ sharing
  // A shared link is parked here until JS takes it, so it is never lost to a
  // listener that isn't mounted yet and never delivered twice.
  private var pendingShare: String? = null
  private var launchIntentChecked = false

  private fun sharedText(intent: Intent?): String? {
    if (intent == null || intent.action != Intent.ACTION_SEND) return null
    if ((intent.flags and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY) != 0) return null
    val text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()
      ?: intent.getStringExtra(Intent.EXTRA_SUBJECT)
    return text?.trim()?.ifEmpty { null }
  }

  private val listener = object : Engine.Listener {
    override fun onOutput(jobId: String, lines: List<String>) {
      sendEvent("onOutput", mapOf("jobId" to jobId, "lines" to lines))
    }
  }

  override fun definition() = ModuleDefinition {
    Name("ViddownEngine")

    Events("onOutput", "onShare")

    OnCreate {
      Engine.attach(context)
      Engine.listener = listener
      Engine.appInForeground = appContext.currentActivity != null
    }

    OnDestroy {
      if (Engine.listener === listener) Engine.listener = null
    }

    OnActivityEntersForeground { Engine.appInForeground = true }
    OnActivityEntersBackground { Engine.appInForeground = false }

    /** App-specific root yt-dlp writes under (one sub-dir per job, see stagingDirFor). */
    Constant("stagingDir") { Storage.stagingRoot(context).absolutePath }

    /** Per-job dir to use as JobSpec.outputDir for yt-dlp; the native side copies out of it. */
    Function("stagingDirFor") { jobId: String -> Storage.jobDir(context, jobId).absolutePath }

    Constant("defaultFolderLabel") { "Download/${Storage.DEFAULT_SUBDIR}" }

    // -------------------------------------------------------------- engine

    /** Unpacks Python/ffmpeg if needed. Optional: every call below does it lazily. */
    AsyncFunction("init") { promise: Promise ->
      Engine.runAsync(promise) { Engine.ensureInit(); null }
    }

    /** Runs yt-dlp with `args` and resolves with stdout (the probe JSON). */
    AsyncFunction("probe") { probeId: String, args: List<String>, promise: Promise ->
      Engine.runAsync(promise) { Engine.probe(probeId, args) }
    }

    /**
     * Starts a download in the foreground service. Output lines arrive as
     * `onOutput` events; the promise settles once the file is saved.
     * `destination` is a SAF tree URI from pickFolder(), or null for Download/viddown.
     */
    AsyncFunction("start") { jobId: String, args: List<String>, title: String, destination: String?, promise: Promise ->
      val dest = destination?.takeIf { it.startsWith("content://") }?.let(Uri::parse)
      Engine.start(jobId, args, title, dest, promise)
    }

    /** Kills a running job or probe. Partial files are kept so a re-run resumes. */
    AsyncFunction("cancel") { id: String -> Engine.cancel(id) }

    /** Removes partial downloads of jobs not in `keep` (and not running). Resolves with bytes freed. */
    AsyncFunction("cleanStaging") { keep: List<String>, promise: Promise ->
      Engine.runAsync(promise) { Engine.cleanStaging(keep) }
    }

    /** Ids of jobs still running natively (e.g. after a JS reload). */
    Function("runningJobs") { Engine.jobs.keys.toList() }

    /** Lets JS (which owns ProgressTracker) drive the notification's bar and text. */
    Function("setJobProgress") { jobId: String, fraction: Double, text: String? ->
      Engine.setProgress(jobId, fraction, text)
    }

    AsyncFunction("versions") { promise: Promise ->
      Engine.runAsync(promise) { Engine.versions() }
    }

    /** channel: "stable" | "nightly" | "master"; version: exact tag (e.g. "2025.10.22") or null for latest. */
    AsyncFunction("selfUpdate") { channel: String, version: String?, promise: Promise ->
      Engine.runAsync(promise) { Engine.selfUpdate(channel, version) }
    }

    /** Signals that drive adaptive --concurrent-fragments. Cheap, synchronous. */
    Function("getConditions") {
      val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
      val pm = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
      val bm = context.getSystemService(Context.BATTERY_SERVICE) as? BatteryManager
      val level = bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)?.takeIf { it in 0..100 }
      val charging = bm?.isCharging == true
      mapOf(
        "metered" to (cm?.isActiveNetworkMetered ?: false),
        "powerSave" to (pm?.isPowerSaveMode ?: false),
        "batteryLevel" to level,
        "charging" to charging,
      )
    }

    /** Asks for POST_NOTIFICATIONS (13+) and, on Android 9 and older, storage write access. */
    AsyncFunction("requestPermissions") { promise: Promise ->
      val wanted = buildList {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) add(Manifest.permission.WRITE_EXTERNAL_STORAGE)
      }
      if (wanted.isEmpty()) {
        promise.resolve(mapOf("granted" to true, "status" to "granted", "canAskAgain" to true, "expires" to "never"))
      } else {
        Permissions.askForPermissionsWithPermissionsManager(appContext.permissions, promise, *wanted.toTypedArray())
      }
    }

    // ------------------------------------------------------------- storage

    /** Opens the system folder picker; resolves { uri, name } with a persisted grant, or null. */
    AsyncFunction("pickFolder") { initialUri: String?, promise: Promise ->
      val activity = appContext.currentActivity ?: throw Exceptions.MissingActivity()
      // A picker left open by a previous JS context is abandoned: settle it as "no choice".
      pendingPick?.resolve(null)
      val intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
        addFlags(
          Intent.FLAG_GRANT_READ_URI_PERMISSION or
            Intent.FLAG_GRANT_WRITE_URI_PERMISSION or
            Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION or
            Intent.FLAG_GRANT_PREFIX_URI_PERMISSION,
        )
        if (initialUri != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
          putExtra(android.provider.DocumentsContract.EXTRA_INITIAL_URI, Uri.parse(initialUri))
        }
      }
      pendingPick = promise
      try {
        activity.startActivityForResult(intent, REQUEST_PICK_FOLDER)
      } catch (e: ActivityNotFoundException) {
        pendingPick = null
        promise.reject("E_NO_PICKER", "This device has no folder picker", e)
      }
    }.runOnQueue(Queues.MAIN)

    OnActivityResult { _, payload ->
      if (payload.requestCode != REQUEST_PICK_FOLDER) return@OnActivityResult
      val promise = pendingPick ?: return@OnActivityResult
      pendingPick = null
      val uri = payload.data?.data
      if (payload.resultCode != Activity.RESULT_OK || uri == null) {
        promise.resolve(null)
        return@OnActivityResult
      }
      try {
        Storage.persistTree(context, uri)
        promise.resolve(mapOf("uri" to uri.toString(), "name" to (Storage.treeName(context, uri) ?: uri.lastPathSegment)))
      } catch (e: SecurityException) {
        promise.reject("E_FOLDER_PERMISSION", "Couldn't keep access to that folder", e)
      }
    }

    /** Drops a persisted folder grant (when the user picks a different folder). */
    Function("releaseFolder") { uri: String -> Storage.releaseTree(context, Uri.parse(uri)) }

    /** True if we still hold write access to a previously picked folder. */
    Function("hasFolderAccess") { uri: String ->
      runCatching { Storage.checkTreeAccess(context, Uri.parse(uri)); true }.getOrDefault(false)
    }

    /** Opens a saved file (content:// URI from a finished job) in another app. */
    AsyncFunction("openFile") { uri: String, promise: Promise ->
      val activity = appContext.currentActivity ?: throw Exceptions.MissingActivity()
      val parsed = Uri.parse(uri)
      val type = context.contentResolver.getType(parsed) ?: Storage.mimeOf(uri)
      val intent = Intent(Intent.ACTION_VIEW).apply {
        setDataAndType(parsed, type)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
      }
      try {
        activity.startActivity(Intent.createChooser(intent, null))
        promise.resolve(null)
      } catch (e: ActivityNotFoundException) {
        promise.reject("E_NO_APP", "No app can open this file", e)
      }
    }.runOnQueue(Queues.MAIN)

    // ------------------------------------------------------------- sharing

    /** Returns the text shared into the app ("Share → viddown") once, then null. */
    Function("takeSharedText") {
      if (!launchIntentChecked) {
        launchIntentChecked = true
        val activity = appContext.currentActivity
        val text = sharedText(activity?.intent)
        if (text != null) {
          // Don't hand the same launch intent out again after a reload.
          activity?.let { a ->
            a.intent = Intent(a.intent).apply {
              action = Intent.ACTION_MAIN
              removeExtra(Intent.EXTRA_TEXT)
            }
          }
          if (pendingShare == null) pendingShare = text
        }
      }
      pendingShare.also { pendingShare = null }
    }

    OnNewIntent { intent ->
      val text = sharedText(intent) ?: return@OnNewIntent
      launchIntentChecked = true
      pendingShare = text
      sendEvent("onShare", mapOf("text" to text))
    }
  }
}
