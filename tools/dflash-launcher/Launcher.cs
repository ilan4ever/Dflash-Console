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
        [STAThread]
        private static int Main()
        {
            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
            string[] args = Environment.GetCommandLineArgs();
            string passThrough = JoinArguments(args, 1);

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
                Process.Start(psi);
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
