//! Navigation-data packs: newer data without a new program.
//!
//! A pack is a zip of the engine's data tree with a `pack.json` at its root
//! (built by `scripts/build_data_pack.py`). The shell:
//!
//! 1. at launch, names the selected pack to the engine in `$ATC_DATA_DIR`
//!    (the engine itself refuses a broken or outdated one and falls back to
//!    the data it was shipped with);
//! 2. while the app is open, asks the data manifest whether a newer pack
//!    exists, downloads it, and accepts it only if its size, SHA-256 **and
//!    signature** check out — the signature against the same public key that
//!    guards app updates, so neither a tampered pack nor a forged manifest
//!    gets in;
//! 3. unpacks it next to the others and selects it for the *next* start. A
//!    running simulation never has its data changed under it.
//!
//! Layout under the app's local data folder:
//!
//! ```text
//! data-packs/
//!   current.json            {"version": "2026.10.01.1"}
//!   2026.10.01.1/           an unpacked pack
//!   2026.09.03.2/           the previous one, kept for rollback
//! ```

use std::{
    fs,
    io::{Cursor, Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::Duration,
};

use base64::Engine as _;
use minisign_verify::{PublicKey, Signature};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{Emitter, Manager};

/// Where installed apps look for data. An asset of the release tagged `data`,
/// which is never the "latest" release — that one is the app.
const MANIFEST_URL: &str = "https://github.com/Changplay12345/ATC-Fast-Time-Sim-Performance-Testing-Version/releases/download/data/data-manifest.json";
const PACKS_DIR: &str = "data-packs";
const CURRENT_FILE: &str = "current.json";
const PACK_FILE: &str = "pack.json";
const PACK_SCHEMA: u64 = 1;
/// Refuse anything larger before downloading it (today's pack is ~10 MB).
const MAX_PACK_BYTES: u64 = 300 * 1024 * 1024;
/// …and anything that unpacks to more than this (today's is ~42 MB).
const MAX_UNPACKED_BYTES: u64 = 2 * 1024 * 1024 * 1024;
/// Packs kept on disk: the selected one and the one before it.
const KEEP_PACKS: usize = 2;
const FIRST_CHECK: Duration = Duration::from_secs(8);
const RECHECK: Duration = Duration::from_secs(6 * 60 * 60);
/// After a check that failed (no network yet, a server hiccup): try again
/// soon, a few times, instead of waiting for the next scheduled check.
const RETRY: Duration = Duration::from_secs(5 * 60);
const MAX_RETRIES: u32 = 3;

#[derive(Deserialize)]
struct Manifest {
    schema: u64,
    version: String,
    airac: Option<String>,
    min_app: Option<String>,
    notes: Option<String>,
    url: String,
    size: u64,
    sha256: String,
    signature: String,
}

/// A pack that has been downloaded and will be used from the next start.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Pending {
    version: String,
    airac: Option<String>,
    notes: Option<String>,
}

/// What the page is told (About dialog, banner).
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DataStatus {
    /// The data the running engine loaded.
    version: Option<String>,
    /// "bundled" (shipped with the program) or "pack" (downloaded).
    source: Option<String>,
    pending: Option<Pending>,
    /// Why the last check did not produce a pack, if it failed.
    error: Option<String>,
}

pub struct DataState {
    packs_dir: PathBuf,
    status: Mutex<DataStatus>,
    /// One check at a time (the timer and the About button can coincide).
    checking: AtomicBool,
}

impl DataState {
    pub fn new(data_dir: &Path, version: Option<String>, source: Option<String>) -> Self {
        let packs_dir = data_dir.join(PACKS_DIR);
        // A pack downloaded earlier that the engine is not running yet.
        let pending = selected_version(&packs_dir)
            .filter(|v| version_key(v) > version_key(version.as_deref().unwrap_or("")))
            .map(|v| Pending {
                version: v,
                airac: None,
                notes: None,
            });
        Self {
            packs_dir,
            status: Mutex::new(DataStatus {
                version,
                source,
                pending,
                error: None,
            }),
            checking: AtomicBool::new(false),
        }
    }

