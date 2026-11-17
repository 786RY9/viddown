import { Linking, StyleSheet, View } from "react-native";

import { Button } from "@/components/Button";
import { SectionTitle } from "@/components/Controls";
import { Pressable } from "@/components/Pressable";
import { Screen } from "@/components/Screen";
import { Text } from "@/components/Text";
import { config } from "@/config";
import { useLayout, useTheme } from "@/theme/theme";
import { fonts, space } from "@/theme/tokens";

const open = (url: string) => () => void Linking.openURL(url);

/** End credits, the way a film lists them: role, then who. */
const CREDITS: { role: string; name: string; url?: string }[] = [
  { role: "Design and engineering", name: config.author, url: config.portfolioUrl },
  { role: "Studio", name: config.studio, url: config.studioUrl },
  { role: "Download engine", name: "yt-dlp", url: "https://github.com/yt-dlp/yt-dlp" },
  { role: "Audio and video processing", name: "FFmpeg", url: "https://ffmpeg.org/" },
  { role: "Android download library", name: "youtubedl-android", url: "https://github.com/JunkFood02/youtubedl-android" },
  { role: "Desktop shell", name: "Tauri", url: "https://tauri.app/" },
  { role: "App framework", name: "Expo and React Native", url: "https://expo.dev/" },
  { role: "Typefaces", name: "Archivo and Archivo Narrow", url: "https://fonts.google.com/specimen/Archivo" },
  { role: "Server", name: "Oracle Cloud, on the free tier" },
];

export default function AboutScreen() {
  const { c } = useTheme();
  const { width } = useLayout();
  const stacked = width < 520;

  return (
    <Screen title="About">
      <Text style={styles.para}>
        viddown saves videos and audio from YouTube, Instagram, TikTok and hundreds of other sites
        straight to your device. It's free, with no ads, no accounts and no tracking, and the code
        is public.
      </Text>
      <View style={styles.row}>
        <Button kind="secondary" icon="external" label="Source code" onPress={open(config.repoUrl)} />
        <Button
          kind="secondary"
          icon="external"
          label="Report a problem"
          onPress={open(`${config.repoUrl}/issues`)}
        />
      </View>

      <View style={[styles.credits, { borderTopColor: c.rule }]}>
        <Text variant="figures" tone="muted" style={styles.center}>
          A Dayframe production
        </Text>
        <Text
          accessibilityRole="header"
          style={[styles.center, { fontFamily: fonts.heavy, fontSize: 36, lineHeight: 40, color: c.ink, letterSpacing: -0.8 }]}
        >
          viddown
        </Text>

        <View style={styles.roll}>
          {CREDITS.map((cr) => (
            <View key={cr.role} style={[styles.credit, stacked && styles.creditStacked]}>
              <Text
                variant="figures"
                tone="muted"
                style={[styles.role, stacked && styles.center]}
              >
                {cr.role}
              </Text>
              {cr.url ? (
                <Pressable
                  accessibilityRole="link"
                  accessibilityLabel={`${cr.role}: ${cr.name}`}
                  onPress={open(cr.url)}
                  style={[styles.nameCol, stacked && styles.nameColStacked]}
                >
                  <Text variant="bodyStrong" style={stacked && styles.center}>
                    {cr.name}
                  </Text>
                </Pressable>
              ) : (
                <View style={[styles.nameCol, stacked && styles.nameColStacked]}>
                  <Text variant="bodyStrong" style={stacked && styles.center}>
                    {cr.name}
                  </Text>
                </View>
              )}
            </View>
          ))}
        </View>

        {/* End of reel: the frame motif, once. */}
        <View style={styles.reelEnd} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
          {Array.from({ length: 7 }, (_, i) => (
            <View key={i} style={[styles.reelFrame, { backgroundColor: c.accent }]} />
          ))}
        </View>

        <Text tone="muted" style={[styles.center, styles.after]}>
          Dayframe designs and builds apps, websites and AI tools for founders and product teams.
          viddown is one of its open-source productions.
        </Text>
        <View style={[styles.row, styles.centerRow]}>
          <Button kind="secondary" label="Develop your brief with Dayframe" onPress={open(config.studioBriefUrl)} />
          <Button kind="quiet" label="Rashid's work" onPress={open(config.portfolioUrl)} />
        </View>
        {config.sponsorsUrl ? (
          <Text variant="small" tone="muted" style={[styles.center, styles.after, { marginTop: space.xl }]}>
            viddown has no ads, no accounts and no tracking. If it's useful to you, you can{" "}
            <Text
              variant="small"
              accessibilityRole="link"
              onPress={open(config.sponsorsUrl)}
              style={{ textDecorationLine: "underline", color: c.ink }}
            >
              sponsor its development on GitHub
            </Text>
            .
          </Text>
        ) : null}
      </View>

      <SectionTitle>Using viddown responsibly</SectionTitle>
      <Text variant="small" tone="muted" style={styles.para}>
        Only download videos you own or have permission to save, and respect each site's terms.
        viddown doesn't bypass DRM or paywalls. If you're a rights holder with a concern, email{" "}
        {config.contactEmail}.
      </Text>
    </Screen>
  );
}

const styles = StyleSheet.create({
  para: { marginBottom: space.md, maxWidth: 620 },
  row: { flexDirection: "row", flexWrap: "wrap", gap: space.sm, marginTop: space.xs },
  centerRow: { justifyContent: "center", marginTop: space.lg },
  center: { textAlign: "center" },
  credits: {
    marginTop: space.xxl,
    paddingTop: space.xxl,
    borderTopWidth: StyleSheet.hairlineWidth * 2,
    maxWidth: 620,
    width: "100%",
  },
  roll: { marginTop: space.xl, gap: space.md },
  credit: { flexDirection: "row", alignItems: "baseline", gap: space.xl },
  creditStacked: { flexDirection: "column", alignItems: "center", gap: 2 },
  role: { flex: 1, textAlign: "right", fontFamily: fonts.figures },
  nameCol: { flex: 1 },
  nameColStacked: { flex: 0, alignItems: "center" },
  reelEnd: {
    flexDirection: "row",
    gap: 3,
    alignSelf: "center",
    marginVertical: space.xxl,
  },
  reelFrame: { width: 4, height: 18, borderRadius: 1 },
  after: { maxWidth: 520, alignSelf: "center" },
});
