//! Desktop shell.
//!
//! On launch it starts the packaged Python engine as a hidden child process on
//! `127.0.0.1`, on a port picked at that moment and guarded by a token
//! generated for this session; waits until the engine answers its health
//! check; then opens the window on the bundled front end, telling the page
//! where the engine is through `window.__APP_CONFIG__`. On exit it stops the
//! engine. Nothing outside this machine — and no other page on it — can use
//! the engine.

use std::{
    env, fs,
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
};

use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

/// How long the engine gets to come up (cold disk, antivirus scan of a fresh
/// install…) before the shell gives up and says so.
const ENGINE_START_TIMEOUT: Duration = Duration::from_secs(90);
const APP_VERSION: &str = env!("CARGO_PKG_VERSION");
/// Origins the shell's own page can have (Windows / macOS+Linux). The engine
/// allows exactly these and nothing else.
const PAGE_ORIGINS: &str = "http://tauri.localhost,https://tauri.localhost,tauri://localhost";

/// The running engine, and where its PID is recorded.
struct Engine {
    child: Mutex<Option<Child>>,
    pid_file: PathBuf,
}

/// A `Command` that never flashes a console window on Windows.
fn hidden(program: impl AsRef<std::ffi::OsStr>) -> Command {
    #[allow(unused_mut)]
    let mut cmd = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

fn engine_file_name() -> &'static str {
    if cfg!(windows) {
        "atc-engine.exe"
    } else {
        "atc-engine"
    }
}

/// The engine executable: inside the app's resources once installed, or the
/// PyInstaller output folder in the repo while developing.
fn engine_exe(app: &tauri::AppHandle) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(res) = app.path().resource_dir() {
        candidates.push(res.join("engine").join(engine_file_name()));
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            // desktop/src-tauri/target/<profile>/… -> desktop/sidecar/dist/…
            candidates.push(
                dir.join("../../../sidecar/dist/atc-engine")
                    .join(engine_file_name()),
            );
        }
    }
    candidates.into_iter().find(|p| p.is_file())
}

/// A port nothing is using right now. (There is a small window before the
/// engine binds it; a collision makes the health check fail, which is
/// reported like any other start failure.)
fn free_port() -> Result<u16, String> {
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .map_err(|e| format!("no free local port: {e}"))
}

/// 32 random bytes, hex-encoded: the session's bearer token.
fn session_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).map_err(|e| format!("no system randomness: {e}"))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// Minimal HTTP/1.0 GET against the local engine → (status, body).
fn http_get(port: u16, path: &str, timeout: Duration) -> Option<(u16, String)> {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let mut stream = TcpStream::connect_timeout(&addr, timeout).ok()?;
    stream.set_read_timeout(Some(timeout)).ok()?;
    stream.set_write_timeout(Some(timeout)).ok()?;
    write!(
        stream,
        "GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
    )
    .ok()?;
    let mut raw = String::new();
    stream.read_to_string(&mut raw).ok()?;
    let status = raw.split_whitespace().nth(1)?.parse().ok()?;
    let body = raw.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
    Some((status, body))
}

