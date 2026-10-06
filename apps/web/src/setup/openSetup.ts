import { isDesktopRuntime, openDesktopSetupWindow } from "../desktop/bridge";

/** Open first-run setup: its own window on desktop, the in-page dialog in a browser. */
export async function requestSetup(): Promise<void> {
  if (isDesktopRuntime()) {
    try {
      await openDesktopSetupWindow();
      return;
    } catch {
      // An older desktop shell without the setup window: fall back to the dialog.
    }
  }
  window.dispatchEvent(new Event("echospeak:open-setup"));
}
