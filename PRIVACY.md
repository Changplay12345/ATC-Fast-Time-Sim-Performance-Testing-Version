# Privacy

**ATC Fast-Time Simulation Tool** - BearCat AEL Co
Version 0.2, 2026-10-02. Contact: kruammek@bearcat.co.th

## Desktop app

The desktop app runs the simulation on your own computer. Your flight plans,
generated trajectories, scenarios, exports and logs stay on your computer, in
`%LOCALAPPDATA%\\th.co.bearcat.atcfts` on Windows.

The app contacts the internet for these things only:

| What | Where | What is sent |
|---|---|---|
| Update check | GitHub (the project's releases) | A request for the latest version file. No account, no identifier, no usage data |
| Map background | Esri's public tile servers | The map tiles being viewed, as with any web map (which implies the area you are looking at, and your IP address) |
| Update download | GitHub | Only when you choose to install an update |
| Navigation-data check and download | GitHub (the project's releases) | A request for the data version file, and the data pack if it is newer. No account, no identifier |

The app does **not** send crash reports, usage statistics or any of your data
to BearCat AEL Co or anyone else. If optional crash reporting is added in a
later version it will be off by default, and this document will list exactly
what a report contains before you can switch it on.

To let an update be released to a share of installations first, the app
draws a number from 0 to 99 once and keeps it on your computer ("update
group"). It decides locally whether an update is offered yet. It is never
sent anywhere.

## Web app

The hosted web version sends the flight plans you generate to the project's
API server to compute their trajectories. Results are held in that server's
memory to serve your downloads and are discarded when the server restarts.
Nothing is stored in a database, and there are no accounts or cookies.

## Logs

Log files record what the app and the engine did (start-up, requests,
errors) to help diagnose a problem. They are only ever shared if you send
them: About > Open logs folder.

**About > Export diagnostics** puts those logs and a short summary (app,
engine and data versions, Windows version, number of processors, update
group) into one zip file in your exports folder, so you can attach it to an
email to support. The app does not send it. It contains none of your flight
plans, scenarios or exports, and paths inside your Windows profile are
rewritten to `%USERPROFILE%` so your account name is not in it.
