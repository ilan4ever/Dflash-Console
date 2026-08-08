using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace DFlashConsoleSetup
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupForm(Environment.GetCommandLineArgs()));
        }
    }

    internal sealed class SetupForm : Form
    {
        private static readonly Color Bg = Color.FromArgb(11, 16, 32);
        private static readonly Color TextPrimary = Color.FromArgb(229, 231, 235);
        private static readonly Color TextMuted = Color.FromArgb(148, 163, 184);
        private static readonly Color Accent = Color.FromArgb(45, 212, 191);
        private static readonly Color AccentDeep = Color.FromArgb(13, 148, 136);
        private static readonly Color ButtonIdleBg = Color.FromArgb(30, 41, 59);
        private static readonly Color ButtonIdleBorder = Color.FromArgb(51, 65, 85);
        private static readonly Color BarTrack = Color.FromArgb(17, 24, 39);
        private static readonly Color BarFill = Color.FromArgb(20, 184, 166);
        private static readonly Color BarBorder = Color.FromArgb(30, 64, 82);

        private readonly Label _title;
        private readonly Label _scopeLabel;
        private readonly RadioButton _scopeUser;
        private readonly RadioButton _scopeMachine;
        private readonly Label _pathPreview;
        private readonly Label _status;
        private readonly Panel _barHost;
        private readonly Button _finish;
        private readonly Timer _marqueeTimer;
        private readonly string[] _args;
        private readonly string _uiRoot;
        private string _payloadRoot;
        private string _destRoot;
        private readonly string _version;
        private readonly string _doneFlag;
        private readonly string _packagePath;
        private readonly bool _silent;
        private bool _perMachine;
        private bool _installStarted;
        private bool _ok;
        private bool _finishReady;
        private bool _marquee;
        private int _progressValue;
        private int _marqueeOffset;
        private string _error = "";

        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);

        private const int DwmwaUseImmersiveDarkModeBefore20H1 = 19;
        private const int DwmwaUseImmersiveDarkMode = 20;

        public SetupForm(string[] args)
        {
            _args = args ?? new string[0];
            _silent = HasSilentArg(_args);
            _packagePath = GetArgValue(_args, "/Package=");
            _uiRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
            _payloadRoot = _uiRoot;
            _destRoot = ResolveInstallRoot(_args);
            _perMachine = IsMachineInstallRoot(_destRoot);
            _doneFlag = Path.Combine(Path.GetTempPath(), "dflash-install-done.flag");
            _version = ReadVersion(_uiRoot);

            Text = "DFlash Console " + _version + " Setup";
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            MinimizeBox = true;
            ControlBox = true;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(480, 286);
            TopMost = true;
            ShowInTaskbar = true;
            Font = new Font("Segoe UI", 9.5F);
            BackColor = Bg;
            ForeColor = TextPrimary;

            _title = new Label
            {
                AutoSize = false,
                Location = new Point(22, 18),
                Size = new Size(436, 28),
                Font = new Font("Segoe UI", 12.5F, FontStyle.Bold),
                ForeColor = TextPrimary,
                BackColor = Color.Transparent,
                Text = "DFlash Console " + _version + " Setup"
            };

            _scopeLabel = new Label
            {
                AutoSize = false,
                Location = new Point(22, 52),
                Size = new Size(436, 20),
                ForeColor = TextMuted,
                BackColor = Color.Transparent,
                Text = "Install for:"
            };

            _scopeUser = CreateScopeRadio("Just for me (recommended)", 76, !_perMachine);
            _scopeMachine = CreateScopeRadio("All users on this PC", 98, _perMachine);
            _scopeUser.CheckedChanged += Scope_CheckedChanged;
            _scopeMachine.CheckedChanged += Scope_CheckedChanged;

            _pathPreview = new Label
            {
                AutoSize = false,
                Location = new Point(40, 122),
                Size = new Size(418, 34),
                ForeColor = TextMuted,
                BackColor = Color.Transparent,
                Text = _destRoot
            };

            _status = new Label
            {
                AutoSize = false,
                Location = new Point(22, 162),
                Size = new Size(436, 44),
                ForeColor = TextMuted,
                BackColor = Color.Transparent,
                Text = _silent
                    ? (string.IsNullOrEmpty(_packagePath) ? "Starting installation…" : "Preparing installation…")
                    : "Choose an install location, then click Install."
            };

            _barHost = new Panel
            {
                Location = new Point(22, 214),
                Size = new Size(436, 22),
                BackColor = BarTrack,
                Visible = _silent
            };
            _barHost.Paint += BarHost_Paint;

            _marqueeTimer = new Timer { Interval = 35 };
            _marqueeTimer.Tick += (s, e) =>
            {
                if (!_marquee) return;
                _marqueeOffset = (_marqueeOffset + 4) % Math.Max(40, _barHost.Width);
                _barHost.Invalidate();
            };

            _finish = new Button
            {
                Text = _silent ? "Finish" : "Install",
                Location = new Point(348, 238),
                Size = new Size(110, 34),
                FlatStyle = FlatStyle.Flat,
                BackColor = _silent ? ButtonIdleBg : Accent,
                ForeColor = _silent ? TextPrimary : Color.FromArgb(8, 28, 24),
                Font = new Font("Segoe UI", 10F, FontStyle.Bold),
                UseVisualStyleBackColor = false,
                Cursor = _silent ? Cursors.Default : Cursors.Hand
            };
            _finish.FlatAppearance.BorderColor = _silent ? ButtonIdleBorder : AccentDeep;
            _finish.FlatAppearance.BorderSize = 1;
            _finish.FlatAppearance.MouseOverBackColor = Color.FromArgb(94, 234, 212);
            _finish.FlatAppearance.MouseDownBackColor = AccentDeep;
            _finish.Click += Finish_Click;
            if (_silent) ApplyFinishIdleStyle();

            Controls.Add(_title);
            Controls.Add(_scopeLabel);
            Controls.Add(_scopeUser);
            Controls.Add(_scopeMachine);
            Controls.Add(_pathPreview);
            Controls.Add(_status);
            Controls.Add(_barHost);
            Controls.Add(_finish);

            UpdatePathPreview();
            SetScopeControlsVisible(!_silent);

            HandleCreated += (s, e) => ApplyDarkTitleBar();
            Resize += (s, e) =>
            {
                if (WindowState == FormWindowState.Minimized) TopMost = false;
                else if (WindowState == FormWindowState.Normal) TopMost = true;
            };

            Shown += async (s, e) =>
            {
                ApplyDarkTitleBar();
                Activate();
                BringToFront();
                if (_silent)
                {
                    await BeginInstallAsync();
                }
            };
        }

        private RadioButton CreateScopeRadio(string text, int top, bool selected)
        {
            return new RadioButton
            {
                AutoSize = true,
                Location = new Point(28, top),
                ForeColor = TextPrimary,
                BackColor = Bg,
                Text = text,
                Checked = selected
            };
        }

        private static string DefaultUserInstallRoot()
        {
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs",
                "DFlash Console");
        }

        private static string DefaultMachineInstallRoot()
        {
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                "DFlash Console");
        }

        private static bool IsMachineInstallRoot(string root)
        {
            if (string.IsNullOrWhiteSpace(root)) return false;
            try
            {
                string machineRoot = Path.GetFullPath(DefaultMachineInstallRoot()).TrimEnd('\\');
                string candidate = Path.GetFullPath(root).TrimEnd('\\');
                return string.Equals(candidate, machineRoot, StringComparison.OrdinalIgnoreCase);
            }
            catch
            {
                return false;
            }
        }

        private static string ResolveInstallRoot(string[] args)
        {
            string custom = GetArgValue(args, "/InstallRoot=");
            if (!string.IsNullOrWhiteSpace(custom)) return custom.Trim().Trim('"');

            if (HasArg(args, "/PerMachine")) return DefaultMachineInstallRoot();

            string machinePath = DefaultMachineInstallRoot();
            string userPath = DefaultUserInstallRoot();
            if (File.Exists(Path.Combine(machinePath, "DFlash Console.exe"))) return machinePath;
            if (File.Exists(Path.Combine(userPath, "DFlash Console.exe"))) return userPath;
            return userPath;
        }

        private void Scope_CheckedChanged(object sender, EventArgs e)
        {
            if (_installStarted) return;
            _perMachine = _scopeMachine.Checked;
            _destRoot = _perMachine ? DefaultMachineInstallRoot() : DefaultUserInstallRoot();
            UpdatePathPreview();
        }

        private void UpdatePathPreview()
        {
            _pathPreview.Text = _destRoot;
        }

        private void SetScopeControlsVisible(bool visible)
        {
            _scopeLabel.Visible = visible;
            _scopeUser.Visible = visible;
            _scopeMachine.Visible = visible;
            _pathPreview.Visible = visible;
        }

        private async Task BeginInstallAsync()
        {
            if (_installStarted) return;
            _installStarted = true;
            SetScopeControlsVisible(false);
            _barHost.Visible = true;
            _finish.Text = "Finish";
            ApplyFinishIdleStyle();
            _finish.Cursor = Cursors.Default;
            SetProgress(0, true);
            await RunInstallAsync();
        }

        private bool EnsureInstallAccess()
        {
            try
            {
                Directory.CreateDirectory(_destRoot);
                string probe = Path.Combine(_destRoot, ".dflash-install-write-test");
                File.WriteAllText(probe, "ok");
                File.Delete(probe);
                return true;
            }
            catch
            {
                if (_silent && !HasArg(_args, "/Elevated")) return false;
            }

            try
            {
                string args = BuildRelaunchArgs();
                ProcessStartInfo psi = new ProcessStartInfo
                {
                    FileName = Application.ExecutablePath,
                    Arguments = args,
                    UseShellExecute = true,
                    Verb = "runas"
                };
                Process.Start(psi);
                Close();
                return false;
            }
            catch
            {
                MessageBox.Show(
                    this,
                    "Administrator approval is required to install DFlash Console for all users.",
                    Text,
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning);
                _installStarted = false;
                SetScopeControlsVisible(true);
                _barHost.Visible = false;
                _finish.Text = "Install";
                _finish.BackColor = Accent;
                _finish.ForeColor = Color.FromArgb(8, 28, 24);
                _finish.Cursor = Cursors.Hand;
                return false;
            }
        }

        private string BuildRelaunchArgs()
        {
            string args = "/Elevated";
            if (!string.IsNullOrEmpty(_packagePath))
            {
                args += " /Package=\"" + _packagePath + "\"";
            }
            if (_perMachine) args += " /PerMachine";
            else args += " /InstallRoot=\"" + _destRoot + "\"";
            return args;
        }

        private void ApplyDarkTitleBar()
        {
            try
            {
                if (!IsHandleCreated) return;
                int useDark = 1;
                if (DwmSetWindowAttribute(Handle, DwmwaUseImmersiveDarkMode, ref useDark, sizeof(int)) != 0)
                {
                    DwmSetWindowAttribute(Handle, DwmwaUseImmersiveDarkModeBefore20H1, ref useDark, sizeof(int));
                }
            }
            catch { /* older Windows */ }
        }

        private void ApplyFinishIdleStyle()
        {
            _finishReady = false;
            _finish.BackColor = ButtonIdleBg;
            _finish.ForeColor = TextPrimary;
            _finish.FlatAppearance.BorderColor = ButtonIdleBorder;
            _finish.Cursor = Cursors.Default;
        }

        private void ApplyFinishReadyStyle()
        {
            _finishReady = true;
            _finish.BackColor = Accent;
            _finish.ForeColor = Color.FromArgb(8, 28, 24);
            _finish.FlatAppearance.BorderColor = AccentDeep;
            _finish.FlatAppearance.MouseOverBackColor = Color.FromArgb(94, 234, 212);
            _finish.FlatAppearance.MouseDownBackColor = AccentDeep;
            _finish.Cursor = Cursors.Hand;
        }

        private void BarHost_Paint(object sender, PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            Rectangle bounds = _barHost.ClientRectangle;
            using (SolidBrush track = new SolidBrush(BarTrack))
            {
                g.FillRectangle(track, bounds);
            }
            using (Pen border = new Pen(BarBorder))
            {
                g.DrawRectangle(border, 0, 0, bounds.Width - 1, bounds.Height - 1);
            }

            Rectangle inner = new Rectangle(2, 2, Math.Max(0, bounds.Width - 4), Math.Max(0, bounds.Height - 4));
            if (inner.Width <= 0 || inner.Height <= 0) return;

            if (_marquee)
            {
                int block = Math.Max(48, inner.Width / 4);
                int x = inner.Left + (_marqueeOffset % (inner.Width + block)) - block;
                Rectangle blockRect = new Rectangle(x, inner.Top, block, inner.Height);
                blockRect.Intersect(inner);
                if (blockRect.Width > 0)
                {
                    using (SolidBrush fill = new SolidBrush(BarFill))
                    {
                        g.FillRectangle(fill, blockRect);
                    }
                }
            }
            else
            {
                int fillWidth = (int)Math.Round(inner.Width * (Math.Max(0, Math.Min(100, _progressValue)) / 100.0));
                if (fillWidth > 0)
                {
                    using (SolidBrush fill = new SolidBrush(BarFill))
                    {
                        g.FillRectangle(fill, new Rectangle(inner.Left, inner.Top, fillWidth, inner.Height));
                    }
                }
            }
        }

        private static string GetArgValue(string[] args, string prefix)
        {
            if (args == null || string.IsNullOrEmpty(prefix)) return "";
            foreach (string raw in args)
            {
                string a = (raw ?? "").Trim();
                if (a.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                {
                    return a.Substring(prefix.Length).Trim().Trim('"');
                }
            }
            return "";
        }

        private static bool HasArg(string[] args, string value)
        {
            if (args == null) return false;
            foreach (string raw in args)
            {
                if (string.Equals((raw ?? "").Trim(), value, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        private static string ReadVersion(string root)
        {
            try
            {
                string path = Path.Combine(root, "install-version.txt");
                if (File.Exists(path))
                {
                    string v = File.ReadAllText(path).Trim();
                    if (!string.IsNullOrEmpty(v)) return v;
                }
            }
            catch { }
            return "unknown";
        }

        private void SetStatus(string text)
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action(() => SetStatus(text)));
                return;
            }
            _status.Text = text;
            _status.ForeColor = TextMuted;
            _status.Refresh();
            Application.DoEvents();
        }

        private void SetErrorStatus(string text)
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action(() => SetErrorStatus(text)));
                return;
            }
            _status.Text = text;
            _status.ForeColor = Color.FromArgb(255, 180, 150);
            _status.Refresh();
            Application.DoEvents();
        }

        private void SetProgress(int value, bool marquee)
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action(() => SetProgress(value, marquee)));
                return;
            }
            _marquee = marquee;
            _progressValue = Math.Max(0, Math.Min(100, value));
            if (marquee)
            {
                if (!_marqueeTimer.Enabled) _marqueeTimer.Start();
            }
            else
            {
                _marqueeTimer.Stop();
            }
            _barHost.Invalidate();
            Application.DoEvents();
        }

        private async Task RunInstallAsync()
        {
            try
            {
                if (!EnsureInstallAccess()) return;

                if (!string.IsNullOrEmpty(_packagePath))
                {
                    if (!File.Exists(_packagePath))
                    {
                        throw new Exception("Update package not found:\n" + _packagePath);
                    }
                    _payloadRoot = _uiRoot + "_payload";
                    SetStatus("Preparing installation…\nExtracting update package…");
                    SetProgress(2, true);
                    await Task.Run(() => ExtractPackage(_packagePath, _payloadRoot));
                    SetStatus("Package ready. Continuing install…");
                }

                string srcExe = Path.Combine(_payloadRoot, "DFlash Console.exe");
                if (!File.Exists(srcExe))
                {
                    throw new Exception("Installer package is incomplete (DFlash Console.exe missing).");
                }

                try { if (File.Exists(_doneFlag)) File.Delete(_doneFlag); } catch { }

                SetStatus("Closing previous DFlash Console…");
                SetProgress(5, true);
                await Task.Run(() => KillOtherConsoleProcesses());
                await Task.Delay(800);

                if (!Directory.Exists(_destRoot))
                {
                    Directory.CreateDirectory(_destRoot);
                }

                SetStatus("Copying program files…\nThis can take a minute on first install.");
                SetProgress(15, true);

                int rc = await Task.Run(() => RunRobocopy(_payloadRoot, _destRoot));
                if (rc >= 8)
                {
                    throw new Exception("File copy failed (code " + rc + ").");
                }

                string destExe = Path.Combine(_destRoot, "DFlash Console.exe");
                if (!File.Exists(destExe))
                {
                    throw new Exception("Installation finished but DFlash Console.exe was not found.");
                }

                SetStatus("Creating Desktop and Start Menu shortcuts…");
                SetProgress(90, true);
                await Task.Run(() => CreateShortcuts(destExe));

                File.WriteAllText(_doneFlag, _version);
                _ok = true;
                SetProgress(100, false);
                SetStatus("Installation complete.\nStarting DFlash Console…");
                _finish.Text = "Finish";
                ApplyFinishReadyStyle();
                _finish.Focus();
                await Task.Delay(_silent ? 200 : 600);
                Finish_Click(_finish, EventArgs.Empty);
            }
            catch (Exception ex)
            {
                _ok = false;
                _error = ex.Message;
                try { File.WriteAllText(_doneFlag, "install-failed " + _error); } catch { }
                SetProgress(0, false);
                SetErrorStatus("Installation failed:\n" + _error);
                _finish.Text = "Close";
                ApplyFinishReadyStyle();
            }
        }

        private static string ResolveSevenZipExe()
        {
            string[] candidates = new string[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "7-Zip", "7z.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "7-Zip", "7z.exe"),
                @"C:\Program Files\7-Zip\7z.exe",
                @"C:\Program Files (x86)\7-Zip\7z.exe"
            };
            foreach (string path in candidates)
            {
                if (!string.IsNullOrEmpty(path) && File.Exists(path)) return path;
            }
            return null;
        }

        private static void ExtractPackage(string packagePath, string extractRoot)
        {
            string sevenZip = ResolveSevenZipExe();
            if (string.IsNullOrEmpty(sevenZip))
            {
                throw new Exception("7-Zip is required to prepare this update. Install 7-Zip and try again.");
            }

            try
            {
                if (Directory.Exists(extractRoot))
                {
                    Directory.Delete(extractRoot, true);
                }
            }
            catch { }

            Directory.CreateDirectory(extractRoot);

            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = sevenZip,
                Arguments = "x \"" + packagePath + "\" -o\"" + extractRoot + "\" -y",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            using (Process p = Process.Start(psi))
            {
                if (p == null) throw new Exception("Could not start 7-Zip to extract the update package.");
                string stdout = "";
                string stderr = "";
                try { stdout = p.StandardOutput.ReadToEnd(); } catch { }
                try { stderr = p.StandardError.ReadToEnd(); } catch { }
                p.WaitForExit();
                if (p.ExitCode > 1)
                {
                    string detail = (stderr + " " + stdout).Trim();
                    if (detail.Length > 180) detail = detail.Substring(0, 180) + "…";
                    throw new Exception(
                        "Could not extract update package (7-Zip code " + p.ExitCode + ")."
                        + (string.IsNullOrEmpty(detail) ? "" : "\n" + detail));
                }
            }

            string srcExe = Path.Combine(extractRoot, "DFlash Console.exe");
            if (!File.Exists(srcExe))
            {
                throw new Exception("Extracted package is incomplete (DFlash Console.exe missing).");
            }
        }

        private static bool HasSilentArg(string[] args)
        {
            if (args == null) return false;
            foreach (string raw in args)
            {
                string a = (raw ?? "").Trim();
                if (string.Equals(a, "/S", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(a, "/silent", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(a, "--silent", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(a, "/Elevated", StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        private void CreateShortcuts(string destExe)
        {
            string desktop = _perMachine
                ? Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory)
                : Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            string startMenuPrograms = _perMachine
                ? Environment.GetFolderPath(Environment.SpecialFolder.CommonPrograms)
                : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs");
            try { Directory.CreateDirectory(startMenuPrograms); } catch { }

            CreateShortcutLink(Path.Combine(desktop, "DFlash Console.lnk"), destExe);
            CreateShortcutLink(Path.Combine(startMenuPrograms, "DFlash Console.lnk"), destExe);
        }

        private static void CreateShortcutLink(string lnkPath, string targetExe)
        {
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null) return;
                object shell = Activator.CreateInstance(shellType);
                object shortcut = shellType.InvokeMember(
                    "CreateShortcut",
                    BindingFlags.InvokeMethod,
                    null,
                    shell,
                    new object[] { lnkPath });
                Type shortcutType = shortcut.GetType();
                shortcutType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { targetExe });
                shortcutType.InvokeMember(
                    "WorkingDirectory",
                    BindingFlags.SetProperty,
                    null,
                    shortcut,
                    new object[] { Path.GetDirectoryName(targetExe) ?? "" });
                shortcutType.InvokeMember(
                    "IconLocation",
                    BindingFlags.SetProperty,
                    null,
                    shortcut,
                    new object[] { targetExe + ",0" });
                shortcutType.InvokeMember(
                    "Description",
                    BindingFlags.SetProperty,
                    null,
                    shortcut,
                    new object[] { "DFlash Console" });
                shortcutType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
            }
            catch { }
        }

        private static void KillOtherConsoleProcesses()
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo
                {
                    FileName = "taskkill.exe",
                    Arguments = "/F /IM \"DFlash Console.exe\" /T",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };
                using (Process p = Process.Start(psi))
                {
                    if (p != null) p.WaitForExit(8000);
                }
            }
            catch { }

            int myPid = Process.GetCurrentProcess().Id;
            foreach (Process p in Process.GetProcessesByName("DFlash Console"))
            {
                try
                {
                    if (p.Id != myPid)
                    {
                        p.Kill();
                        p.WaitForExit(3000);
                    }
                }
                catch { }
            }
        }

        private static int RunRobocopy(string src, string dst)
        {
            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = "robocopy.exe",
                Arguments = "\"" + src + "\" \"" + dst + "\" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /XF dflash-setup-ui.exe install-version.txt _install.cmd _install.vbs _install-ui.ps1",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };
            using (Process p = Process.Start(psi))
            {
                if (p == null) return 16;
                p.WaitForExit();
                return p.ExitCode;
            }
        }

        private async void Finish_Click(object sender, EventArgs e)
        {
            if (!_installStarted)
            {
                _perMachine = _scopeMachine.Checked;
                _destRoot = _perMachine ? DefaultMachineInstallRoot() : DefaultUserInstallRoot();
                await BeginInstallAsync();
                return;
            }

            if (!_finishReady && !_ok && string.IsNullOrEmpty(_error))
            {
                return;
            }

            if (_ok)
            {
                string destExe = Path.Combine(_destRoot, "DFlash Console.exe");
                try
                {
                    if (File.Exists(destExe))
                    {
                        Process.Start(new ProcessStartInfo
                        {
                            FileName = destExe,
                            WorkingDirectory = _destRoot,
                            Arguments = "--dflash-post-update",
                            UseShellExecute = true
                        });
                    }
                }
                catch (Exception ex)
                {
                    MessageBox.Show(this, "Could not start DFlash Console:\n" + ex.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }
            }
            Close();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                try { _marqueeTimer.Stop(); _marqueeTimer.Dispose(); } catch { }
            }
            base.Dispose(disposing);
        }
    }
}
