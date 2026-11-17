import { useEffect } from "react";
import { router } from "expo-router";

import { addSharedUrlListener, takeSharedUrl } from "@/engine/android";

/**
 * "Share → viddown" from another app (Android). Opens the Download tab with
 * the link filled in and looked up. No-op on web, desktop and iOS.
 */
export function ShareIntentHandler() {
  useEffect(() => {
    const open = (url: string) => router.navigate({ pathname: "/", params: { url } });
    const first = takeSharedUrl(); // link that launched the app (cold start)
    if (first) open(first);
    const sub = addSharedUrlListener(open); // links shared while the app is running
    return () => sub.remove();
  }, []);
  return null;
}
