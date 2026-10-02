//! "Export diagnostics": one zip a user can attach to an email to support.
//!
//! It holds a summary (versions, operating system, processor count) and the
//! app's own logs. Nothing is sent anywhere: the file is written to the
//! exports folder and shown in the file manager, and the user decides what to
//! do with it.
//!
//! The logs are the app's, not the user's work: no flight plans, scenarios or
//! exports go in. Paths inside the user's profile are rewritten to
//! `%USERPROFILE%` so the Windows account name does not travel with the file.

use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

/// Per log file: the end of it is what matters, and a runaway log must not
/// produce a zip too big to email.
const MAX_LOG_BYTES: usize = 2 * 1024 * 1024;

/// Seconds since 1970 -> (year, month, day, hour, minute, second), UTC.
fn civil(secs: u64) -> (i64, u32, u32, u32, u32, u32) {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    // Howard Hinnant's days-to-civil.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = (if mp < 10 { mp + 3 } else { mp - 9 }) as u32;
    let year = yoe + era * 400 + i64::from(month <= 2);
    (
        year,
        month,
        day,
        (rem / 3_600) as u32,
        (rem % 3_600 / 60) as u32,
        (rem % 60) as u32,
    )
}

/// Replace the user's profile folder, in every spelling a log may use, so the
/// account name is not in the file.
fn scrub(text: &str, home: Option<&str>) -> String {
    let Some(home) = home.filter(|h| h.len() > 3) else {
        return text.to_string();
    };
    let mut out = text.to_string();
    for spelling in [
        home.to_string(),
        home.replace('\\', "/"),
        home.replace('\\', "\\\\"),
    ] {
        // Windows paths are case-insensitive and logs are not consistent.
        let lower_out = out.to_lowercase();
        let lower_spelling = spelling.to_lowercase();
        if lower_out.len() != out.len() {
            // Lower-casing changed byte offsets (non-ASCII text): fall back
            // to an exact-case replacement rather than cut in the wrong place.
            out = out.replace(&spelling, "%USERPROFILE%");
            continue;
        }
        let mut result = String::with_capacity(out.len());
        let mut at = 0;
        while let Some(found) = lower_out[at..].find(&lower_spelling) {
            result.push_str(&out[at..at + found]);
            result.push_str("%USERPROFILE%");
            at += found + spelling.len();
        }
        result.push_str(&out[at..]);
        out = result;
    }
    out
}

/// The last `MAX_LOG_BYTES` of a log, as text.
fn tail(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    let start = bytes.len().saturating_sub(MAX_LOG_BYTES);
    let mut text = String::from_utf8_lossy(&bytes[start..]).into_owned();
    if start > 0 {
        text.insert_str(0, "[... earlier lines left out ...]\n");
    }
    Some(text)
}

/// Write `diagnostics_<UTC time>.zip` into `out_dir` and return its path.
/// `summary` is the text the caller assembled (versions and so on).
pub fn export(out_dir: &Path, log_dir: &Path, summary: &str) -> Result<PathBuf, String> {
    fs::create_dir_all(out_dir).map_err(|e| format!("cannot create {}: {e}", out_dir.display()))?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let (y, mo, d, h, mi, s) = civil(now);
    let path = out_dir.join(format!("diagnostics_{y:04}{mo:02}{d:02}_{h:02}{mi:02}{s:02}.zip"));
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .ok();

    let file = fs::File::create(&path).map_err(|e| format!("cannot write {}: {e}", path.display()))?;
    let mut zip = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default();
    let mut add = |name: &str, text: &str| -> Result<(), String> {
        zip.start_file(name, options).map_err(|e| e.to_string())?;
        zip.write_all(scrub(text, home.as_deref()).as_bytes())
            .map_err(|e| e.to_string())
    };

    add(
        "summary.txt",
        &format!("Exported {y:04}-{mo:02}-{d:02} {h:02}:{mi:02}:{s:02} UTC\n{summary}"),
    )?;
    let mut logs: Vec<PathBuf> = fs::read_dir(log_dir)
        .map(|entries| {
            entries
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.extension().is_some_and(|x| x == "log"))
                .collect()
        })
        .unwrap_or_default();
    logs.sort();
    for log in logs {
        let (Some(name), Some(text)) = (log.file_name().and_then(|n| n.to_str()), tail(&log)) else {
            continue;
        };
        add(&format!("logs/{name}"), &text)?;
    }
    zip.finish().map_err(|e| e.to_string())?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    #[test]
    fn dates_come_out_right() {
        assert_eq!(civil(0), (1970, 1, 1, 0, 0, 0));
        assert_eq!(civil(951_782_400), (2000, 2, 29, 0, 0, 0)); // leap day
        assert_eq!(civil(1_790_931_298), (2026, 10, 2, 8, 54, 58));
    }

    #[test]
    fn the_profile_folder_is_scrubbed_in_every_spelling() {
        let home = r"C:\Users\Somchai";
        let text = "a C:\\Users\\Somchai\\AppData b c:/users/somchai/x d C:\\\\Users\\\\Somchai\\\\y e";
        let out = scrub(text, Some(home));
        assert!(!out.to_lowercase().contains("somchai"), "{out}");
        assert_eq!(out.matches("%USERPROFILE%").count(), 3);
        assert!(out.starts_with("a ") && out.ends_with(" e"));
        // No profile known, or an implausibly short one: text is left alone.
        assert_eq!(scrub(text, None), text);
        assert_eq!(scrub("C:\\ is a drive", Some("C:\\")), "C:\\ is a drive");
    }

    #[test]
    fn the_zip_holds_the_summary_and_the_logs_scrubbed() {
        let root = std::env::temp_dir().join(format!("atc-diag-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let logs = root.join("logs");
        fs::create_dir_all(&logs).unwrap();
        let home = std::env::var("USERPROFILE")
            .or_else(|_| std::env::var("HOME"))
            .unwrap_or_else(|_| "/nowhere/special".into());
        fs::write(logs.join("engine.log"), format!("started in {home}/app\n")).unwrap();
        fs::write(logs.join("notes.txt"), "not a log").unwrap();

        let path = export(&root.join("out"), &logs, "App 0.0.0\n").unwrap();
        let mut archive = zip::ZipArchive::new(fs::File::open(&path).unwrap()).unwrap();
        let names: Vec<String> = (0..archive.len())
            .map(|i| archive.by_index(i).unwrap().name().to_string())
            .collect();
        assert_eq!(names, ["summary.txt", "logs/engine.log"]);
        let mut log = String::new();
        archive.by_name("logs/engine.log").unwrap().read_to_string(&mut log).unwrap();
        assert!(log.contains("%USERPROFILE%"), "{log}");
        assert!(!log.contains(&home));
        let mut summary = String::new();
        archive.by_name("summary.txt").unwrap().read_to_string(&mut summary).unwrap();
        assert!(summary.contains("App 0.0.0") && summary.starts_with("Exported "));
        drop(archive);
        let _ = fs::remove_dir_all(&root);
    }
}
