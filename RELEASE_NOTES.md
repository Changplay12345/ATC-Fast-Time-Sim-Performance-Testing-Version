Version 0.4.1

- Fixed: if the simulation engine could not start, the app stayed running with no window and no message. It now explains what happened and closes.
- If checking for updates or new data fails when the app starts (no connection yet), the app now tries again shortly instead of waiting for the next scheduled check.
- A failed update check is recorded in the log, so support can see why.
- Closing the app on a slow computer is no longer cut short.
