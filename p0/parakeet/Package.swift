// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "SubtitleParakeetProbe",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/FluidInference/FluidAudio.git",
                 revision: "ee78635d23d52379117554ba72358b65a0612447", traits: [])
    ],
    targets: [
        .executableTarget(name: "ParakeetProbe", dependencies: [
            .product(name: "FluidAudio", package: "FluidAudio")
        ])
    ]
)
