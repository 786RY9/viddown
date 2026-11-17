import AsyncStorage from "@react-native-async-storage/async-storage";

/** JSON helpers over AsyncStorage (localStorage on web/desktop). Never throw. */
export async function loadJSON<T>(key: string, fallback: T): Promise<T> {
  try {
    const raw = await AsyncStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export async function saveJSON(key: string, value: unknown): Promise<void> {
  try {
    await AsyncStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage full or unavailable (private window): state just won't persist.
  }
}
