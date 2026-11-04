export type NotificationStatus = NotificationPermission | "unsupported" | "insecure" | "error";

export interface NotificationHost {
  isSecureContext: boolean;
  Notification?: typeof Notification;
  focus(): void;
}

export function notificationStatus(host: NotificationHost = window): NotificationStatus {
  if (!host.isSecureContext) return "insecure";
  if (!host.Notification) return "unsupported";
  return host.Notification.permission;
}

export async function requestBrowserNotifications(host: NotificationHost = window): Promise<NotificationStatus> {
  const status = notificationStatus(host);
  if (status !== "default") return status;
  try {
    return await host.Notification!.requestPermission();
  } catch {
    return "error";
  }
}

export function sendBrowserNotification(title: string, body: string,
  onError: (message: string) => void = () => undefined, host: NotificationHost = window): boolean {
  if (notificationStatus(host) !== "granted") return false;
  try {
    // Show in the foreground too. A visible dashboard must not silently consume the episode.
    const note = new host.Notification!(title, { body, tag: "three-feet-warning", requireInteraction: true });
    note.onclick = () => { host.focus(); note.close(); };
    note.onerror = () => onError("The browser could not display a notification. Check browser and Windows notification settings.");
    return true;
  } catch {
    onError("Notification delivery failed. Try this page in Chrome or Edge and check site permissions.");
    return false;
  }
}

export function notificationHint(status: NotificationStatus): string {
  switch (status) {
    case "granted": return "Browser notifications allowed. Use Test notification to check delivery.";
    case "denied": return "Notifications are blocked. Open this site's browser permissions, allow Notifications, then test again.";
    case "default": return "Allow notifications when the browser asks. Permission applies separately to localhost and 127.0.0.1.";
    case "unsupported": return "This browser does not support desktop notifications. Open the dashboard in Chrome or Edge.";
    case "insecure": return "Notifications need localhost, 127.0.0.1, or HTTPS. Open the dashboard at http://127.0.0.1:8000/.";
    case "error": return "The browser could not request notification permission. Check this site's browser permissions.";
  }
}
