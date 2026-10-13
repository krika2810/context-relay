import Foundation

struct EngineError: Error {
    let message: String
}

/// Collects a pipe's output without relying on EOF, so a detached grandchild
/// (e.g. a launched terminal) that inherited the pipe cannot block us.
private final class PipeCollector {
    private let handle: FileHandle
    private let lock = NSLock()
    private var buffer = Data()

    init(_ pipe: Pipe) {
        handle = pipe.fileHandleForReading
        handle.readabilityHandler = { [weak self] h in
            let chunk = h.availableData
            guard let self = self else { return }
            if chunk.isEmpty { h.readabilityHandler = nil; return }
            self.lock.lock(); self.buffer.append(chunk); self.lock.unlock()
        }
    }

    /// Stops streaming and drains whatever is left without blocking.
    func finish() -> Data {
        handle.readabilityHandler = nil
        let fd = handle.fileDescriptor
        _ = fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK)
        var chunk = [UInt8](repeating: 0, count: 65536)
        var tail = Data()
        while true {
            let n = read(fd, &chunk, chunk.count)
            if n <= 0 { break }
            tail.append(chunk, count: n)
        }
        lock.lock(); defer { lock.unlock() }
        buffer.append(tail)
        return buffer
    }
}

final class Engine {
    static let timeout: TimeInterval = 20

    static var engineDir: String {
        if let override = ProcessInfo.processInfo.environment["RELAY_ENGINE_DIR"], !override.isEmpty {
            return override
        }
        return (Bundle.main.resourcePath ?? "") + "/engine"
    }

    /// Runs `python3 -m context_relay <args>` synchronously. Call from a background queue.
    static func run(_ args: [String], timeout: TimeInterval = Engine.timeout) -> Result<Data, EngineError> {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = ["-m", "context_relay"] + args
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = engineDir
        env["PYTHONDONTWRITEBYTECODE"] = "1" // never write into the (signed) app bundle
        env["PYTHONUNBUFFERED"] = "1"
        process.environment = env
        process.standardInput = FileHandle.nullDevice

        let outPipe = Pipe(), errPipe = Pipe()
        process.standardOutput = outPipe
        process.standardError = errPipe
        let out = PipeCollector(outPipe), err = PipeCollector(errPipe)

        let done = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in done.signal() }
        do { try process.run() } catch {
            _ = out.finish(); _ = err.finish()
            return .failure(EngineError(message: "could not start python3 (\(error.localizedDescription))"))
        }

        let timedOut = done.wait(timeout: .now() + timeout) == .timedOut
        if timedOut {
            process.terminate()
            _ = done.wait(timeout: .now() + 2)
        }
        let stdout = out.finish()
        let stderr = String(decoding: err.finish(), as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)

        if timedOut { return .failure(EngineError(message: "timed out after \(Int(timeout))s")) }
        if stdout.isEmpty {
            if stderr.contains("xcode-select") || stderr.contains("developer tools") || stderr.contains("xcrun: error") {
                return .failure(EngineError(message: "Context Relay needs Apple's Command Line Tools for Python. "
                    + "Open Terminal, run: xcode-select --install, then reopen Context Relay."))
            }
            let detail = stderr.split(separator: "\n").last.map(String.init) ?? "exit code \(process.terminationStatus)"
            return .failure(EngineError(message: detail))
        }
        return .success(stdout)
    }

    /// Decodes engine stdout, tolerating stray text before the JSON object.
    static func decode<T: Decodable>(_ type: T.Type, from data: Data) -> Result<T, EngineError> {
        let decoder = JSONDecoder()
        if let value = try? decoder.decode(T.self, from: data) { return .success(value) }
        if let start = data.firstIndex(of: UInt8(ascii: "{")),
           let value = try? decoder.decode(T.self, from: data[start...]) {
            return .success(value)
        }
        do {
            return .success(try decoder.decode(T.self, from: data))
        } catch {
            return .failure(EngineError(message: "invalid JSON from engine (\(describe(error)))"))
        }
    }

    private static func describe(_ error: Error) -> String {
        guard let e = error as? DecodingError else { return error.localizedDescription }
        switch e {
        case .keyNotFound(let key, _): return "missing key \(key.stringValue)"
        case .typeMismatch(_, let ctx), .valueNotFound(_, let ctx), .dataCorrupted(let ctx):
            return ctx.debugDescription
        @unknown default: return "decode failure"
        }
    }

    /// Runs the engine off the main thread and delivers the decoded result on the main queue.
    static func call<T: Decodable>(_ args: [String], as type: T.Type,
                                   completion: @escaping (Result<T, EngineError>) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let result = run(args).flatMap { decode(T.self, from: $0) }
            DispatchQueue.main.async { completion(result) }
        }
    }
}
