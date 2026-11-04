import test from "node:test";
import assert from "node:assert/strict";
import { notificationStatus, requestBrowserNotifications, sendBrowserNotification } from "../src/lib/browserNotifications.ts";
import type { NotificationHost } from "../src/lib/browserNotifications.ts";

function browser(permission: NotificationPermission = "granted") {
  const notes: FakeNotification[] = [];
  let focused = false;
  class FakeNotification {
    static permission = permission;
    static requests = 0;
    static fail = false;
    static async requestPermission(): Promise<NotificationPermission> {
      this.requests++;
      this.permission = "granted";
      return this.permission;
    }
    onclick: (() => void) | null = null;
    onerror: (() => void) | null = null;
    closed = false;
    title: string;
    options: NotificationOptions;
    constructor(title: string, options: NotificationOptions) {
      this.title = title;
      this.options = options;
      if (FakeNotification.fail) throw new Error("unsupported");
      notes.push(this);
    }
    close() { this.closed = true; }
  }
  const host: NotificationHost = { isSecureContext: true,
    Notification: FakeNotification as unknown as typeof Notification, focus: () => { focused = true; } };
  return { host, notes, FakeNotification, focused: () => focused };
}

test("notifications deliver without requiring a hidden dashboard", () => {
  const b = browser();
  assert.equal(sendBrowserNotification("Warning", "Blanket in zone", undefined, b.host), true);
  assert.equal(b.notes.length, 1);
  assert.equal(b.notes[0].options.body, "Blanket in zone");
  b.notes[0].onclick!();
  assert.equal(b.focused(), true);
  assert.equal(b.notes[0].closed, true);
});

test("pending permission is awaited, then notification delivery can succeed", async () => {
  const b = browser("default");
  assert.equal(sendBrowserNotification("Warning", "Blanket", undefined, b.host), false);
  assert.equal(await requestBrowserNotifications(b.host), "granted");
  assert.equal(b.FakeNotification.requests, 1);
  assert.equal(sendBrowserNotification("Warning", "Blanket", undefined, b.host), true);
});

test("denied permission is reported and is never repeatedly requested", async () => {
  const b = browser("denied");
  assert.equal(await requestBrowserNotifications(b.host), "denied");
  assert.equal(sendBrowserNotification("Warning", "Blanket", undefined, b.host), false);
  assert.equal(b.FakeNotification.requests, 0);
});

test("unsupported and insecure browsers are distinguished", () => {
  assert.equal(notificationStatus({ isSecureContext: true, focus() {} }), "unsupported");
  assert.equal(notificationStatus({ isSecureContext: false, focus() {} }), "insecure");
});

test("constructor and asynchronous delivery errors are surfaced", () => {
  const b = browser();
  const errors: string[] = [];
  b.FakeNotification.fail = true;
  assert.equal(sendBrowserNotification("Warning", "Blanket", message => errors.push(message), b.host), false);
  assert.match(errors[0], /delivery failed/);
  b.FakeNotification.fail = false;
  assert.equal(sendBrowserNotification("Warning", "Blanket", message => errors.push(message), b.host), true);
  b.notes[0].onerror!();
  assert.match(errors[1], /could not display/);
});

test("permission request errors return a visible error status", async () => {
  const b = browser("default");
  b.FakeNotification.requestPermission = async () => { throw new Error("blocked by browser"); };
  assert.equal(await requestBrowserNotifications(b.host), "error");
});
