package dev.ry9.viddown.engine

import android.Manifest
import android.content.ContentResolver
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.DocumentsContract
import android.provider.MediaStore
import android.webkit.MimeTypeMap
import androidx.core.content.ContextCompat
import java.io.File
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Where a finished file ended up. */
internal data class Saved(val uri: String, val name: String, val size: Long, val mimeType: String) {
  fun toMap() = mapOf("uri" to uri, "name" to name, "size" to size.toDouble(), "mimeType" to mimeType)
}

/**
 * yt-dlp writes into an app-specific staging dir (plain file paths, resumable
 * .part files). Finished files are then copied into the user's SAF folder, or
 * into Download/viddown via MediaStore by default.
 */
internal object Storage {
  const val DEFAULT_SUBDIR = "viddown"
  private const val COPY_BUFFER = 1 shl 20
  private val PARTIAL = Regex("""\.(part|ytdl|temp|tmp)$|\.part-Frag\d+|\.f\d+\.\w+$""", RegexOption.IGNORE_CASE)

  fun stagingRoot(ctx: Context): File {
    val base = ctx.getExternalFilesDir(null) ?: ctx.filesDir
    return File(base, "staging").apply { mkdirs() }
  }

  fun jobDir(ctx: Context, jobId: String): File =
    File(stagingRoot(ctx), jobId.replace(Regex("[^A-Za-z0-9._-]"), "_").take(80))

  fun mimeOf(name: String): String {
    val ext = name.substringAfterLast('.', "").lowercase()
    return when (ext) {
      "" -> "application/octet-stream"
      "mkv" -> "video/x-matroska"
      "m4a" -> "audio/mp4"
      "opus" -> "audio/ogg"
      else -> MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext) ?: "application/octet-stream"
    }
  }

  fun checkTreeAccess(ctx: Context, tree: Uri) {
    val ok = ctx.contentResolver.persistedUriPermissions.any { it.uri == tree && it.isWritePermission }
    if (!ok) throw EngineException("E_FOLDER_PERMISSION", "viddown lost access to the download folder. Pick it again in Settings.")
  }

  /** Copies the job's finished files out of staging, then deletes the staging dir. */
  fun export(ctx: Context, job: Job): List<Saved> {
    val root = job.stagingDir.canonicalFile
    val reported = synchronized(job) { job.files.toList() }
      .map { File(it).canonicalFile }
      .filter { it.isFile && it.path.startsWith(root.path + File.separator) }
    val files = reported.ifEmpty {
      root.listFiles()?.filter { it.isFile && !PARTIAL.containsMatchIn(it.name) }.orEmpty()
    }
    if (files.isEmpty()) throw EngineException("E_NO_OUTPUT", "yt-dlp finished but no file was produced")

    val saved = files.map { f ->
      try {
        if (job.destination != null) copyToTree(ctx, f, job.destination) else copyToDownloads(ctx, f)
      } catch (e: EngineException) {
        throw e
      } catch (e: Exception) {
        throw EngineException("E_STORAGE", "Couldn't save ${f.name}: ${e.message}", e)
      }
    }
    root.deleteRecursively()
    return saved
  }

  private fun copy(resolver: ContentResolver, src: File, dest: Uri) {
    val out = resolver.openOutputStream(dest, "w") ?: throw IOException("Can't open $dest for writing")
    out.use { o -> src.inputStream().use { it.copyTo(o, COPY_BUFFER) } }
  }

  private fun copyToTree(ctx: Context, src: File, tree: Uri): Saved {
    checkTreeAccess(ctx, tree)
    val resolver = ctx.contentResolver
    val parent = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
    val mime = mimeOf(src.name)
    // Providers de-duplicate names themselves ("name (1).mp4").
    val doc = DocumentsContract.createDocument(resolver, parent, mime, src.name)
      ?: throw IOException("The folder refused a new file")
    try {
      copy(resolver, src, doc)
    } catch (e: Exception) {
      runCatching { DocumentsContract.deleteDocument(resolver, doc) }
      throw e
    }
    return Saved(doc.toString(), displayName(ctx, doc) ?: src.name, src.length(), mime)
  }

  private fun copyToDownloads(ctx: Context, src: File): Saved {
    val mime = mimeOf(src.name)
    return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
      val resolver = ctx.contentResolver
      val values = ContentValues().apply {
        put(MediaStore.MediaColumns.DISPLAY_NAME, src.name)
        put(MediaStore.MediaColumns.MIME_TYPE, mime)
        put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/$DEFAULT_SUBDIR")
        put(MediaStore.MediaColumns.IS_PENDING, 1)
      }
      val collection = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
      val uri = resolver.insert(collection, values) ?: throw IOException("MediaStore refused the file")
      try {
        copy(resolver, src, uri)
        resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
      } catch (e: Exception) {
        runCatching { resolver.delete(uri, null, null) }
        throw e
      }
      Saved(uri.toString(), displayName(ctx, uri) ?: src.name, src.length(), mime)
    } else {
      val granted = ContextCompat.checkSelfPermission(ctx, Manifest.permission.WRITE_EXTERNAL_STORAGE) ==
        PackageManager.PERMISSION_GRANTED
      if (!granted) throw EngineException("E_STORAGE_PERMISSION", "Allow storage access so viddown can save to Downloads")
      @Suppress("DEPRECATION")
      val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), DEFAULT_SUBDIR)
      dir.mkdirs()
      val target = uniqueFile(dir, src.name)
      src.inputStream().use { i -> target.outputStream().use { i.copyTo(it, COPY_BUFFER) } }
      Saved(scan(ctx, target, mime)?.toString() ?: Uri.fromFile(target).toString(), target.name, target.length(), mime)
    }
  }

  private fun uniqueFile(dir: File, name: String): File {
    var f = File(dir, name)
    val base = name.substringBeforeLast('.')
    val ext = name.substringAfterLast('.', "").let { if (it.isEmpty()) "" else ".$it" }
    var n = 1
    while (f.exists()) f = File(dir, "$base (${n++})$ext")
    return f
  }

  /** Registers a legacy file with MediaStore and returns its content:// URI. */
  private fun scan(ctx: Context, file: File, mime: String): Uri? {
    val latch = CountDownLatch(1)
    var result: Uri? = null
    MediaScannerConnection.scanFile(ctx, arrayOf(file.absolutePath), arrayOf(mime)) { _, uri ->
      result = uri
      latch.countDown()
    }
    latch.await(5, TimeUnit.SECONDS)
    return result
  }

  fun displayName(ctx: Context, uri: Uri): String? = runCatching {
    ctx.contentResolver.query(uri, arrayOf(MediaStore.MediaColumns.DISPLAY_NAME), null, null, null)?.use { c ->
      if (c.moveToFirst()) c.getString(0) else null
    }
  }.getOrNull()

  /** Human name of a picked tree, e.g. "Movies". */
  fun treeName(ctx: Context, tree: Uri): String? = runCatching {
    val doc = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
    ctx.contentResolver.query(doc, arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME), null, null, null)?.use { c ->
      if (c.moveToFirst()) c.getString(0) else null
    }
  }.getOrNull()

  fun persistTree(ctx: Context, tree: Uri) {
    ctx.contentResolver.takePersistableUriPermission(
      tree,
      Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
    )
  }

  fun releaseTree(ctx: Context, tree: Uri) {
    runCatching {
      ctx.contentResolver.releasePersistableUriPermission(
        tree,
        Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
      )
    }
  }
}