    pub fn status(&self) -> DataStatus {
        self.status.lock().map(|s| s.clone()).unwrap_or(DataStatus {
            version: None,
            source: None,
            pending: None,
            error: None,
        })
    }
}

/// `"2026.09.03.1"` -> `[2026, 9, 3, 1]`. Anything else -> empty, which
/// sorts before every real version. Also used for app versions (`"0.3.0"`).
pub fn version_key(version: &str) -> Vec<u64> {
    version
        .split('.')
        .map(|part| part.parse::<u64>())
        .collect::<Result<Vec<_>, _>>()
        .unwrap_or_default()
}

/// A version is used as a folder name, so it must be nothing but a version.
fn is_version(text: &str) -> bool {
    !version_key(text).is_empty() && text.chars().all(|c| c.is_ascii_digit() || c == '.')
}

fn selected_version(packs_dir: &Path) -> Option<String> {
    let text = fs::read_to_string(packs_dir.join(CURRENT_FILE)).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    let version = value.get("version")?.as_str()?.to_string();
    is_version(&version).then_some(version)
}

/// The pack to start the engine on, if one is selected and present.
pub fn selected_pack(data_dir: &Path) -> Option<PathBuf> {
    let packs_dir = data_dir.join(PACKS_DIR);
    let dir = packs_dir.join(selected_version(&packs_dir)?);
    dir.join(PACK_FILE).is_file().then_some(dir)
}

/// Stop selecting a pack (the engine would not start on it, or refused it).
pub fn deselect(data_dir: &Path) {
    let _ = fs::remove_file(data_dir.join(PACKS_DIR).join(CURRENT_FILE));
}

fn manifest_url() -> String {
    // Overridable so the whole path can be tested against a local server.
    // Safe to expose: whatever it points at still has to carry our signature.
    std::env::var("ATC_DATA_MANIFEST_URL").unwrap_or_else(|_| MANIFEST_URL.to_string())
}

/// A licence token for the data service, when there is one. Nothing issues
/// tokens yet and no token is sent; this is the place they will come from.
fn data_token(packs_dir: &Path) -> Option<String> {
    let text = fs::read_to_string(packs_dir.join("token.txt")).ok()?;
    let token = text.trim();
    (!token.is_empty()).then(|| token.to_string())
}

fn client() -> Result<reqwest::Client, String> {
    // reqwest is built without a TLS crypto provider of its own (as the
    // updater plugin builds it); install one if nobody has yet.
    if rustls::crypto::CryptoProvider::get_default().is_none() {
        let _ = rustls::crypto::ring::default_provider().install_default();
    }
    reqwest::Client::builder()
        .user_agent(concat!("atc-fts-desktop/", env!("CARGO_PKG_VERSION")))
        .connect_timeout(Duration::from_secs(15))
        .timeout(Duration::from_secs(300))
        .build()
        .map_err(|e| format!("cannot set up the download: {e}"))
}

/// Size, hash, then signature. The manifest is not trusted: it only tells us
/// what to fetch. Trust comes from the signature over the pack itself.
fn verify(bytes: &[u8], manifest: &Manifest, pubkey: &str) -> Result<(), String> {
    if bytes.len() as u64 != manifest.size {
        return Err(format!(
            "the pack is {} bytes, the manifest says {}",
            bytes.len(),
            manifest.size
        ));
    }
    let digest: String = Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect();
    if !digest.eq_ignore_ascii_case(manifest.sha256.trim()) {
        return Err("the pack's SHA-256 does not match the manifest".into());
    }
    let b64 = base64::engine::general_purpose::STANDARD;
    let decode = |what: &str, text: &str| -> Result<String, String> {
        let raw = b64
            .decode(text.trim())
            .map_err(|e| format!("the {what} is not valid base64: {e}"))?;
        String::from_utf8(raw).map_err(|e| format!("the {what} is not text: {e}"))
    };
    let key = PublicKey::decode(&decode("public key", pubkey)?)
        .map_err(|e| format!("the public key is unusable: {e}"))?;
    if manifest.signature.trim().is_empty() {
        return Err("the pack is not signed".into());
    }
    let signature = Signature::decode(&decode("signature", &manifest.signature)?)
        .map_err(|e| format!("the pack's signature is unreadable: {e}"))?;
    key.verify(bytes, &signature, true)
        .map_err(|e| format!("the pack's signature does not match: {e}"))
}

