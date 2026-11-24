using System;
using System.IO;
using System.Threading;

namespace DFlashConsoleSetup
{
    /// <summary>
    /// Wait for program files that a closing DFlash Console still holds.
    ///
    /// An in-app update force-closes the old app and then copies the new files.
    /// Windows can keep "DFlash Console.exe" locked for several seconds after
    /// the process is killed (the process is still being torn down, or
    /// antivirus is scanning the 228 MB file). Copying during that window
    /// failed with robocopy code 11 and left a half-updated install.
    /// </summary>
    internal static class InstallFileLocks
    {
        /// <summary>
        /// True when the file is missing or can be opened for exclusive write.
        /// Access denied is not a lock: it means permissions, which robocopy
        /// reports itself, so it does not make the caller wait.
        /// </summary>
        public static bool IsWritable(string path)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) return true;
            try
            {
                using (new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                {
                }
                return true;
            }
            catch (UnauthorizedAccessException)
            {
                return true;
            }
            catch (IOException)
            {
                return false;
            }
        }

        /// <summary>
        /// Wait until <see cref="IsWritable"/> or the timeout. Every
        /// <paramref name="nudgeMs"/> it calls <paramref name="onStillLocked"/>
        /// (for example, to close the app again if something restarted it).
        /// </summary>
        public static bool WaitUntilWritable(string path, int timeoutMs, Action onStillLocked, int nudgeMs)
        {
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(Math.Max(0, timeoutMs));
            DateTime nextNudge = DateTime.UtcNow.AddMilliseconds(Math.Max(250, nudgeMs));
            while (true)
            {
                if (IsWritable(path)) return true;
                if (DateTime.UtcNow >= deadline) return false;
                if (onStillLocked != null && DateTime.UtcNow >= nextNudge)
                {
                    try { onStillLocked(); } catch { }
                    nextNudge = DateTime.UtcNow.AddMilliseconds(Math.Max(250, nudgeMs));
                }
                Thread.Sleep(250);
            }
        }
    }
}
