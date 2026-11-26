using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;
using DFlashConsoleLayout;

namespace DFlashConsoleLauncher
{
    /// <summary>
    /// Installed as &lt;root&gt;\DFlash Console.exe, the path shortcuts, Start with
    /// Windows and the uninstall entry already use. It starts the version named
    /// in current.txt (see AppLayout) and exits at once.
    /// </summary>
    internal static class Launcher
    {
        // How long the launcher watches a version that has not started well yet.
        // The first start of a new version can be slow (runtime copy, Python).
        private const int WatchMilliseconds = 10 * 60 * 1000;

        [STAThread]
        private static int Main()
        {
            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
            string[] args = Environment.GetCommandLineArgs();
            string passThrough = JoinArguments(args, 1);

            // A version on probation that exits with an error before its good-start
            // marker is chosen again at once: the second failure rolls back and
            // starts the previous version. Before, the app vanished and the user
            // had to open it twice more before the rollback happened.
            int result = 1;
            for (int round = 0; round <= AppLayout.MaxFailedStarts; round++)
            {
                bool failedEarly;
                result = LaunchOnce(root, passThrough, out failedEarly);
                if (!failedEarly) return result;
            }
            return result;
        }

        private static int LaunchOnce(string root, string passThrough, out bool failedEarly)
        {
            failedEarly = false;
            AppLayout.LaunchChoice choice;
            try
            {
                choice = AppLayout.ChooseLaunchVersion(root, version => IsRunningFrom(root, version));
            }
            catch (Exception ex)
            {
                Log(root, "choose failed: " + ex.Message);
                choice = new AppLayout.LaunchChoice { Version = AppLayout.NewestUsable(root) };
            }

            if (string.IsNullOrEmpty(choice.Version))
            {
                Log(root, "no usable app folder");
                if (DryRunPath() == null)
                {
                    MessageBox.Show(
                        "DFlash Console could not find its program files.\nRun the DFlash Console setup again to repair it.",
                        "DFlash Console",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error);
                }
                else
                {
                    RecordDryRun("NONE", "", choice);
                }
                return 1;
            }

            string exe = AppLayout.AppExe(root, choice.Version);
            if (!string.IsNullOrEmpty(choice.RolledBackFrom))
            {
                Log(root, "version " + choice.RolledBackFrom + " did not start; going back to " + choice.Version);
            }

            if (DryRunPath() != null)
            {
                RecordDryRun(exe, passThrough, choice);
                return 0;
            }

            try
            {
                ProcessStartInfo psi = new ProcessStartInfo
                {
                    FileName = exe,
                    Arguments = passThrough,
                    WorkingDirectory = Path.GetDirectoryName(exe),
                    UseShellExecute = false
                };
                psi.EnvironmentVariables["DFLASH_LAUNCHER_PATH"] = Path.Combine(root, AppLayout.ExeName);
                using (Process app = Process.Start(psi))
                {
                    if (!choice.CountedAttempt || app == null) return 0;
                    int code;
                    if (ExitedBeforeGoodStart(root, choice.Version, app, out code))
                    {
                        Log(root, "version " + choice.Version + " exited with code " + code + " before it was ready");
                        failedEarly = true;
                        return 1;
                    }
                }
                return 0;
            }
            catch (Exception ex)
            {
                Log(root, "start failed: " + ex.Message);
                MessageBox.Show(
                    "DFlash Console could not start.\n" + ex.Message,
                    "DFlash Console",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }
        }

        /// <summary>
        /// Watch a version on probation. True when it exits with an error before
        /// writing started-ok. Exit code 0 is a normal quit or a hand-off to a copy
        /// that is already running, and does not count as a failed start.
        /// </summary>
        private static bool ExitedBeforeGoodStart(string root, string version, Process app, out int exitCode)
        {
            exitCode = 0;
            DateTime until = DateTime.UtcNow.AddMilliseconds(WatchMilliseconds);
            while (DateTime.UtcNow < until)
            {
                if (AppLayout.HasStartedOk(root, version)) return false;
                if (app.WaitForExit(500))
                {
                    if (AppLayout.HasStartedOk(root, version)) return false;
                    exitCode = app.ExitCode;
                    return exitCode != 0;
                }
            }
            return false;
        }

        /// <summary>True when that version's app is already running (a second launch only hands off).</summary>
        private static bool IsRunningFrom(string root, string version)
        {
            string exe = AppLayout.AppExe(root, version);
            int self = Process.GetCurrentProcess().Id;
            foreach (Process p in Process.GetProcessesByName(Path.GetFileNameWithoutExtension(AppLayout.ExeName)))
            {
                try
                {
                    if (p.Id == self) continue;
                    if (string.Equals(p.MainModule.FileName, exe, StringComparison.OrdinalIgnoreCase)) return true;
                }
                catch { }
                finally
                {
                    p.Dispose();
                }
            }
            return false;
        }

        /// <summary>Re-quote arguments with the rules CommandLineToArgvW uses to split them.</summary>
        internal static string JoinArguments(string[] args, int start)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = start; i < args.Length; i++)
            {
                if (sb.Length > 0) sb.Append(' ');
                sb.Append(QuoteArgument(args[i] ?? ""));
            }
            return sb.ToString();
        }

        private static string QuoteArgument(string arg)
        {
            if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return arg;
            StringBuilder sb = new StringBuilder("\"");
            int backslashes = 0;
            foreach (char c in arg)
            {
                if (c == '\\')
                {
                    backslashes++;
                    continue;
                }
                if (c == '"')
                {
                    sb.Append('\\', backslashes * 2 + 1);
                    sb.Append('"');
                }
                else
                {
                    sb.Append('\\', backslashes);
                    sb.Append(c);
                }
                backslashes = 0;
            }
            sb.Append('\\', backslashes * 2);
            sb.Append('"');
            return sb.ToString();
        }

        private static string DryRunPath()
        {
            string path = Environment.GetEnvironmentVariable("DFLASH_LAUNCHER_DRY_RUN");
            return string.IsNullOrWhiteSpace(path) ? null : path;
        }

        /// <summary>Tests set DFLASH_LAUNCHER_DRY_RUN to a file: record the decision instead of starting.</summary>
        private static void RecordDryRun(string exe, string arguments, AppLayout.LaunchChoice choice)
        {
            string text = "exe=" + exe + "\n"
                + "args=" + arguments + "\n"
                + "version=" + choice.Version + "\n"
                + "rolledBackFrom=" + choice.RolledBackFrom + "\n"
                + "counted=" + (choice.CountedAttempt ? "1" : "0") + "\n";
            File.WriteAllText(DryRunPath(), text, new UTF8Encoding(false));
        }

        private static void Log(string root, string message)
        {
            try
            {
                string path = Path.Combine(root, "launcher.log");
                FileInfo info = new FileInfo(path);
                if (info.Exists && info.Length > 256 * 1024) info.Delete();
                File.AppendAllText(path, DateTime.Now.ToString("o") + " " + message + Environment.NewLine);
            }
            catch { }
        }
    }
}
