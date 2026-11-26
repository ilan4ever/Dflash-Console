using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;

namespace DFlashConsoleLayout
{
    /// <summary>
    /// The side-by-side program layout, shared by the setup UI and the
    /// launcher (both compile this file). See docs/plans/side-by-side-updates.md.
    ///
    ///   &lt;root&gt;\DFlash Console.exe     launcher (same path as the old flat exe)
    ///   &lt;root&gt;\current.txt           version the launcher starts
    ///   &lt;root&gt;\previous.txt          version to go back to
    ///   &lt;root&gt;\rolled-back.txt       version the launcher abandoned (read by the app)
    ///   &lt;root&gt;\app-&lt;version&gt;\        one complete Electron app per version
    ///       started-ok              written by the app once it really ran
    ///       launch-attempts         starts without started-ok (launcher)
    ///       bad                     never start this version again
    ///   &lt;root&gt;\app-&lt;version&gt;.partial  being prepared; never started
    /// </summary>
    internal static class AppLayout
    {
        public const string ExeName = "DFlash Console.exe";
        public const string CurrentFile = "current.txt";
        public const string PreviousFile = "previous.txt";
        public const string RolledBackFile = "rolled-back.txt";
        public const string StartedOkFile = "started-ok";
        public const string AttemptsFile = "launch-attempts";
        public const string BadFile = "bad";
        public const string AppDirPrefix = "app-";
        public const string PartialSuffix = ".partial";
        public const int MaxFailedStarts = 2;

        private static readonly Regex VersionPattern =
            new Regex(@"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$", RegexOptions.CultureInvariant);

        public static bool IsValidVersion(string version)
        {
            return !string.IsNullOrEmpty(version) && VersionPattern.IsMatch(version);
        }

        public static string AppDir(string root, string version)
        {
            return Path.Combine(root, AppDirPrefix + version);
        }

        public static string AppExe(string root, string version)
        {
            return Path.Combine(AppDir(root, version), ExeName);
        }

        /// <summary>Numeric compare of the x.y.z part; suffixes compare as text.</summary>
        public static int CompareVersions(string left, string right)
        {
            string[] a = (left ?? "").Split(new[] { '-', '+' }, 2);
            string[] b = (right ?? "").Split(new[] { '-', '+' }, 2);
            string[] pa = a[0].Split('.');
            string[] pb = b[0].Split('.');
            for (int i = 0; i < Math.Max(pa.Length, pb.Length); i++)
            {
                long na = 0;
                long nb = 0;
                if (i < pa.Length) long.TryParse(pa[i], out na);
                if (i < pb.Length) long.TryParse(pb[i], out nb);
                if (na != nb) return na < nb ? -1 : 1;
            }
            string sa = a.Length > 1 ? a[1] : "";
            string sb = b.Length > 1 ? b[1] : "";
            // A release (no suffix) sorts after its own pre-release.
            if (sa.Length == 0 && sb.Length > 0) return 1;
            if (sa.Length > 0 && sb.Length == 0) return -1;
            return string.CompareOrdinal(sa, sb);
        }

        public static string ReadPointer(string root, string name)
        {
            try
            {
                string path = Path.Combine(root, name);
                if (!File.Exists(path)) return "";
                string value = File.ReadAllText(path).Trim();
                return IsValidVersion(value) ? value : "";
            }
            catch
            {
                return "";
            }
        }

        /// <summary>Write to a temporary file, then replace: never a half-written pointer.</summary>
        public static void WritePointer(string root, string name, string version)
        {
            WriteTextAtomic(Path.Combine(root, name), version);
        }

        public static void WriteTextAtomic(string path, string text)
        {
            string temp = path + ".tmp-" + Guid.NewGuid().ToString("N");
            File.WriteAllText(temp, text);
            try
            {
                if (File.Exists(path))
                {
                    File.Replace(temp, path, null);
                }
                else
                {
                    File.Move(temp, path);
                }
            }
            finally
            {
                try { if (File.Exists(temp)) File.Delete(temp); } catch { }
            }
        }

        public static bool IsUsable(string root, string version)
        {
            if (!IsValidVersion(version)) return false;
            string dir = AppDir(root, version);
            return File.Exists(Path.Combine(dir, ExeName)) && !File.Exists(Path.Combine(dir, BadFile));
        }

        public static List<string> InstalledVersions(string root)
        {
            List<string> versions = new List<string>();
            try
            {
                foreach (string dir in Directory.GetDirectories(root, AppDirPrefix + "*"))
                {
                    string name = Path.GetFileName(dir);
                    if (name.EndsWith(PartialSuffix, StringComparison.OrdinalIgnoreCase)) continue;
                    string version = name.Substring(AppDirPrefix.Length);
                    if (IsValidVersion(version)) versions.Add(version);
                }
            }
            catch { }
            versions.Sort(CompareVersions);
            return versions;
        }

        public static string NewestUsable(string root)
        {
            List<string> versions = InstalledVersions(root);
            for (int i = versions.Count - 1; i >= 0; i--)
            {
                if (IsUsable(root, versions[i])) return versions[i];
            }
            return "";
        }

