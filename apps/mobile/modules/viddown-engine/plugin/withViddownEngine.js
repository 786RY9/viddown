// Config plugin for the local viddown-engine module (plain JS: no build step).
// Native permissions, the foreground service and its receiver live in the
// module's own AndroidManifest.xml and are merged by Gradle; this plugin only
// touches what must live in the app project.
const {
  AndroidConfig,
  createRunOncePlugin,
  withAndroidManifest,
  withAppBuildGradle,
  withGradleProperties,
} = require("expo/config-plugins");

const MIN_SDK = 24; // youtubedl-android's minSdk
const SPLITS_MARKER = "// @viddown-engine abi-splits";

/** "Share → viddown": accept text/plain (links) on the main activity. */
function withShareIntent(config) {
  return withAndroidManifest(config, (cfg) => {
    const activity = AndroidConfig.Manifest.getMainActivityOrThrow(cfg.modResults);
    const filters = (activity["intent-filter"] = activity["intent-filter"] ?? []);
    const exists = filters.some(
      (f) =>
        f.action?.some((a) => a.$["android:name"] === "android.intent.action.SEND") &&
        f.data?.some((d) => d.$["android:mimeType"] === "text/plain"),
    );
    if (!exists) {
      filters.push({
        action: [{ $: { "android:name": "android.intent.action.SEND" } }],
        category: [{ $: { "android:name": "android.intent.category.DEFAULT" } }],
        data: [{ $: { "android:mimeType": "text/plain" } }],
      });
    }
    return cfg;
  });
}

function setProp(props, key, value) {
  const item = props.find((p) => p.type === "property" && p.key === key);
  if (item) item.value = value;
  else props.push({ type: "property", key, value });
}

/**
 * youtubedl-android executes python/ffmpeg straight from nativeLibraryDir, so
 * native libs must be extracted at install (legacy packaging), and it needs
 * minSdk 24.
 */
function withNativeLibPackaging(config) {
  return withGradleProperties(config, (cfg) => {
    const props = cfg.modResults;
    setProp(props, "expo.useLegacyPackaging", "true");
    const min = props.find((p) => p.type === "property" && p.key === "android.minSdkVersion");
    if (min && Number(min.value) < MIN_SDK) min.value = String(MIN_SDK);
    return cfg;
  });
}

/**
 * Per-ABI APKs (the Python + ffmpeg payload is ~30 MB per ABI). ABIs follow
 * reactNativeArchitectures, so `expo run:android` (which builds only the device
 * ABI) keeps working. Disable for one build with -Pviddown.abiSplits=false.
 */
function withAbiSplits(config, { abiSplits = true, universalApk = true } = {}) {
  if (!abiSplits) return config;
  return withAppBuildGradle(config, (cfg) => {
    if (cfg.modResults.language !== "groovy") {
      throw new Error("with-viddown-engine: expected a Groovy app/build.gradle");
    }
    if (cfg.modResults.contents.includes(SPLITS_MARKER)) return cfg;
    cfg.modResults.contents += `
${SPLITS_MARKER}
def viddownAbis = (findProperty('reactNativeArchitectures') ?: 'armeabi-v7a,arm64-v8a,x86,x86_64')
    .split(',').collect { it.trim() }.findAll { it }
android {
    splits {
        abi {
            enable((findProperty('viddown.abiSplits') ?: 'true').toBoolean())
            reset()
            include(*viddownAbis)
            universalApk ${universalApk ? "true" : "false"}
        }
    }
}
`;
    return cfg;
  });
}

/**
 * @param {import('expo/config-plugins').ExpoConfig} config
 * @param {{ abiSplits?: boolean, universalApk?: boolean }} [props]
 */
function withViddownEngine(config, props = {}) {
  config = withShareIntent(config);
  config = withNativeLibPackaging(config);
  config = withAbiSplits(config, props);
  return config;
}

module.exports = createRunOncePlugin(withViddownEngine, "with-viddown-engine", "0.1.0");
