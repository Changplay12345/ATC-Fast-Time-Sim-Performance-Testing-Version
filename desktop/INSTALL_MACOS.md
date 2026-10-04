# Installing the Mac version (test builds)

For Apple Silicon Macs (M1 or later) running macOS 12 or newer.

The test builds are not signed with an Apple Developer certificate, so macOS
treats the app as being from an unidentified developer and blocks the first
launch. The steps below get past that once; after that it opens normally and
updates itself.

## Install

1. Download `ATC-FTS_<version>_macos-arm64.zip` from the Releases page.
2. Double-click the zip. It unpacks to **ATC Fast-Time Simulation Tool.app**.
3. Drag the app into your **Applications** folder. (Do not run it from
   Downloads: macOS runs apps from there in a read-only sandbox, and the
   app could not update itself.)

## First launch

Open the app from Applications. macOS says it "cannot be opened because the
developer cannot be verified" (or "is damaged"). Then:

1. Open **System Settings → Privacy & Security**.
2. Scroll down to the message about the app and click **Open Anyway**.
3. Confirm with your password or Touch ID, and the app opens.

This is needed once per computer. If the warning keeps coming back, or you
prefer the terminal, this removes the block instead:

```
xattr -dr com.apple.quarantine "/Applications/ATC Fast-Time Simulation Tool.app"
```

## Updates

The app checks for updates by itself and shows a notice in the top-right
corner when one exists. Click **Install and restart**; nothing else is needed.
Updated versions do not trigger the warning again.

## If it does not start

The app writes logs to `~/Library/Logs/th.co.bearcat.atcfts/`. In the app,
**About → Export diagnostics** collects them into one file. Send that file to
kruammek@bearcat.co.th.

## Where your files are

Exports and downloaded data live in
`~/Library/Application Support/th.co.bearcat.atcfts/`. They are kept when
the app is updated or removed.
