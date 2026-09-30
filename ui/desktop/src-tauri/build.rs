fn main() {
    // GNU ld exports every symbol from a cdylib and can exceed the PE ordinal
    // limit (65535). Hide internals so mingw can link Tauri apps.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows")
        && std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("gnu")
    {
        println!("cargo::rustc-link-arg=-Wl,--exclude-libs=ALL,--exclude-all-symbols");
    }
    tauri_build::build()
}
