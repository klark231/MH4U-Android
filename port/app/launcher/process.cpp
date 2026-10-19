// (Windows Vista's process API: STARTUPINFOEX and its handle list)
#if defined(_WIN32) && (!defined(_WIN32_WINNT) || _WIN32_WINNT < 0x0600)
#undef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif

#include "launcher/process.h"

#include <atomic>
#include <chrono>
#include <cstring>
#include <mutex>
#include <thread>

#ifdef _WIN32
#include <windows.h>
#include "common/string_util.h"
#else
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <poll.h>
#include <spawn.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#ifdef __APPLE__
#include <climits>
#include <cstdlib>
#include <mach-o/dyld.h>
#endif
extern char** environ;
#endif

namespace Launcher {

namespace {

using Clock = std::chrono::steady_clock;
constexpr auto KILL_AFTER = std::chrono::seconds(3);   // after Stop(): then forcibly
constexpr auto DRAIN_FOR = std::chrono::seconds(1);    // output still read after the process ended

#ifdef _WIN32
/// One argument as the C runtime's command-line parser reads it back.
std::wstring QuoteArg(const std::wstring& arg) {
    if (!arg.empty() && arg.find_first_of(L" \t\n\v\"") == std::wstring::npos) {
        return arg;
    }
    std::wstring out = L"\"";
    for (auto it = arg.begin();; ++it) {
        std::size_t backslashes = 0;
        while (it != arg.end() && *it == L'\\') {
            ++it;
            ++backslashes;
        }
        if (it == arg.end()) {
            out.append(backslashes * 2, L'\\');
            break;
        }
        if (*it == L'"') {
            out.append(backslashes * 2 + 1, L'\\');
        } else {
            out.append(backslashes, L'\\');
        }
        out.push_back(*it);
    }
    out.push_back(L'"');
    return out;
}

std::string LastError() {
    const DWORD code = GetLastError();
    wchar_t* text = nullptr;
    FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
                       FORMAT_MESSAGE_IGNORE_INSERTS,
                   nullptr, code, 0, reinterpret_cast<wchar_t*>(&text), 0, nullptr);
    std::string message = text ? Common::UTF16ToUTF8(text) : "error " + std::to_string(code);
    if (text) {
        LocalFree(text);
    }
    while (!message.empty() && (message.back() == '\n' || message.back() == '\r' || message.back() == '.')) {
        message.pop_back();
    }
    return message;
}
#endif

} // namespace

struct Process::Impl {
    mutable std::mutex mutex;
    std::string output;
    std::atomic<bool> running{false};
    std::atomic<bool> stop_requested{false};
    std::atomic<int> exit_code{-1};
    std::thread reader;
#ifdef _WIN32
    HANDLE process = nullptr;
    HANDLE job = nullptr;
    HANDLE pipe = nullptr;
#else
    pid_t pid = -1;
    int fd = -1;
    bool reaped = false;  // (under mutex) the pid may be reused after this: no more signals
#endif

    void Append(const char* data, std::size_t size) {
        std::scoped_lock lock{mutex};
        output.append(data, size);
    }

    void Read();
    void Kill(bool hard);
};

#ifdef _WIN32

void Process::Impl::Kill(bool) {
    if (job) {
        TerminateJobObject(job, 1);
    } else if (process) {
        TerminateProcess(process, 1);
    }
}