/// Unzip into `dest` (which must not exist). No entry may land outside it.
fn unpack(bytes: &[u8], dest: &Path) -> Result<(), String> {
    let mut archive =
        zip::ZipArchive::new(Cursor::new(bytes)).map_err(|e| format!("the pack is not a zip: {e}"))?;
    fs::create_dir_all(dest).map_err(|e| format!("cannot create {}: {e}", dest.display()))?;
    let mut total: u64 = 0;
    for i in 0..archive.len() {
        let mut entry = archive
            .by_index(i)
            .map_err(|e| format!("the pack is damaged: {e}"))?;
        let Some(relative) = entry.enclosed_name() else {
            return Err(format!("the pack has an unsafe path: {}", entry.name()));
        };
        let target = dest.join(relative);
        if entry.is_dir() {
            fs::create_dir_all(&target).map_err(|e| e.to_string())?;
            continue;
        }
        total += entry.size();
        if total > MAX_UNPACKED_BYTES {
            return Err("the pack unpacks to more than is plausible".into());
        }
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let mut data = Vec::with_capacity(entry.size() as usize);
        entry
            .read_to_end(&mut data)
            .map_err(|e| format!("the pack is damaged: {e}"))?;
        fs::write(&target, data).map_err(|e| format!("cannot write {}: {e}", target.display()))?;
    }
    Ok(())
}

/// Unpack a verified pack, check it is what the manifest promised, and select
/// it for the next start. Each step leaves the previous state usable: the
/// pack only becomes visible by a rename, and is only selected by another.
fn install(packs_dir: &Path, manifest: &Manifest, bytes: &[u8]) -> Result<(), String> {
    fs::create_dir_all(packs_dir).map_err(|e| format!("cannot create {}: {e}", packs_dir.display()))?;
    let partial = packs_dir.join(format!("{}.partial", manifest.version));
    let final_dir = packs_dir.join(&manifest.version);
    let _ = fs::remove_dir_all(&partial);
    let unpacked = unpack(bytes, &partial).and_then(|()| {
        let text = fs::read_to_string(partial.join(PACK_FILE))
            .map_err(|_| format!("the pack has no {PACK_FILE}"))?;
        let own: serde_json::Value =
            serde_json::from_str(&text).map_err(|e| format!("the pack's {PACK_FILE} is unreadable: {e}"))?;
        if own.get("version").and_then(|v| v.as_str()) != Some(manifest.version.as_str()) {
            return Err("the pack is not the version the manifest announced".into());
        }
        if own.get("schema").and_then(|v| v.as_u64()) != Some(PACK_SCHEMA) {
            return Err("the pack has a layout this app does not read".into());
        }
        Ok(())
    });
    if let Err(e) = unpacked {
        let _ = fs::remove_dir_all(&partial);
        return Err(e);
    }
    let _ = fs::remove_dir_all(&final_dir);
    fs::rename(&partial, &final_dir).map_err(|e| format!("cannot move the pack into place: {e}"))?;

    let tmp = packs_dir.join(format!("{CURRENT_FILE}.tmp"));
    let body = serde_json::json!({ "version": manifest.version }).to_string();
    fs::File::create(&tmp)
        .and_then(|mut f| f.write_all(body.as_bytes()))
        .map_err(|e| format!("cannot record the new pack: {e}"))?;
    fs::rename(&tmp, packs_dir.join(CURRENT_FILE)).map_err(|e| format!("cannot select the new pack: {e}"))?;
    Ok(())
}

