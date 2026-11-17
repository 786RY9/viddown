import { Platform } from "react-native";

/**
 * Deployment settings. Change these in one place.
 */
const utm = (content: string) =>
  `utm_source=viddown&utm_medium=app&utm_campaign=credits&utm_content=${content}-${Platform.OS}`;

export const config = {
  /** viddown server on Oracle Always-Free (see server/README.md). */
  apiBase: process.env.EXPO_PUBLIC_API_BASE ?? "https://viddown-ry9.duckdns.org",
  /** Public repository, used for releases and the source link. */
  repoUrl: process.env.EXPO_PUBLIC_REPO_URL ?? "https://github.com/786RY9/viddown",
  /** Takedown requests. */
  contactEmail: "rashidyaseen5484@gmail.com",

  // ── Credits ────────────────────────────────────────────────────────────
  author: "Rashid Yaseen",
  studio: "Dayframe",
  /** Tagged so Dayframe's analytics can tell which visitors came from viddown. */
  studioUrl: `https://dayframe.studio/?${utm("studio")}`,
  studioBriefUrl: `https://dayframe.studio/?${utm("brief")}#brief`,
  portfolioUrl: `https://ryaseen.studio/?${utm("portfolio")}`,
  /** GitHub Sponsors page. Empty until approved; the support line stays hidden. */
  sponsorsUrl: process.env.EXPO_PUBLIC_SPONSORS_URL ?? "",
} as const;
