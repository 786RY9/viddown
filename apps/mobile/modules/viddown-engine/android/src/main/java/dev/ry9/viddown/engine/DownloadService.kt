package dev.ry9.viddown.engine

import android.annotation.SuppressLint
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat

/**
 * Foreground service (type dataSync) that keeps the process alive while any job
 * runs. The work itself happens on Engine's worker threads; this service only
 * owns the ongoing notification and a partial wake lock.
 */
class DownloadService : Service() {
  companion object {
    private const val TAG = "ViddownEngine"

    /** Keep the service around briefly so back-to-back queue items don't flap it. */
    private const val STOP_GRACE_MS = 8_000L
    private const val NOTIFY_INTERVAL_MS = 700L

    private val main = Handler(Looper.getMainLooper())

    @Volatile private var instance: DownloadService? = null
    private var starting = false
    private val stopRunnable = Runnable { instance?.stopIfIdle() }
    private val refreshRunnable = Runnable { instance?.updateNotification() }
    private var lastNotifyAt = 0L

    /** Called by Engine whenever a job is added or removed. */
    internal fun onJobsChanged(ctx: Context) {
      val app = ctx.applicationContext
      main.post {
        if (Engine.jobs.isEmpty()) {
          main.removeCallbacks(stopRunnable)
          main.postDelayed(stopRunnable, STOP_GRACE_MS)
          instance?.updateNotification()
          return@post
        }
        main.removeCallbacks(stopRunnable)
        if (instance != null) {
          instance?.updateNotification()
        } else if (!starting) {
          starting = true
          try {
            ContextCompat.startForegroundService(app, Intent(app, DownloadService::class.java))
          } catch (e: Exception) {
            // Android 12+ refuses FGS starts from the background (e.g. a queued job
            // starting while the app is hidden and the service had already stopped).
            // The job still runs; it just isn't protected from being killed.
            starting = false
            Log.w(TAG, "couldn't start foreground service", e)
          }
        }
      }
    }

    /** Throttled notification refresh (the system drops updates faster than ~5/s). */
    internal fun refresh() {
      main.post {
        val wait = NOTIFY_INTERVAL_MS - (SystemClock.elapsedRealtime() - lastNotifyAt)
        main.removeCallbacks(refreshRunnable)
        if (wait <= 0) instance?.updateNotification() else main.postDelayed(refreshRunnable, wait)
      }
    }
  }

  private var wakeLock: PowerManager.WakeLock? = null

  override fun onBind(intent: Intent?): IBinder? = null

  override fun onCreate() {
    super.onCreate()
    instance = this
    starting = false
    Notifier.ensureChannels(this)
    wakeLock = (getSystemService(Context.POWER_SERVICE) as PowerManager)
      .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "viddown:downloads")
      .apply {
        setReferenceCounted(false)
        acquire(6 * 60 * 60 * 1000L)
      }
  }

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    // Must call startForeground() promptly after startForegroundService(), even if
    // the job already finished, or the system crashes the app.
    try {
      val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0
      ServiceCompat.startForeground(this, Notifier.ONGOING_ID, Notifier.ongoing(this), type)
    } catch (e: Exception) {
      Log.w(TAG, "startForeground failed", e)
      stopSelf()
      return START_NOT_STICKY
    }
    lastNotifyAt = SystemClock.elapsedRealtime()
    if (Engine.jobs.isEmpty()) {
      main.removeCallbacks(stopRunnable)
      main.postDelayed(stopRunnable, STOP_GRACE_MS)
    }
    // Not sticky: if the process dies, the JS queue resumes jobs on next launch.
    return START_NOT_STICKY
  }

  @SuppressLint("MissingPermission")
  private fun updateNotification() {
    lastNotifyAt = SystemClock.elapsedRealtime()
    Notifier.notifyOngoing(this)
  }

  private fun stopIfIdle() {
    if (Engine.jobs.isNotEmpty()) return
    ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
    stopSelf()
  }

  /** Android 15+: dataSync services get ~6h per day. Stop cleanly instead of crashing. */
  override fun onTimeout(startId: Int, fgsType: Int) {
    Log.w(TAG, "foreground service time limit reached")
    ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
    stopSelf()
  }

  override fun onDestroy() {
    main.removeCallbacks(stopRunnable)
    main.removeCallbacks(refreshRunnable)
    if (instance === this) instance = null
    starting = false
    wakeLock?.let { if (it.isHeld) it.release() }
    wakeLock = null
    super.onDestroy()
  }
}