        public static bool HasStartedOk(string root, string version)
        {
            return File.Exists(Path.Combine(AppDir(root, version), StartedOkFile));
        }

        public static int ReadAttempts(string root, string version)
        {
            try
            {
                string path = Path.Combine(AppDir(root, version), AttemptsFile);
                int n;
                if (File.Exists(path) && int.TryParse(File.ReadAllText(path).Trim(), out n)) return n;
            }
            catch { }
            return 0;
        }

        /// <summary>A fresh install of a version starts with a clean record.</summary>
        public static void ClearMarkers(string root, string version)
        {
            string dir = AppDir(root, version);
            foreach (string name in new[] { StartedOkFile, AttemptsFile, BadFile })
            {
                try { File.Delete(Path.Combine(dir, name)); } catch { }
            }
        }

        public sealed class LaunchChoice
        {
            public string Version = "";
            public string RolledBackFrom = "";
            public bool CountedAttempt;
        }

        /// <summary>
        /// Decide which version to start, counting starts of a version that has
        /// not yet proved it works and going back to previous.txt after
        /// MaxFailedStarts. A start while that version already runs (the user
        /// opening a second window) is not counted: it only hands off.
        /// </summary>
        public static LaunchChoice ChooseLaunchVersion(string root, Func<string, bool> isRunning)
        {
            LaunchChoice choice = new LaunchChoice();
            string current = ReadPointer(root, CurrentFile);
            if (IsUsable(root, current))
            {
                if (HasStartedOk(root, current) || (isRunning != null && isRunning(current)))
                {
                    choice.Version = current;
                    return choice;
                }
                int attempts = ReadAttempts(root, current);
                if (attempts < MaxFailedStarts)
                {
                    try
                    {
                        File.WriteAllText(
                            Path.Combine(AppDir(root, current), AttemptsFile),
                            (attempts + 1).ToString());
                    }
                    catch { }
                    choice.Version = current;
                    choice.CountedAttempt = true;
                    return choice;
                }
                // Started MaxFailedStarts times and never reached started-ok.
                try { File.WriteAllText(Path.Combine(AppDir(root, current), BadFile), "did not start"); } catch { }
                choice.RolledBackFrom = current;
                string previous = ReadPointer(root, PreviousFile);
                if (IsUsable(root, previous) && previous != current)
                {
                    TrySwitch(root, previous, current);
                    choice.Version = previous;
                    return choice;
                }
            }

            string newest = NewestUsable(root);
            if (newest.Length > 0)
            {
                if (newest != current)
                {
                    TrySwitch(root, newest, choice.RolledBackFrom);
                }
                choice.Version = newest;
            }
            return choice;
        }

        private static void TrySwitch(string root, string version, string rolledBackFrom)
        {
            try { WritePointer(root, CurrentFile, version); } catch { }
            if (!string.IsNullOrEmpty(rolledBackFrom))
            {
                try { WriteTextAtomic(Path.Combine(root, RolledBackFile), rolledBackFrom); } catch { }
            }
        }

        /// <summary>
        /// Remove the files of the old flat install (everything that is also a
        /// top-level entry of the new app folder) from the root. The launcher
        /// replaces the old exe, so it is skipped here. Returns names that could
        /// not be removed.
        /// </summary>
        public static List<string> RemoveFlatInstall(string root, string newAppDir)
        {
            List<string> failed = new List<string>();
            if (!Directory.Exists(newAppDir)) return failed;
            // Only a real flat install is cleaned: its app.asar sits in the root.
            if (!File.Exists(Path.Combine(root, "resources", "app.asar"))) return failed;
            foreach (string entry in Directory.GetFileSystemEntries(newAppDir))
            {
                string name = Path.GetFileName(entry);
                if (string.Equals(name, ExeName, StringComparison.OrdinalIgnoreCase)) continue;
                if (name.StartsWith(AppDirPrefix, StringComparison.OrdinalIgnoreCase)) continue;
                string target = Path.Combine(root, name);
                try
                {
                    if (Directory.Exists(target)) Directory.Delete(target, true);
                    else if (File.Exists(target)) File.Delete(target);
                }
                catch
                {
                    failed.Add(name);
                }
            }
            return failed;
        }

        /// <summary>
        /// Keep the given versions (current, previous, the one running) and
        /// delete every other app folder and every unfinished .partial folder.
        /// A folder still in use is skipped and retried next time.
        /// </summary>
        public static void RemoveOldVersions(string root, params string[] keep)
        {
            HashSet<string> keepSet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string v in keep)
            {
                if (!string.IsNullOrEmpty(v)) keepSet.Add(AppDirPrefix + v);
            }
            string[] dirs;
            try { dirs = Directory.GetDirectories(root, AppDirPrefix + "*"); }
            catch { return; }
            foreach (string dir in dirs)
            {
                string name = Path.GetFileName(dir);
                if (keepSet.Contains(name)) continue;
                try { Directory.Delete(dir, true); } catch { }
            }
        }
    }
}
