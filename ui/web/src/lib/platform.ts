/** Build-time platform: desktop (Tauri static) vs web (browser / Next dev). */

export type Platform = "desktop" | "web";

export function platform(): Platform {
  const raw = (process.env.NEXT_PUBLIC_PLATFORM || "web").toLowerCase();
  return raw === "desktop" ? "desktop" : "web";
}

export function isDesktop(): boolean {
  return platform() === "desktop";
}

export function isWeb(): boolean {
  return platform() === "web";
}

/** True when running inside a Tauri webview (runtime). */
export function isTauriRuntime(): boolean {
  if (typeof window === "undefined") return false;
  return "__TAURI_INTERNALS__" in window || "__TAURI__" in window;
}
