export type OutputEvent = {
  jobId: string;
  /** Raw yt-dlp stdout+stderr lines (ffmpeg output included), in order. Download progress and ffmpeg time= lines are coalesced to ~4/s. */
  lines: string[];
};

export type ShareEvent = {
  /** Text shared into the app. Call takeSharedText() to consume it. */
  text: string;
};

export type ViddownEngineEvents = {
  onOutput: (event: OutputEvent) => void;
  onShare: (event: ShareEvent) => void;
};

export type SavedFile = {
  /** content:// URI (MediaStore or SAF document). */
  uri: string;
  name: string;
  size: number;
  mimeType: string;
};

export type StartResult = { files: SavedFile[] };

export type NativeVersions = { ytdlp: string | null; ffmpeg: string | null };

export type UpdateChannel = "stable" | "nightly" | "master";

export type UpdateResult = NativeVersions & { status: "updated" | "up-to-date" };

export type Conditions = {
  metered: boolean;
  powerSave: boolean;
  batteryLevel: number | null;
  charging: boolean;
};

export type PickedFolder = { uri: string; name: string | null };

export type PermissionResult = { granted: boolean; status: string; canAskAgain: boolean };

/**
 * Error codes the native side rejects with (`error.code`):
 * E_INIT, E_YTDLP (message = last output lines), E_CANCELED, E_BUSY, E_UPDATE,
 * E_ARGS, E_NO_OUTPUT, E_STORAGE, E_STORAGE_PERMISSION, E_FOLDER_PERMISSION,
 * E_NO_PICKER, E_NO_APP, E_UNEXPECTED.
 */
export type EngineErrorCode =
  | "E_INIT"
  | "E_YTDLP"
  | "E_CANCELED"
  | "E_BUSY"
  | "E_UPDATE"
  | "E_ARGS"
  | "E_NO_OUTPUT"
  | "E_STORAGE"
  | "E_STORAGE_PERMISSION"
  | "E_FOLDER_PERMISSION"
  | "E_NO_PICKER"
  | "E_NO_APP"
  | "E_UNEXPECTED";
