//! Spawning yt-dlp so that its whole process tree (yt-dlp's PyInstaller
//! bootloader + Python child + ffmpeg/ffprobe) can be killed at once.
//!
//! * Windows: every child is placed in a Job Object with
//!   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; cancel calls `TerminateJobObject`.
//!   Grandchildren inherit the job, and if viddown itself dies the OS closes
//!   the handle and kills the tree. `taskkill /T /F` is the fallback when a job
//!   could not be created.
//! * Unix: the child leads its own process group; cancel sends SIGTERM to the
//!   group and SIGKILL two seconds later.
//!
//! Killing never deletes anything: yt-dlp's `.part` / `.ytdl` files stay on
//! disk, so the next run with `--continue` resumes.

use tokio::process::{Child, Command};

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Platform setup that must happen before spawn.
pub fn prepare(cmd: &mut Command) {
    cmd.kill_on_drop(false);
    #[cfg(windows)]
    {
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(unix)]
    {
        cmd.process_group(0);
    }
}

/// Handle used to kill a spawned process and all of its descendants.
pub struct ProcTree {
    pid: u32,
    #[cfg(windows)]
    job: Option<win::Job>,
}

impl ProcTree {
    /// Call immediately after spawning.
    pub fn attach(child: &Child) -> Option<Self> {
        let pid = child.id()?;
        #[cfg(windows)]
        {
            let job = child.raw_handle().and_then(|h| win::Job::assign(h as _));
            Some(Self { pid, job })
        }
        #[cfg(not(windows))]
        {
            Some(Self { pid })
        }
    }

    pub fn kill(&self) {
        #[cfg(windows)]
        {
            if let Some(job) = &self.job {
                if job.terminate() {
                    return;
                }
            }
            let _ = std::process::Command::new("taskkill")
                .args(["/T", "/F", "/PID", &self.pid.to_string()])
                .creation_flags_no_window()
                .status();
        }
        #[cfg(unix)]
        {
            let pgid = self.pid as libc::pid_t;
            // SAFETY: plain syscalls on a process group we created.
            unsafe {
                libc::kill(-pgid, libc::SIGTERM);
            }
            std::thread::spawn(move || {
                std::thread::sleep(std::time::Duration::from_secs(2));
                unsafe {
                    libc::kill(-pgid, libc::SIGKILL);
                }
            });
        }
    }
}

#[cfg(windows)]
trait NoWindow {
    fn creation_flags_no_window(&mut self) -> &mut Self;
}

#[cfg(windows)]
impl NoWindow for std::process::Command {
    fn creation_flags_no_window(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        self.creation_flags(CREATE_NO_WINDOW)
    }
}

/// `std::process::Command` for short helper runs (versions etc.) with no console window.
pub fn quiet_std_command(program: &std::path::Path) -> std::process::Command {
    #[allow(unused_mut)]
    let mut cmd = std::process::Command::new(program);
    #[cfg(windows)]
    cmd.creation_flags_no_window();
    cmd
}

#[cfg(windows)]
mod win {
    use std::ffi::c_void;
    use std::mem::{size_of, zeroed};
    use std::ptr::null;

    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
        SetInformationJobObject, TerminateJobObject,
    };

    pub struct Job(HANDLE);

    // SAFETY: a job handle is a kernel object handle, usable from any thread.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn assign(process: HANDLE) -> Option<Self> {
            // SAFETY: standard Win32 job object setup; every handle is checked.
            unsafe {
                let job = CreateJobObjectW(null(), null());
                if job.is_null() {
                    return None;
                }
                let job = Job(job);
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    job.0,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const c_void,
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if ok == 0 || AssignProcessToJobObject(job.0, process) == 0 {
                    return None;
                }
                Some(job)
            }
        }

        pub fn terminate(&self) -> bool {
            // SAFETY: valid job handle owned by self.
            unsafe { TerminateJobObject(self.0, 1) != 0 }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            // Closing the last handle kills anything still in the job.
            // SAFETY: handle owned by self, closed exactly once.
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}
