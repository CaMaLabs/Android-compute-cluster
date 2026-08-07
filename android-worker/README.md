# Native Android worker

This is the native Android foreground-service worker for the universal compute swarm. It uses only outbound HTTP(S), so phones can sit behind a hotspot, home router, or carrier NAT.

## Build

Open `android-worker/` in Android Studio and build the `app` module. The current project targets Android API 35 and requires Java 17.

## Use

1. Enter the controller URL.
2. Enter the enrollment token.
3. Tap **Join swarm**.
4. Leave the foreground-service notification active while the phone is contributing compute.

Public/remote controllers must use HTTPS. Plain HTTP is accepted only for localhost/private RFC1918 LAN addresses such as a home network or phone hotspot.

The worker currently advertises these native tasks:

- `prime_count`
- `monte_carlo_pi`
- `sha256_artifact`
- `text_artifact`

`TaskRegistry.kt` is the extension point for additional native Kotlin/NDK/Vulkan/TFLite workloads. The controller selects a registered task name but never sends an executable or shell command.
