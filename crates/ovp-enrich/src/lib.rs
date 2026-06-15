//! OVP Enrichment: web fetch, GitHub enrichment, image download.
//!
//! This crate only _enriches_ existing sources — it never owns intake, dedup,
//! or lifecycle (those stay in `ovp-intake`). It also never touches demoted
//! substrate or canonical stores.

pub mod web_fetch;
pub mod github;
pub mod image_download;