/// Keep the newest `KEEP_PACKS` packs (and whatever the engine is running
/// on); remove older ones and any half-unpacked leftovers.
fn prune(packs_dir: &Path, in_use: Option<&str>) {
    let Ok(entries) = fs::read_dir(packs_dir) else {
        return;
    };
    let mut versions: Vec<String> = Vec::new();
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !entry.path().is_dir() {
            continue;
        }
        if name.ends_with(".partial") {
            let _ = fs::remove_dir_all(entry.path());
        } else if is_version(&name) {
            versions.push(name);
        }
    }
    versions.sort_by_key(|v| std::cmp::Reverse(version_key(v)));
    for old in versions.iter().skip(KEEP_PACKS) {
        if Some(old.as_str()) != in_use {
            log::info!("removing old data pack {old}");
            let _ = fs::remove_dir_all(packs_dir.join(old));
        }
    }
}

fn updater_pubkey(app: &tauri::AppHandle) -> Result<String, String> {
    app.config()
        .plugins
        .0
        .get("updater")
        .and_then(|u| u.get("pubkey"))
        .and_then(|k| k.as_str())
        .map(str::to_owned)
        .ok_or_else(|| "no public key is configured".to_string())
}

/// One round: is there a newer pack, and if so fetch, verify and select it.
/// `Ok(Some(..))` when a pack was installed, `Ok(None)` when there was
/// nothing to do.
async fn check(app: &tauri::AppHandle, state: &DataState) -> Result<Option<Pending>, String> {
    let before = state.status();
    let newest_here = [
        before.version.clone(),
        before.pending.as_ref().map(|p| p.version.clone()),
    ]
    .into_iter()
    .flatten()
    .map(|v| version_key(&v))
    .max()
    .unwrap_or_default();

    let client = client()?;
    let mut request = client.get(manifest_url());
    if let Some(token) = data_token(&state.packs_dir) {
        request = request.bearer_auth(token);
    }
    let response = request
        .send()
        .await
        .map_err(|e| format!("the data server could not be reached: {e}"))?;
    if !response.status().is_success() {
        return Err(format!("the data server answered {}", response.status()));
    }
    let text = response
        .text()
        .await
        .map_err(|e| format!("the data manifest could not be read: {e}"))?;
    let manifest: Manifest =
        serde_json::from_str(&text).map_err(|e| format!("the data manifest is not valid: {e}"))?;

    if manifest.schema != PACK_SCHEMA {
        return Err(format!("the data manifest has schema {}, this app reads {PACK_SCHEMA}", manifest.schema));
    }
    if !is_version(&manifest.version) {
        return Err("the data manifest has no valid version".into());
    }
    if version_key(&manifest.version) <= newest_here {
        return Ok(None);
    }
    if let Some(min_app) = manifest.min_app.as_deref() {
        if version_key(min_app) > version_key(env!("CARGO_PKG_VERSION")) {
            // Not an error: the app update that can read it comes first.
            log::info!(
                "data pack {} needs app {min_app} or newer; waiting for the app update",
                manifest.version
            );
            return Ok(None);
        }
    }
    if manifest.size > MAX_PACK_BYTES {
        return Err(format!("the data pack is implausibly large ({} bytes)", manifest.size));
    }

    log::info!("downloading data pack {} ({} bytes)", manifest.version, manifest.size);
    let response = client
        .get(&manifest.url)
        .send()
        .await
        .map_err(|e| format!("the data pack could not be downloaded: {e}"))?;
    if !response.status().is_success() {
        return Err(format!("the data pack download answered {}", response.status()));
    }
    let bytes = response
        .bytes()
        .await
        .map_err(|e| format!("the data pack download was interrupted: {e}"))?;

    verify(&bytes, &manifest, &updater_pubkey(app)?)?;
    install(&state.packs_dir, &manifest, &bytes)?;
    prune(&state.packs_dir, before.version.as_deref());
    log::info!("data pack {} is ready; it is used from the next start", manifest.version);
    Ok(Some(Pending {
        version: manifest.version,
        airac: manifest.airac,
        notes: manifest.notes,
    }))
}

