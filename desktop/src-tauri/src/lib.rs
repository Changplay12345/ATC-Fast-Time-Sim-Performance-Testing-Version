//! Desktop shell: starts the packaged Python engine as a child process, waits
//! until it answers, then opens the window on the static front end.
//!
//! Phase 0 (spike): fixed port 8765, the front end was built with that port
//! baked in. Phase 1 replaces this with a random free port, a per-session
//! token and runtime config injected into the page.

use std::{
    env, fs,
    net::{SocketAddr, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
};

use tauri::{Manager, RunEvent};

const ENGINE_PORT: u16 = 8765;
/// How long the engine gets to come up before we give up (cold disk, AV scan…).
const ENGINE_START_TIMEOUT: Duration = Duration::from_secs(60);

/// The running engine process, killed when the app exits.
struct Engine(Mutex<Option<Child>>);

/// Where the engine executable is: inside the app's resources once installed,
/// or the PyInstaller output folder in the repo while developing.
fn engine_exe(app: &tauri::AppHandle) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(res) = app.path().resource_dir() {
        candidates.push(res.join("engine").join("atc-engine.exe"));
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            // desktop/src-tauri/target/<profile>/app.exe -> desktop/sidecar/dist/…
            candidates.push(dir.join("../../../sidecar/dist/atc-engine/atc-engine.exe"));
        }
    }
    candidates.into_iter().find(|p| p.is_file())
}

fn port_open(port: u16) -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

fn start_engine(app: &tauri::AppHandle) -> Result<Child, String> {
    let exe = engine_exe(app).ok_or("engine executable not found")?;
    let log_dir = app
        .path()
        .app_log_dir()
        .map_err(|e| format!("no log dir: {e}"))?;
    fs::create_dir_all(&log_dir).map_err(|e| format!("cannot create log dir: {e}"))?;
    let log = fs::File::create(log_dir.join("engine.log"))
        .map_err(|e| format!("cannot open engine.log: {e}"))?;
    let log_err = log.try_clone().map_err(|e| e.to_string())?;

    let mut cmd = Command::new(&exe);
    cmd.current_dir(exe.parent().expect("exe has a parent"))
        .env("ATC_BIND", "127.0.0.1")
        .env("ATC_PORT", ENGINE_PORT.to_string())
        // The page is served from this origin by Tauri on Windows; the engine's
        // CORS allow-list reads WEB_ORIGIN. Phase 1 drops CORS in local mode.
        .env("WEB_ORIGIN", "http://tauri.localhost")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(log_err));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let child = cmd.spawn().map_err(|e| format!("cannot start engine: {e}"))?;

    let started = Instant::now();
    while started.elapsed() < ENGINE_START_TIMEOUT {
        if port_open(ENGINE_PORT) {
            log::info!("engine up on port {ENGINE_PORT} after {:?}", started.elapsed());
            return Ok(child);
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Err(format!(
        "engine did not open port {ENGINE_PORT} within {:?}; see {}",
        ENGINE_START_TIMEOUT,
        log_dir.join("engine.log").display()
    ))
}

fn stop_engine(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<Engine>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(mut child) = guard.take() {
                // Killing the main process is enough: its worker processes
                // watch their parent and exit on their own.
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(
            tauri_plugin_log::Builder::default()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .setup(|app| {
            let handle = app.handle().clone();
            // An engine left over from a crashed previous run would already hold
            // the port; using it is better than failing (Phase 1: PID file).
            let child = if port_open(ENGINE_PORT) {
                log::warn!("port {ENGINE_PORT} already open — reusing a running engine");
                None
            } else {
                Some(start_engine(&handle)?)
            };
            app.manage(Engine(Mutex::new(child)));

            // The window is created only now, so the page's first requests
            // find the engine already answering.
            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("ATC Fast-Time Simulation Tool")
            .inner_size(1600.0, 950.0)
            .min_inner_size(1024.0, 640.0)
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
