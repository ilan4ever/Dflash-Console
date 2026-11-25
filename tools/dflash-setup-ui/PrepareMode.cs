using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;
using DFlashConsoleLayout;

namespace DFlashConsoleSetup
{
    /// <summary>
    /// setup.exe /Prepare /Target="&lt;root&gt;\app-&lt;version&gt;.partial" /Result="&lt;file&gt;"
    ///
    /// Unpacks this installer's app into the target folder without a window,
    /// at below-normal priority, while the old version keeps running. The
    /// running app renames the folder to app-&lt;version&gt; and switches to it
    /// (docs/plans/side-by-side-updates.md). Writes a one-line JSON result.
    /// </summary>
    internal static class PrepareMode
    {
        internal static int Run(string[] args)
        {
            string target = (SetupForm.GetArgValue(args, "/Target=") ?? "").Trim().Trim('"');
            string result = (SetupForm.GetArgValue(args, "/Result=") ?? "").Trim().Trim('"');
            string staging = "";
            try
            {
                try { Process.GetCurrentProcess().PriorityClass = ProcessPriorityClass.BelowNormal; } catch { }
                if (string.IsNullOrEmpty(target)) throw new Exception("No /Target folder given.");
                target = Path.GetFullPath(target);
                if (!Path.GetFileName(target).StartsWith(AppLayout.AppDirPrefix, StringComparison.OrdinalIgnoreCase)
                    || !target.EndsWith(AppLayout.PartialSuffix, StringComparison.OrdinalIgnoreCase))
                {
                    throw new Exception("The target must be an app-<version>.partial folder.");
                }

                long offset;
                long length;
                if (!SetupForm.TryReadPayloadTrailer(Application.ExecutablePath, out offset, out length))
                {
                    throw new Exception("This installer carries no package.");
                }

                // Unpack next to the target, inside the install folder, so the
                // files inherit its access rules and the final move is a rename.
                staging = target + ".extract";
                DeleteDirectory(staging);
                DeleteDirectory(target);
                Directory.CreateDirectory(staging);
                string archive = Path.Combine(staging, "payload.7z");
                CopyRange(Application.ExecutablePath, archive, offset, length);
                string unpacked = Path.Combine(staging, "unpacked");
                SetupForm.ExtractPackage(archive, unpacked);
                try { File.Delete(archive); } catch { }

                string appRoot = SetupForm.ResolvePayloadRoot(unpacked);
                foreach (string required in new[]
                {
                    AppLayout.ExeName,
                    Path.Combine("resources", "launcher", AppLayout.ExeName),
                    Path.Combine("resources", "console-runtime", "server.ps1")
                })
                {
                    if (!File.Exists(Path.Combine(appRoot, required)))
                    {
                        throw new Exception("The package is incomplete (" + required + " missing).");
                    }
                }
                Directory.Move(appRoot, target);
                File.WriteAllText(Path.Combine(target, "install-version.txt"), SetupVersion.Value);
                DeleteDirectory(staging);
                WriteResult(result, true, "");
                return 0;
            }
            catch (Exception ex)
            {
                SetupForm.AppendSetupLog("prepare failed: " + ex.Message);
                DeleteDirectory(staging);
                DeleteDirectory(target);
                WriteResult(result, false, ex.Message);
                return 1;
            }
        }

        private static void CopyRange(string sourcePath, string destPath, long offset, long length)
        {
            using (FileStream source = new FileStream(sourcePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (FileStream dest = new FileStream(destPath, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                source.Seek(offset, SeekOrigin.Begin);
                byte[] buffer = new byte[1024 * 1024];
                long remaining = length;
                while (remaining > 0)
                {
                    int n = source.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                    if (n <= 0) throw new Exception("The package ended early.");
                    dest.Write(buffer, 0, n);
                    remaining -= n;
                }
            }
        }

        private static void DeleteDirectory(string path)
        {
            if (string.IsNullOrEmpty(path)) return;
            try { if (Directory.Exists(path)) Directory.Delete(path, true); } catch { }
        }

        private static void WriteResult(string path, bool ok, string error)
        {
            if (string.IsNullOrEmpty(path)) return;
            string json = "{\"ok\":" + (ok ? "true" : "false")
                + ",\"version\":\"" + Escape(SetupVersion.Value) + "\""
                + ",\"error\":\"" + Escape(error) + "\"}";
            try { File.WriteAllText(path, json, new UTF8Encoding(false)); } catch { }
        }

        private static string Escape(string text)
        {
            StringBuilder sb = new StringBuilder();
            foreach (char c in text ?? "")
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4"));
                else sb.Append(c);
            }
            return sb.ToString();
        }
    }
}