/// Run one check, record the outcome and tell the page.
pub async fn check_now(app: &tauri::AppHandle) -> DataStatus {
    let Some(state) = app.try_state::<DataState>() else {
        return DataStatus {
            version: None,
            source: None,
            pending: None,
            error: Some("the data service is not running".into()),
        };
    };
    if state.checking.swap(true, Ordering::SeqCst) {
        return state.status();
    }
    let outcome = check(app, &state).await;
    state.checking.store(false, Ordering::SeqCst);
    if let Ok(mut status) = state.status.lock() {
        match outcome {
            Ok(Some(pending)) => {
                status.pending = Some(pending);
                status.error = None;
            }
            Ok(None) => status.error = None,
            Err(e) => {
                log::warn!("data update: {e}");
                status.error = Some(e);
            }
        }
    }
    let status = state.status();
    let _ = app.emit("data-status", status.clone());
    status
}

/// Check shortly after launch, then a few times a day while the app is open.
pub fn watch(app: &tauri::AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(FIRST_CHECK);
        let mut retries = 0;
        loop {
            let status = tauri::async_runtime::block_on(check_now(&app));
            if status.error.is_some() && retries < MAX_RETRIES {
                retries += 1;
                std::thread::sleep(RETRY);
            } else {
                retries = 0;
                std::thread::sleep(RECHECK);
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_compare_by_number() {
        assert!(version_key("2026.10.01.10") > version_key("2026.10.01.9"));
        assert!(version_key("2026.10.01.1") > version_key("2026.09.03.7"));
        assert!(version_key("0.3.0") > version_key("0.2.9"));
        assert!(version_key("nonsense").is_empty());
        assert!(version_key("") < version_key("2026.09.03.1"));
    }

    #[test]
    fn only_a_version_can_name_a_folder() {
        assert!(is_version("2026.10.01.1"));
        assert!(!is_version("../evil"));
        assert!(!is_version("2026.10.01.1/.."));
        assert!(!is_version(""));
        assert!(!is_version("+1.2"));
    }

    fn manifest(bytes: &[u8]) -> Manifest {
        Manifest {
            schema: 1,
            version: "2026.10.01.1".into(),
            airac: None,
            min_app: None,
            notes: None,
            url: String::new(),
            size: bytes.len() as u64,
            sha256: Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect(),
            signature: String::new(),
        }
    }

    #[test]
    fn a_pack_with_the_wrong_size_or_hash_or_no_signature_is_refused() {
        let bytes = b"not really a pack";
        let key = "ignored";

        let mut wrong_size = manifest(bytes);
        wrong_size.size += 1;
        assert!(verify(bytes, &wrong_size, key).unwrap_err().contains("bytes"));

        let mut wrong_hash = manifest(bytes);
        wrong_hash.sha256 = "0".repeat(64);
        assert!(verify(bytes, &wrong_hash, key).unwrap_err().contains("SHA-256"));

        // Size and hash right, but nothing vouches for it.
        assert!(verify(bytes, &manifest(bytes), key).is_err());
    }

    #[test]
    fn unpacking_refuses_paths_that_leave_the_folder() {
        let mut buffer = Cursor::new(Vec::new());
        {
            let mut zip = zip::ZipWriter::new(&mut buffer);
            let options = zip::write::SimpleFileOptions::default();
            zip.start_file("../outside.txt", options).unwrap();
            zip.write_all(b"x").unwrap();
            zip.finish().unwrap();
        }
        let dir = std::env::temp_dir().join(format!("atc-pack-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let result = unpack(buffer.get_ref(), &dir.join("pack"));
        let escaped = dir.join("outside.txt").exists();
        let _ = fs::remove_dir_all(&dir);
        assert!(result.is_err());
        assert!(!escaped);
    }

    #[test]
    fn pruning_keeps_the_newest_two_and_the_one_in_use() {
        let dir = std::env::temp_dir().join(format!("atc-prune-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        for v in ["2026.07.09.1", "2026.08.06.1", "2026.09.03.1", "2026.10.01.1", "2026.10.01.2.partial"] {
            fs::create_dir_all(dir.join(v)).unwrap();
        }
        prune(&dir, Some("2026.07.09.1"));
        let mut left: Vec<String> = fs::read_dir(&dir)
            .unwrap()
            .flatten()
            .map(|e| e.file_name().to_string_lossy().to_string())
            .collect();
        left.sort();
        let _ = fs::remove_dir_all(&dir);
        assert_eq!(left, ["2026.07.09.1", "2026.09.03.1", "2026.10.01.1"]);
    }
}
