/**
 * Object-storage URLs are created with the host active at upload time. When the
 * app is opened through a different address (LAN, Tailscale, localhost), re-point
 * the asset host at the browser's current host so previews load anywhere.
 */
export function normalizeAssetUrl(url: string | null | undefined): string {
    if (!url) return "";
    if (url.startsWith("data:") || url.startsWith("blob:") || url.startsWith("/")) {
        return url;
    }
    if (typeof window === "undefined") return url;
    try {
        const parsed = new URL(url);
        if (parsed.hostname !== window.location.hostname) {
            parsed.hostname = window.location.hostname;
        }
        return parsed.toString();
    } catch {
        return url;
    }
}
