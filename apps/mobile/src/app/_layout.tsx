import { useEffect, useState } from "react";
import {
  Archivo_400Regular,
  Archivo_500Medium,
  Archivo_600SemiBold,
  Archivo_700Bold,
  Archivo_800ExtraBold,
} from "@expo-google-fonts/archivo";
import { ArchivoNarrow_500Medium, ArchivoNarrow_600SemiBold } from "@expo-google-fonts/archivo-narrow";
import { useFonts } from "expo-font";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { Shell } from "@/components/Shell";
import { getAndroidEngine } from "@/engine";
import { queue } from "@/state/downloads";
import { settingsStore } from "@/state/settings";
import { ThemeProvider, useTheme } from "@/theme/theme";

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Archivo_400Regular,
    Archivo_500Medium,
    Archivo_600SemiBold,
    Archivo_700Bold,
    Archivo_800ExtraBold,
    ArchivoNarrow_500Medium,
    ArchivoNarrow_600SemiBold,
  });
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    // Settings first so the queue starts with the user's concurrency.
    settingsStore
      .hydrate()
      .then(() => queue.setConcurrency(settingsStore.get().concurrency))
      .then(() => queue.hydrate())
      .then(() => {
        // Android keeps partial downloads in app storage so they can resume.
        // Drop the ones no queued, paused or failed job will ever resume.
        const keep = queue.snapshot
          .filter((j) => j.status !== "done" && j.status !== "canceled")
          .map((j) => j.spec.id);
        void getAndroidEngine()?.cleanStaging(keep);
      })
      .finally(() => setHydrated(true));
  }, []);

  const ready = (fontsLoaded || !!fontError) && hydrated;
  useEffect(() => {
    if (ready) void SplashScreen.hideAsync();
  }, [ready]);

  if (!ready) return null;
  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <ThemedStatusBar />
        <Shell />
      </ThemeProvider>
    </SafeAreaProvider>
  );
}

function ThemedStatusBar() {
  const { scheme } = useTheme();
  return <StatusBar style={scheme === "dark" ? "light" : "dark"} />;
}