/// Stop an engine left behind by a previous run that crashed or was killed:
/// it would otherwise sit on its memory until the next reboot. The PID is only
/// trusted if that process really is an engine (PIDs get reused).
fn sweep_orphan(pid_file: &Path) {
    let Ok(text) = fs::read_to_string(pid_file) else {
        return;
    };
    let _ = fs::remove_file(pid_file);
    let Ok(pid) = text.trim().parse::<u32>() else {
        return;
    };
    #[cfg(windows)]
    {
        let listed = hidden("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
            .output();
        if let Ok(out) = listed {
            let text = String::from_utf8_lossy(&out.stdout).to_ascii_lowercase();
            if text.contains("atc-engine.exe") {
                log::warn!("stopping engine left over from a previous run (pid {pid})");
                let _ = hidden("taskkill")
                    .args(["/PID", &pid.to_string(), "/T", "/F"])
                    .output();
            }
        }
    }
    #[cfg(unix)]
    {
        let listed = Command::new("ps")
            .args(["-p", &pid.to_string(), "-o", "comm="])
            .output();
        if let Ok(out) = listed {
            if String::from_utf8_lossy(&out.stdout).contains("atc-engine") {
                log::warn!("stopping engine left over from a previous run (pid {pid})");
                let _ = Command::new("kill").arg(pid.to_string()).output();
            }
        }
    }
}

struct Started {
    child: Child,
    port: u16,
    token: String,
}

fn start_engine(app: &tauri::AppHandle, data_dir: &Path, pid_file: &Path) -> Result<Started, String> {
    let exe = engine_exe(app).ok_or("The simulation engine is missing from the installation.")?;
    let log_dir = app
        .path()
        .app_log_dir()
        .map_err(|e| format!("no log folder: {e}"))?;
    fs::create_dir_all(&log_dir).map_err(|e| format!("cannot create {}: {e}", log_dir.display()))?;
    let exports = data_dir.join("exports");
    fs::create_dir_all(&exports).map_err(|e| format!("cannot create {}: {e}", exports.display()))?;
    let log_path = log_dir.join("engine.log");
    let log = fs::File::create(&log_path).map_err(|e| format!("cannot open engine.log: {e}"))?;
    let log_err = log.try_clone().map_err(|e| e.to_string())?;

    let port = free_port()?;
    let token = session_token()?;

    let mut cmd = hidden(&exe);
    cmd.current_dir(exe.parent().expect("engine exe has a parent folder"))
        .env("ATC_LOCAL_MODE", "1")
        .env("ATC_BIND", "127.0.0.1")
        .env("ATC_PORT", port.to_string())
        .env("ATC_SESSION_TOKEN", &token)
        // The engine watches this process and exits if it disappears, so a
        // crash — or an installer closing the app to update it — never leaves
        // an engine holding its files open.
        .env("ATC_PARENT_PID", std::process::id().to_string())
        .env("ATC_OUT_DIR", &exports)
        .env("WEB_ORIGIN", PAGE_ORIGINS)
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(log_err));
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("The simulation engine could not be started: {e}"))?;
    let _ = fs::write(pid_file, child.id().to_string());

    let started = Instant::now();
    loop {
        if let Some((200, body)) = http_get(port, "/api/health", Duration::from_secs(2)) {
            let engine_version = serde_json::from_str::<serde_json::Value>(&body)
                .ok()
                .and_then(|v| v.get("version").and_then(|x| x.as_str()).map(str::to_owned))
                .unwrap_or_default();
            log::info!(
                "engine {engine_version} up on port {port} after {:?}",
                started.elapsed()
            );
            if engine_version != APP_VERSION {
                // A half-applied update or a dev build; say so in the log.
                log::warn!("engine version {engine_version} != shell version {APP_VERSION}");
            }
            return Ok(Started { child, port, token });
        }
        if let Ok(Some(status)) = child.try_wait() {
            let _ = fs::remove_file(pid_file);
            return Err(format!(
                "The simulation engine stopped while starting ({status}).\n\nDetails: {}",
                log_path.display()
            ));
        }
        if started.elapsed() > ENGINE_START_TIMEOUT {
            let _ = child.kill();
            let _ = child.wait();
            let _ = fs::remove_file(pid_file);
            return Err(format!(
                "The simulation engine did not respond within {} seconds.\n\nDetails: {}",
                ENGINE_START_TIMEOUT.as_secs(),
                log_path.display()
            ));
        }
        std::thread::sleep(Duration::from_millis(150));
    }
}

fn stop_engine(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<Engine>() {
        if let Ok(mut guard) = state.child.lock() {
            if let Some(mut child) = guard.take() {
                // Killing the main process is enough: its worker processes
                // watch their parent and exit on their own.
                let _ = child.kill();
                let _ = child.wait();
            }
        }
        let _ = fs::remove_file(&state.pid_file);
    }
}

/// A blocking native error box. Shown from its own thread: a message box must
/// not block the event-loop thread the setup hook runs on.
fn fatal(app: &tauri::AppHandle, message: String) {
    log::error!("{message}");
    let handle = app.clone();
    let _ = std::thread::spawn(move || {
        handle
            .dialog()
            .message(message)
            .title("ATC Fast-Time Simulation Tool")
            .kind(MessageDialogKind::Error)
            .blocking_show();
    })
    .join();
}

// ---------------------------------------------------------------------------
// Commands the page calls (About dialog): app info, folders, updates.
// ---------------------------------------------------------------------------

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct AppInfo {
    version: String,
    data_dir: String,
    log_dir: String,
    exports_dir: String,
}

fn dirs(app: &tauri::AppHandle) -> Result<(PathBuf, PathBuf, PathBuf), String> {
    let data = app.path().app_local_data_dir().map_err(|e| e.to_string())?;
    let logs = app.path().app_log_dir().map_err(|e| e.to_string())?;
    let exports = data.join("exports");
    Ok((data, logs, exports))
}

