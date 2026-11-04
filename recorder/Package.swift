// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "MeetingMinutes",
    platforms: [.macOS("26.0")],
    targets: [
        .executableTarget(
            name: "MeetingMinutes",
            path: "Sources/MeetingMinutes",
            linkerSettings: [
                .linkedFramework("ScreenCaptureKit"),
                .linkedFramework("AVFoundation"),
                .linkedFramework("CoreMedia"),
                .linkedFramework("AppKit"),
                .linkedFramework("Speech"),
                .linkedFramework("ServiceManagement"),
            ]
        )
    ]
)
