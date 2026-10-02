//! Release control: who is offered an update, and who must take it.
//!
//! Two optional fields in the update manifest (`latest.json`), next to the
//! ones the updater itself reads:
//!
//! ```json
//! { "version": "0.4.1", "rollout": 25, "min_supported": "0.4.0", ... }
//! ```
//!
//! * `rollout` (0-100, default 100): the share of installations that are
//!   *offered* the update by the background check. Each installation draws a
//!   number 0-99 once and keeps it; it is offered the update when its number
//!   is below `rollout`. Raising the figure only ever adds installations.
//!   "Check for updates" in About always offers it: a person asking for the
//!   update gets it.
//! * `min_supported`: the oldest version that may keep running. An app below
//!   it must update before it can be used (a wrong-separation bug, a data
//!   format that is going away). It overrides the rollout.
//!
//! The manifest is not signed, so these fields are not a security boundary:
//! the worst a forged one can do is offer, or insist on, an update that still
//! has to carry our signature and be newer than what is installed.
//!
//! Stopping a bad release altogether is done on the server
//! (`scripts/release_control.py halt`): the previous release becomes "latest"
//! again and nobody is offered the bad one.

use std::{fs, path::Path};

use crate::datapack::version_key;

const BUCKET_FILE: &str = "install-bucket";

pub struct Policy {
    /// 0-100.
    pub rollout: u8,
    pub min_supported: Option<String>,
}

/// Read the policy from the manifest as the updater received it. Missing or
/// malformed fields mean "no restriction": an old or hand-edited manifest
/// must never stop updates.
pub fn policy(manifest: &serde_json::Value) -> Policy {
    let rollout = manifest
        .get("rollout")
        .and_then(|v| v.as_f64())
        .map(|n| n.clamp(0.0, 100.0) as u8)
        .unwrap_or(100);
    let min_supported = manifest
        .get("min_supported")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|v| !version_key(v).is_empty())
        .map(str::to_owned);
    Policy {
        rollout,
        min_supported,
    }
}

/// This installation's number, 0-99: drawn once, then kept in the data
/// folder. If it cannot be stored it is 0 for this run, which errs towards
/// offering the update.
pub fn bucket(data_dir: &Path) -> u8 {
    let file = data_dir.join(BUCKET_FILE);
    if let Some(n) = fs::read_to_string(&file)
        .ok()
        .and_then(|t| t.trim().parse::<u8>().ok())
        .filter(|n| *n < 100)
    {
        return n;
    }
    let mut byte = [0u8; 4];
    if getrandom::getrandom(&mut byte).is_err() {
        return 0;
    }
    let n = (u32::from_le_bytes(byte) % 100) as u8;
    match fs::write(&file, n.to_string()) {
        Ok(()) => n,
        Err(_) => 0,
    }
}

pub fn in_rollout(bucket: u8, rollout: u8) -> bool {
    bucket < rollout
}

/// Must `app_version` update before it may be used?
pub fn is_required(app_version: &str, min_supported: Option<&str>) -> bool {
    match min_supported {
        Some(min) => version_key(app_version) < version_key(min),
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_manifest_without_the_fields_restricts_nothing() {
        let p = policy(&json!({ "version": "0.4.1" }));
        assert_eq!(p.rollout, 100);
        assert!(p.min_supported.is_none());
        assert!(in_rollout(99, p.rollout));
    }

    #[test]
    fn malformed_fields_restrict_nothing() {
        let p = policy(&json!({ "rollout": "half", "min_supported": "soon" }));
        assert_eq!(p.rollout, 100);
        assert!(p.min_supported.is_none());
        let p = policy(&json!({ "rollout": 250, "min_supported": 4 }));
        assert_eq!(p.rollout, 100);
        assert!(p.min_supported.is_none());
        assert_eq!(policy(&json!({ "rollout": -5 })).rollout, 0);
    }

    #[test]
    fn the_rollout_share_decides_who_is_offered() {
        assert!(!in_rollout(0, 0), "0 % offers it to nobody");
        assert!(in_rollout(0, 1));
        assert!(in_rollout(24, 25));
        assert!(!in_rollout(25, 25));
        assert!(in_rollout(99, 100), "100 % offers it to everybody");
        // Raising the share only adds installations.
        for bucket in 0..100u8 {
            if in_rollout(bucket, 25) {
                assert!(in_rollout(bucket, 50));
            }
        }
    }

    #[test]
    fn only_versions_below_the_minimum_must_update() {
        assert!(is_required("0.3.9", Some("0.4.0")));
        assert!(is_required("0.3.10", Some("0.4.0")));
        assert!(!is_required("0.4.0", Some("0.4.0")));
        assert!(!is_required("0.10.0", Some("0.9.0")), "compared by number, not text");
        assert!(!is_required("0.3.0", None));
    }

    #[test]
    fn the_bucket_is_drawn_once_and_kept() {
        let dir = std::env::temp_dir().join(format!("atc-bucket-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let first = bucket(&dir);
        assert!(first < 100);
        assert_eq!(bucket(&dir), first);
        assert_eq!(bucket(&dir), first);
        // A damaged file is replaced, not trusted.
        fs::write(dir.join(BUCKET_FILE), "250").unwrap();
        let redrawn = bucket(&dir);
        assert!(redrawn < 100);
        assert_eq!(bucket(&dir), redrawn);
        let _ = fs::remove_dir_all(&dir);
    }
}
