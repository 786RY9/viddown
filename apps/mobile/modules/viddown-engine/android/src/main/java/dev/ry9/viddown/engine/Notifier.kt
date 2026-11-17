package dev.ry9.viddown.engine

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

internal object Notifier {
  const val ONGOING_ID = 0x7D01
  private const val CHANNEL_PROGRESS = "viddown.downloads"
  private const val CHANNEL_DONE = "viddown.completed"

  fun ensureChannels(ctx: Context) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
    val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
    nm.createNotificationChannel(
      NotificationChannel(CHANNEL_PROGRESS, "Downloads in progress", NotificationManager.IMPORTANCE_LOW).apply {
        description = "Progress of running downloads"
        setShowBadge(false)
      },
    )
    nm.createNotificationChannel(
      NotificationChannel(CHANNEL_DONE, "Finished downloads", NotificationManager.IMPORTANCE_DEFAULT).apply {
        description = "Shown when a download finishes while viddown is in the background"
      },
    )
  }

  private fun canPost(ctx: Context) =
    Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
      ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

  private fun immutable(flags: Int) = flags or PendingIntent.FLAG_IMMUTABLE

  private fun launchIntent(ctx: Context): PendingIntent? {
    val intent = ctx.packageManager.getLaunchIntentForPackage(ctx.packageName)?.apply {
      flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
    } ?: return null
    return PendingIntent.getActivity(ctx, 0, intent, immutable(PendingIntent.FLAG_UPDATE_CURRENT))
  }

  private fun cancelIntent(ctx: Context, jobId: String?): PendingIntent {
    val intent = Intent(ctx, NotificationActionReceiver::class.java).apply {
      action = NotificationActionReceiver.ACTION_CANCEL
      if (jobId != null) putExtra(NotificationActionReceiver.EXTRA_JOB_ID, jobId)
    }
    return PendingIntent.getBroadcast(ctx, 1, intent, immutable(PendingIntent.FLAG_UPDATE_CURRENT))
  }

  fun ongoing(ctx: Context): android.app.Notification {
    val jobs = Engine.jobs.values.toList()
    val single = jobs.singleOrNull()
    val title = when {
      jobs.isEmpty() -> "Downloads finished"
      single != null -> single.title.ifBlank { "Downloading" }
      else -> "Downloading ${jobs.size} items"
    }
    val known = jobs.map { it.fraction }.filter { it >= 0 }
    val text = when {
      jobs.isEmpty() -> null
      single != null -> single.statusText ?: "Starting…"
      else -> jobs.joinToString(" · ") { j -> if (j.fraction >= 0) "${(j.fraction * 100).toInt()}%" else "…" }
    }
    val b = NotificationCompat.Builder(ctx, CHANNEL_PROGRESS)
      .setSmallIcon(android.R.drawable.stat_sys_download)
      .setContentTitle(title)
      .setContentText(text)
      .setOngoing(jobs.isNotEmpty())
      .setOnlyAlertOnce(true)
      .setSilent(true)
      .setShowWhen(false)
      .setCategory(NotificationCompat.CATEGORY_PROGRESS)
      .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
      .setContentIntent(launchIntent(ctx))
    if (jobs.isNotEmpty()) {
      val avg = if (known.isEmpty()) 0.0 else jobs.sumOf { it.fraction.coerceAtLeast(0.0) } / jobs.size
      b.setProgress(1000, (avg * 1000).toInt(), known.isEmpty())
      b.addAction(0, if (single != null) "Cancel" else "Cancel all", cancelIntent(ctx, single?.id))
    }
    return b.build()
  }

  fun notifyOngoing(ctx: Context) {
    if (!canPost(ctx)) return
    try {
      NotificationManagerCompat.from(ctx).notify(ONGOING_ID, ongoing(ctx))
    } catch (_: SecurityException) {
    }
  }

  /** "Saved" notice, only when the user isn't looking at the app. Tapping opens the file. */
  fun completed(ctx: Context, job: Job, saved: Saved?) {
    if (Engine.appInForeground || saved == null || !canPost(ctx)) return
    ensureChannels(ctx)
    val view = Intent(Intent.ACTION_VIEW).apply {
      setDataAndType(Uri.parse(saved.uri), saved.mimeType)
      addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
    }
    val tap = PendingIntent.getActivity(ctx, job.id.hashCode(), view, immutable(PendingIntent.FLAG_UPDATE_CURRENT))
    val n = NotificationCompat.Builder(ctx, CHANNEL_DONE)
      .setSmallIcon(android.R.drawable.stat_sys_download_done)
      .setContentTitle(job.title.ifBlank { saved.name })
      .setContentText("Saved · ${saved.name}")
      .setAutoCancel(true)
      .setContentIntent(tap)
      .build()
    try {
      NotificationManagerCompat.from(ctx).notify("done:${job.id}", ONGOING_ID + 1, n)
    } catch (_: SecurityException) {
    }
  }
}