#[tauri::command]
fn app_info(app: tauri::AppHandle) -> Result<AppInfo, String> {
    let (data, logs, exports) = dirs(&app)?;
    Ok(AppInfo {
        version: APP_VERSION.to_string(),
        data_dir: data.display().to_string(),
        log_dir: logs.display().to_string(),
        exports_dir: exports.display().to_string(),
    })
}

/// Show a folder in the system file manager.
fn reveal(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path).map_err(|e| e.to_string())?;
    let program = if cfg!(windows) {
        "explorer"
    } else if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    };
    Command::new(program)
        .arg(path)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("cannot open {}: {e}", path.display()))
}

#[tauri::command]
fn open_logs_folder(app: tauri::AppHandle) -> Result<(), String> {
    reveal(&dirs(&app)?.1)
}

#[tauri::command]
fn open_exports_folder(app: tauri::AppHandle) -> Result<(), String> {
    reveal(&dirs(&app)?.2)
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateCheck {
    available: bool,
    version: Option<String>,
    notes: Option<String>,
    date: Option<String>,
}

/// Ask the update server whether a newer signed release exists. Only reports;
/// nothing is downloaded until the user asks.
#[tauri::command]
async fn check_update(app: tauri::AppHandle) -> Result<UpdateCheck, String> {
    let updater = app.updater().map_err(|e| e.to_string())?;
    match updater.check().await.map_err(|e| e.to_string())? {
        Some(update) => {
            log::info!("update available: {} -> {}", APP_VERSION, update.version);
            Ok(UpdateCheck {
                available: true,
                version: Some(update.version.clone()),
                notes: update.body.clone(),
                date: update.date.map(|d| d.to_string()),
            })
        }
        None => Ok(UpdateCheck {
            available: false,
            version: None,
            notes: None,
            date: None,
        }),
    }
}

/// Download the update (its signature is verified against the public key
/// built into this app), stop the engine, and install. On Windows the
/// installer takes over and the app exits; elsewhere the app restarts itself.
#[tauri::command]
async fn install_update(app: tauri::AppHandle) -> Result<(), String> {
    let for_exit = app.clone();
    let updater = app
        .updater_builder()
        // Windows: runs just before the app exits to let the installer work.
        // The engine must be gone by then or its files cannot be replaced.
        .on_before_exit(move || stop_engine(&for_exit))
        .build()
        .map_err(|e| e.to_string())?;
    let Some(update) = updater.check().await.map_err(|e| e.to_string())? else {
        return Err("No update is available.".into());
    };
    log::info!("installing update {}", update.version);

    let progress = app.clone();
    let mut downloaded: u64 = 0;
    let mut last_emit: u64 = 0;
    update
        .download_and_install(
            move |chunk, total| {
                downloaded += chunk as u64;
                // A progress event per network chunk would flood the page.
                if downloaded - last_emit >= 512 * 1024 || Some(downloaded) == total {
                    last_emit = downloaded;
                    let _ = progress.emit(
                        "update-progress",
                        serde_json::json!({ "downloaded": downloaded, "total": total }),
                    );
                }
            },
            || {},
        )
        .await
        .map_err(|e| e.to_string())?;

    stop_engine(&app);
    app.restart();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(
            tauri_plugin_log::Builder::default()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            app_info,
            open_logs_folder,
            open_exports_folder,
            check_update,
            install_update
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let data_dir = handle
                .path()
                .app_local_data_dir()
                .map_err(|e| format!("no data folder: {e}"))?;
            fs::create_dir_all(&data_dir)?;
            let pid_file = data_dir.join("engine.pid");
            sweep_orphan(&pid_file);

            let started = match start_engine(&handle, &data_dir, &pid_file) {
                Ok(s) => s,
                Err(message) => {
                    fatal(&handle, message.clone());
                    return Err(message.into());
                }
            };
            let config = serde_json::json!({
                "mode": "local",
                "apiBase": format!("http://127.0.0.1:{}", started.port),
                "token": started.token,
                "version": APP_VERSION,
            });
            app.manage(Engine {
                child: Mutex::new(Some(started.child)),
                pid_file,
            });

            // Created only now, and with the config injected before any page
            // script runs, so the page's very first request finds the engine.
            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("ATC Fast-Time Simulation Tool")
            .inner_size(1600.0, 950.0)
            .min_inner_size(1024.0, 640.0)
            .initialization_script(&format!(
                "Object.defineProperty(window, '__APP_CONFIG__', {{ value: Object.freeze({config}), writable: false }});"
            ))
            .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_engine(handle);
        }
    });
}
