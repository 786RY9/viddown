import { NativeModule, requireOptionalNativeModule } from "expo";

import type {
  Conditions,
  NativeVersions,
  PermissionResult,
  PickedFolder,
  StartResult,
  UpdateChannel,
  UpdateResult,
  ViddownEngineEvents,
} from "./ViddownEngine.types";

declare class ViddownEngineNative extends NativeModule<ViddownEngineEvents> {
  /** App-specific root yt-dlp writes under. */
  readonly stagingDir: string;
  /** Shown in Settings when no folder has been picked. */
  readonly defaultFolderLabel: string;

  /** Per-job staging dir: use it as JobSpec.outputDir when building jobArgs. */
  stagingDirFor(jobId: string): string;
  init(): Promise<void>;
  /** Runs yt-dlp with `args` (probeArgs) and resolves with stdout. Cancel with cancel(probeId). */
  probe(probeId: string, args: string[]): Promise<string>;
  /**
   * Runs a download inside the foreground service. Lines arrive as `onOutput`.
   * Calling again with the id of a job that is still running re-attaches to it.
   * `destination`: SAF tree URI from pickFolder(), or null for Download/viddown.
   */
  start(jobId: string, args: string[], title: string, destination: string | null): Promise<StartResult>;
  cancel(id: string): Promise<boolean>;
  /** Deletes partial downloads of jobs not in `keep` (running jobs are kept). Resolves with bytes freed. */
  cleanStaging(keep: string[]): Promise<number>;
  runningJobs(): string[];
  setJobProgress(jobId: string, fraction: number, text: string | null): void;
  versions(): Promise<NativeVersions>;
  selfUpdate(channel: UpdateChannel, version: string | null): Promise<UpdateResult>;
  getConditions(): Conditions;
  requestPermissions(): Promise<PermissionResult>;
  pickFolder(initialUri: string | null): Promise<PickedFolder | null>;
  releaseFolder(uri: string): void;
  hasFolderAccess(uri: string): boolean;
  openFile(uri: string): Promise<void>;
  /** The text shared into the app via "Share → viddown", once; then null. */
  takeSharedText(): string | null;
}

/** null on web/iOS, or in a build that doesn't include the module (Expo Go). */
const ViddownEngine = requireOptionalNativeModule<ViddownEngineNative>("ViddownEngine");

export default ViddownEngine;
export type { ViddownEngineNative };