void Process::Impl::Read() {
    char buffer[4096];
    Clock::time_point ended{};
    bool exited = false;
    for (;;) {
        DWORD available = 0;
        if (!PeekNamedPipe(pipe, nullptr, 0, nullptr, &available, nullptr)) {
            break;  // every writer has closed it
        }
        if (available > 0) {
            DWORD got = 0;
            if (!ReadFile(pipe, buffer, sizeof(buffer), &got, nullptr) || got == 0) {
                break;
            }
            Append(buffer, got);
            continue;
        }
        if (!exited) {
            exited = WaitForSingleObject(process, 50) == WAIT_OBJECT_0;
            if (exited) {
                ended = Clock::now();
            }
        } else if (Clock::now() - ended > DRAIN_FOR) {
            break;  // something it started still holds the pipe: don't wait for that
        } else {
            Sleep(20);
        }
    }
    WaitForSingleObject(process, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(process, &code);
    exit_code = stop_requested ? -1 : static_cast<int>(code);
    CloseHandle(pipe);
    pipe = nullptr;
    running = false;
}

bool Process::Start(const std::vector<std::string>& args, std::string& error, bool low_priority) {
    if (args.empty()) {
        error = "nothing to run";
        return false;
    }
    SECURITY_ATTRIBUTES inheritable{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
    HANDLE read_end = nullptr, write_end = nullptr;
    if (!CreatePipe(&read_end, &write_end, &inheritable, 0)) {
        error = LastError();
        return false;
    }
    SetHandleInformation(read_end, HANDLE_FLAG_INHERIT, 0);
    HANDLE nul = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &inheritable,
                             OPEN_EXISTING, 0, nullptr);
    // only these two handles go to the child, whatever else is inheritable at the moment
    HANDLE inherit[2] = {write_end, nul};
    SIZE_T list_size = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &list_size);
    std::vector<char> list_buffer(list_size);
    auto* list = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(list_buffer.data());
    InitializeProcThreadAttributeList(list, 1, 0, &list_size);
    UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, inherit,
                              nul != INVALID_HANDLE_VALUE ? sizeof(inherit) : sizeof(HANDLE),
                              nullptr, nullptr);
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = nul != INVALID_HANDLE_VALUE ? nul : nullptr;
    startup.StartupInfo.hStdOutput = write_end;
    startup.StartupInfo.hStdError = write_end;
    startup.lpAttributeList = list;

    std::wstring command;
    for (const auto& arg : args) {
        command += (command.empty() ? L"" : L" ") + QuoteArg(Common::UTF8ToUTF16W(arg));
    }
    PROCESS_INFORMATION info{};
    // (the processes a below-normal one starts are below normal too)
    const DWORD flags = CREATE_NO_WINDOW | CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT |
                        (low_priority ? BELOW_NORMAL_PRIORITY_CLASS : 0);
    const BOOL started = CreateProcessW(nullptr, command.data(), nullptr, nullptr, TRUE, flags, nullptr,
                                        nullptr, &startup.StartupInfo, &info);
    if (!started) {
        error = LastError();
    }
    DeleteProcThreadAttributeList(list);
    CloseHandle(write_end);
    if (nul != INVALID_HANDLE_VALUE) {
        CloseHandle(nul);
    }
    if (!started) {
        CloseHandle(read_end);
        return false;
    }
    // a job holds it and everything it starts, so that Stop() ends the compilers of a build too
    impl->job = CreateJobObjectW(nullptr, nullptr);
    if (impl->job) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(impl->job, JobObjectExtendedLimitInformation, &limits, sizeof(limits));
        if (!AssignProcessToJobObject(impl->job, info.hProcess)) {
            CloseHandle(impl->job);
            impl->job = nullptr;
        }
    }
    ResumeThread(info.hThread);
    CloseHandle(info.hThread);
    impl->process = info.hProcess;
    impl->pipe = read_end;
    impl->running = true;
    impl->reader = std::thread([impl = impl.get()] { impl->Read(); });
    return true;
}

Process::~Process() {
    if (impl->running) {
        Stop();
    }
    if (impl->reader.joinable()) {
        impl->reader.join();
    }
    if (impl->process) {
        CloseHandle(impl->process);
    }
    if (impl->job) {
        CloseHandle(impl->job);
    }
}

std::string SelfPath() {
    std::wstring path(MAX_PATH, L'\0');
    for (;;) {
        const DWORD n = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
        if (n == 0) {
            return "";
        }
        if (n < path.size()) {
            path.resize(n);
            return Common::UTF16ToUTF8(path);
        }
        path.resize(path.size() * 2);
    }
}

#else  // POSIX

void Process::Impl::Kill(bool hard) {
    std::scoped_lock lock{mutex};
    if (pid > 0 && !reaped) {
        // the child leads a process group of its own: this reaches what it started too
        kill(-pid, hard ? SIGKILL : SIGTERM);
    }
}

void Process::Impl::Read() {
    char buffer[4096];
    Clock::time_point ended{}, stop_seen{};
    bool exited = false, killed = false;
    int status = 0;
    for (;;) {
        pollfd p{fd, POLLIN, 0};
        const int ready = poll(&p, 1, 50);
        if (ready > 0) {
            const ssize_t n = read(fd, buffer, sizeof(buffer));
            if (n > 0) {
                Append(buffer, static_cast<std::size_t>(n));
                continue;
            }
            if (n < 0 && (errno == EINTR || errno == EAGAIN)) {
                continue;
            }
            break;  // end of the output
        }
        if (ready < 0 && errno != EINTR) {
            break;
        }
        if (stop_requested && !killed) {
            if (stop_seen == Clock::time_point{}) {
                stop_seen = Clock::now();
            } else if (Clock::now() - stop_seen > KILL_AFTER) {
                Kill(true);
                killed = true;
            }
        }
        if (!exited) {
            std::scoped_lock lock{mutex};
            if (waitpid(pid, &status, WNOHANG) == pid) {
                exited = true;
                reaped = true;
                ended = Clock::now();
            }
        } else if (Clock::now() - ended > DRAIN_FOR) {
            break;  // something it started still holds the pipe: don't wait for that
        }
    }
    close(fd);
    fd = -1;
    if (!exited) {
        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {
        }
        std::scoped_lock lock{mutex};
        reaped = true;
    }
    exit_code = !stop_requested && WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    running = false;
}

