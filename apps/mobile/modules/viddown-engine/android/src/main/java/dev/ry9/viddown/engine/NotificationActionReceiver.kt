package dev.ry9.viddown.engine

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Handles the "Cancel" button on the ongoing download notification. */
class NotificationActionReceiver : BroadcastReceiver() {
  companion object {
    const val ACTION_CANCEL = "dev.ry9.viddown.engine.CANCEL"
    const val EXTRA_JOB_ID = "jobId"
  }

  override fun onReceive(context: Context, intent: Intent) {
    if (intent.action != ACTION_CANCEL) return
    val id = intent.getStringExtra(EXTRA_JOB_ID)
    if (id != null) Engine.cancel(id) else Engine.cancelAll()
  }
}