bool Process::Start(const std::vector<std::string>& args, std::string& error, bool low_priority) {
    if (args.empty()) {
        error = "nothing to run";
        return false;
    }
    int fds[2];
    if (pipe(fds) != 0) {
        error = std::strerror(errno);
        return false;
    }
    // neither end leaks into other children started meanwhile; dup2 gives the child its own
    fcntl(fds[0], F_SETFD, FD_CLOEXEC);
    fcntl(fds[1], F_SETFD, FD_CLOEXEC);
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0);
    posix_spawn_file_actions_adddup2(&actions, fds[1], 1);
    posix_spawn_file_actions_adddup2(&actions, fds[1], 2);
    posix_spawnattr_t attributes;
    posix_spawnattr_init(&attributes);
    // a process group of its own (Stop() signals the group), default signal handling and no
    // blocked signals whatever this thread has
    posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_SETSIGMASK |
                                              POSIX_SPAWN_SETSIGDEF);
    posix_spawnattr_setpgroup(&attributes, 0);
    sigset_t none;
    sigemptyset(&none);
    posix_spawnattr_setsigmask(&attributes, &none);
    sigset_t defaults;
    sigemptyset(&defaults);
    sigaddset(&defaults, SIGPIPE);
    sigaddset(&defaults, SIGTERM);
    sigaddset(&defaults, SIGINT);
    posix_spawnattr_setsigdefault(&attributes, &defaults);

    std::vector<char*> argv;
    for (const auto& arg : args) {
        argv.push_back(const_cast<char*>(arg.c_str()));
    }
    argv.push_back(nullptr);
    pid_t pid = -1;
    const int result = posix_spawnp(&pid, argv[0], &actions, &attributes, argv.data(), environ);
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attributes);
    close(fds[1]);
    if (result != 0) {
        close(fds[0]);
        error = std::strerror(result);
        return false;
    }
    if (low_priority) {
        // nice 10, inherited by what it starts (it starts nothing before its interpreter is up)
        setpriority(PRIO_PROCESS, static_cast<id_t>(pid), 10);
    }
    impl->pid = pid;
    impl->fd = fds[0];
    impl->running = true;
    impl->reader = std::thread([impl = impl.get()] { impl->Read(); });
    return true;
}

Process::~Process() {
    if (impl->running) {
        Stop();
    }
    if (impl->reader.joinable()) {
        impl->reader.join();
    }
}

std::string SelfPath() {
#ifdef __APPLE__
    uint32_t size = 0;
    _NSGetExecutablePath(nullptr, &size);
    std::string path(size, '\0');
    if (_NSGetExecutablePath(path.data(), &size) != 0) {
        return "";
    }
    path.resize(std::strlen(path.c_str()));
    char real[PATH_MAX];
    return realpath(path.c_str(), real) ? std::string(real) : path;
#else
    char buffer[4096];
    const ssize_t n = readlink("/proc/self/exe", buffer, sizeof(buffer) - 1);
    return n > 0 ? std::string(buffer, static_cast<std::size_t>(n)) : std::string();
#endif
}

#endif

Process::Process() : impl(std::make_shared<Impl>()) {}

bool Process::Running() const {
    return impl->running;
}

void Process::Stop() {
    if (!impl->running) {
        return;
    }
    impl->stop_requested = true;
    impl->Kill(false);
}

bool Process::WasStopped() const {
    return impl->stop_requested;
}

int Process::ExitCode() const {
    return impl->exit_code;
}

std::string Process::Output() const {
    std::scoped_lock lock{impl->mutex};
    return impl->output;
}

std::vector<std::string> Process::Lines() const {
    std::vector<std::string> lines;
    std::string line;
    bool carriage_return = false;
    for (const char c : Output()) {
        if (c == '\n') {
            lines.push_back(std::move(line));
            line.clear();
            carriage_return = false;
        } else if (c == '\r') {
            carriage_return = true;
        } else {
            if (carriage_return) {
                line.clear();
                carriage_return = false;
            }
            line.push_back(c);
        }
    }
    if (!line.empty()) {
        lines.push_back(std::move(line));
    }
    return lines;
}

int Process::Wait() {
    if (impl->reader.joinable()) {
        impl->reader.join();
    }
    return impl->exit_code;
}

int RunToEnd(const std::vector<std::string>& args, std::string& output) {
    Process process;
    std::string error;
    if (!process.Start(args, error)) {
        output = error;
        return -1;
    }
    const int code = process.Wait();
    output = process.Output();
    return code;
}

} // namespace Launcher
