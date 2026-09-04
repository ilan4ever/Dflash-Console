using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

namespace DFlashConsoleSetup
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string[] args = Environment.GetCommandLineArgs();
            if (SetupForm.HasArg(args, "/Uninstall") && SetupForm.RelocateUninstallerIfNeeded(args))
            {
                return;
            }
            Application.Run(new SetupForm(args));
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
        private readonly bool _autoInstall;
        private readonly bool _uninstall;
        private bool _perMachine;
        private bool _installStarted;
        private bool _ok;
        private bool _appLaunchStarted;
        private bool _firstRunLaunch;
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
            _packagePath = GetArgValue(_args, "/Package=");
            if (string.IsNullOrEmpty(_packagePath))
            {
                _packagePath = (Environment.GetEnvironmentVariable("DFLASH_SETUP_PACKAGE") ?? "").Trim();
            }
            _autoInstall = HasAutoInstallArg(_args)
                || string.Equals(Environment.GetEnvironmentVariable("DFLASH_SETUP_AUTOINSTALL"), "1", StringComparison.OrdinalIgnoreCase);
            _uninstall = HasArg(_args, "/Uninstall");
            // 7-Zip SFX with GUIMode=2 forwards /S to this exe. That is extraction
            // silence, not "skip the install-location choice". Only skip the dialog
            // after the user already picked a scope (/Elevated UAC relaunch) or for
            // an in-app update that explicitly passed /AutoInstall.
            _silent = _uninstall || HasArg(_args, "/Elevated") || _autoInstall;
            _uiRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
            _payloadRoot = ResolvePayloadRoot(_uiRoot);
            _destRoot = ResolveInstallRoot(_args);
            _perMachine = IsMachineInstallRoot(_destRoot);
            _doneFlag = Path.Combine(Path.GetTempPath(), "dflash-install-done.flag");
            _version = ReadVersion(_uiRoot, _destRoot);
            WriteSetupLog();

            Text = "DFlash Console " + _version + " Setup";
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            MinimizeBox = true;
            ControlBox = true;
            AutoScaleMode = AutoScaleMode.Font;
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
                Text = _uninstall
                    ? "Removing DFlash Console…"
                    : (_silent
                    ? (string.IsNullOrEmpty(_packagePath) ? "Starting installation…" : "Preparing installation…")
                    : "Choose an install location, then click Install.")
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
                if (_uninstall)
                {
                    await RunUninstallAsync();
                    return;
                }
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
                FlatStyle = FlatStyle.Flat,
                UseVisualStyleBackColor = false,
                Padding = new Padding(2, 0, 0, 0),
                Text = text,
                Checked = selected
            };
        }

        private static string ResolvePayloadRoot(string uiRoot)
        {
            string[] candidates = new string[]
            {
                Path.Combine(uiRoot, "app"),
                uiRoot
            };
            foreach (string candidate in candidates)
            {
                if (File.Exists(Path.Combine(candidate, "DFlash Console.exe"))) return candidate;
            }
            return uiRoot;
        }

        private static bool IsUnsafeInstallRoot(string root)
        {
            if (string.IsNullOrWhiteSpace(root)) return true;
            try
            {
                string full = Path.GetFullPath(root);
                string temp = Path.GetFullPath(Path.GetTempPath());
                if (full.StartsWith(temp, StringComparison.OrdinalIgnoreCase)
                    && (full.IndexOf("\\7z", StringComparison.OrdinalIgnoreCase) >= 0
                        || full.IndexOf("\\DFlash-Console-updates", StringComparison.OrdinalIgnoreCase) >= 0))
                {
                    return true;
                }
            }
            catch { }
            return false;
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
            string envRoot = (Environment.GetEnvironmentVariable("DFLASH_SETUP_INSTALLROOT") ?? "").Trim().Trim('"');
            if (!string.IsNullOrWhiteSpace(envRoot) && !IsUnsafeInstallRoot(envRoot)) return envRoot;

            string custom = GetArgValue(args, "/InstallRoot=");
            if (!string.IsNullOrWhiteSpace(custom) && !IsUnsafeInstallRoot(custom)) return custom.Trim().Trim('"');

            if (HasArg(args, "/PerMachine")
                || string.Equals(Environment.GetEnvironmentVariable("DFLASH_SETUP_PER_MACHINE"), "1", StringComparison.OrdinalIgnoreCase))
            {
                return DefaultMachineInstallRoot();
            }

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
            string args = "/Elevated /AutoInstall";
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

        internal static bool HasArg(string[] args, string value)
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

        private static string ReadVersion(string uiRoot, string installRoot)
        {
            // Always prefer the version baked into this setup.exe. Reading the
            // destination install-version.txt or Apps & Features DisplayVersion
            // first reused the *already installed* label (e.g. 0.3.86) during
            // upgrades and then wrote that stale value back onto the new files.
            if (!string.IsNullOrWhiteSpace(SetupVersion.Value))
            {
                return SetupVersion.Value.Trim();
            }

            foreach (string dir in new[] { uiRoot })
            {
                if (string.IsNullOrWhiteSpace(dir)) continue;
                try
                {
                    string path = Path.Combine(dir, "install-version.txt");
                    if (File.Exists(path))
                    {
                        string v = File.ReadAllText(path).Trim();
                        if (!string.IsNullOrEmpty(v)) return v;
                    }
                }
                catch { }
            }

            foreach (string pkgPath in PayloadPackageJsonPaths(uiRoot, installRoot))
            {
                try
                {
                    if (!File.Exists(pkgPath)) continue;
                    string json = File.ReadAllText(pkgPath);
                    int idx = json.IndexOf("\"version\"", StringComparison.OrdinalIgnoreCase);
                    if (idx < 0) continue;
                    int start = json.IndexOf('"', idx + 9);
                    int end = json.IndexOf('"', start + 1);
                    if (start < 0 || end <= start) continue;
                    string v = json.Substring(start + 1, end - start - 1).Trim();
                    if (!string.IsNullOrEmpty(v)) return v;
                }
                catch { }
            }

            return "unknown";
        }

        private static string[] PayloadPackageJsonPaths(string uiRoot, string installRoot)
        {
            return new[]
            {
                Path.Combine(uiRoot ?? "", "app", "resources", "app", "package.json"),
                Path.Combine(uiRoot ?? "", "resources", "app", "package.json"),
                Path.Combine(installRoot ?? "", "resources", "app", "package.json"),
            };
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
                    _payloadRoot = ResolvePayloadRoot(_payloadRoot);
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

                try
                {
                    File.WriteAllText(Path.Combine(_destRoot, "install-version.txt"), _version);
                }
                catch { }

                string destExe = Path.Combine(_destRoot, "DFlash Console.exe");
                if (!File.Exists(destExe))
                {
                    throw new Exception("Installation finished but DFlash Console.exe was not found.");
                }

                SetStatus("Preparing Console data folder…\nCopying runtime files for first run.");
                SetProgress(55, true);
                bool configExisted = ConsoleConfigExisted();
                string dataRoot = await Task.Run(() => BootstrapDataRoot(_destRoot));
                bool firstRun = !configExisted && string.IsNullOrEmpty(_packagePath);
                _firstRunLaunch = firstRun;

                SetStatus("Creating Desktop and Start Menu shortcuts…");
                SetProgress(70, true);
                await Task.Run(() => CreateShortcuts(destExe));

                SetStatus("Registering in Windows Apps & features…");
                SetProgress(75, true);
                await Task.Run(() => RegisterUninstall(destExe));

                SetStatus("Installing Transformers engine…\nThis can take several minutes on first install.");
                SetProgress(80, true);
                try
                {
                    await Task.Run(() => InstallTransformersRuntime(dataRoot));
                }
                catch (Exception tfEx)
                {
                    SetStatus("Program files are installed.\nTransformers will finish later in the app if needed.\n" + tfEx.Message);
                }

                File.WriteAllText(_doneFlag, _version);
                _ok = true;
                SetProgress(100, false);
                _finish.Text = "Finish";
                ApplyFinishReadyStyle();
                if (!LaunchInstalledApp(firstRun))
                {
                    SetStatus("Installation complete.\nDFlash Console is ready from the Start menu.");
                    _finish.Focus();
                    return;
                }
                SetStatus("Installation complete.\nStarting DFlash Console…");
                await Task.Delay(_silent ? 200 : 600);
                Close();
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

            if (!File.Exists(Path.Combine(ResolvePayloadRoot(extractRoot), "DFlash Console.exe")))
            {
                throw new Exception("Extracted package is incomplete (DFlash Console.exe missing).");
            }
        }

        private void WriteSetupLog()
        {
            try
            {
                string line = DateTime.Now.ToString("o")
                    + " version=" + _version
                    + " silent=" + _silent
                    + " autoInstall=" + _autoInstall
                    + " dest=" + _destRoot
                    + " args=" + string.Join(" ", _args ?? new string[0])
                    + Environment.NewLine;
                File.AppendAllText(Path.Combine(Path.GetTempPath(), "dflash-setup-ui.log"), line);
            }
            catch { }
        }

        private const string UninstallKeyName = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\DFlashConsole";

        internal static bool RelocateUninstallerIfNeeded(string[] args)
        {
            string dest = GetArgValue(args, "/InstallRoot=");
            if (string.IsNullOrWhiteSpace(dest))
            {
                dest = HasArg(args, "/PerMachine") ? DefaultMachineInstallRoot() : DefaultUserInstallRoot();
            }
            dest = dest.Trim().Trim('"');
            string myPath = Application.ExecutablePath;
            string destDir = "";
            try
            {
                string myDir = Path.GetFullPath(Path.GetDirectoryName(myPath) ?? "").TrimEnd('\\');
                destDir = Path.GetFullPath(dest).TrimEnd('\\');
                if (!string.Equals(myDir, destDir, StringComparison.OrdinalIgnoreCase)
                    && !myDir.StartsWith(destDir + "\\", StringComparison.OrdinalIgnoreCase))
                {
                    return false;
                }
            }
            catch
            {
                return false;
            }

            string tempUi = Path.Combine(Path.GetTempPath(), "dflash-setup-ui-uninstall.exe");
            File.Copy(myPath, tempUi, true);
            string versionFile = Path.Combine(destDir, "install-version.txt");
            if (File.Exists(versionFile))
            {
                try
                {
                    File.Copy(versionFile, Path.Combine(Path.GetTempPath(), "dflash-install-version.txt"), true);
                }
                catch { }
            }
            string argLine = "/Uninstall /InstallRoot=\"" + dest + "\"";
            if (HasArg(args, "/PerMachine")) argLine += " /PerMachine";
            if (HasArg(args, "/S") || HasArg(args, "/silent") || HasArg(args, "--silent")) argLine += " /S";
            Process.Start(new ProcessStartInfo
            {
                FileName = tempUi,
                Arguments = argLine,
                UseShellExecute = true
            });
            return true;
        }

        private void RegisterUninstall(string destExe)
        {
            string uiExe = Path.Combine(_destRoot, "dflash-setup-ui.exe");
            try { File.Copy(Application.ExecutablePath, uiExe, true); } catch { }
            try { File.WriteAllText(Path.Combine(_destRoot, "install-version.txt"), _version); } catch { }
            if (!File.Exists(uiExe)) uiExe = destExe;

            string uninstallArgs = "/Uninstall /InstallRoot=\"" + _destRoot + "\"";
            if (_perMachine) uninstallArgs += " /PerMachine";
            RegistryHive hive = _perMachine ? RegistryHive.LocalMachine : RegistryHive.CurrentUser;
            using (RegistryKey baseKey = RegistryKey.OpenBaseKey(hive, RegistryView.Registry64))
            using (RegistryKey key = baseKey.CreateSubKey(UninstallKeyName))
            {
                if (key == null) throw new Exception("Could not write the Windows Apps uninstall entry.");
                key.SetValue("DisplayName", "DFlash Console");
                key.SetValue("DisplayVersion", _version);
                key.SetValue("Publisher", "ILAN AVIV");
                key.SetValue("InstallLocation", _destRoot);
                key.SetValue("DisplayIcon", destExe);
                key.SetValue("UninstallString", "\"" + uiExe + "\" " + uninstallArgs);
                key.SetValue("QuietUninstallString", "\"" + uiExe + "\" " + uninstallArgs + " /S");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                key.SetValue("EstimatedSize", EstimateDirectoryKb(_destRoot), RegistryValueKind.DWord);
                key.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"));
                key.SetValue("HelpLink", "https://github.com/ilan4ever/Dflash-Console");
                key.SetValue("URLInfoAbout", "https://github.com/ilan4ever/Dflash-Console");
            }
        }

        private static int EstimateDirectoryKb(string root)
        {
            long bytes = 0;
            try
            {
                foreach (string file in Directory.GetFiles(root, "*", SearchOption.AllDirectories))
                {
                    try { bytes += new FileInfo(file).Length; } catch { }
                }
            }
            catch { }
            long kb = bytes / 1024L;
            if (kb > int.MaxValue) return int.MaxValue;
            return (int)kb;
        }

        private void UnregisterUninstall()
        {
            foreach (RegistryHive hive in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
            {
                try
                {
                    using (RegistryKey baseKey = RegistryKey.OpenBaseKey(hive, RegistryView.Registry64))
                    {
                        baseKey.DeleteSubKeyTree(UninstallKeyName, false);
                    }
                }
                catch { }
            }
        }

        private void RemoveShortcuts()
        {
            string[] links = new string[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "DFlash Console.lnk"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory), "DFlash Console.lnk"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", "DFlash Console.lnk"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonPrograms), "DFlash Console.lnk")
            };
            foreach (string link in links)
            {
                try { if (File.Exists(link)) File.Delete(link); } catch { }
            }
        }

        private async Task RunUninstallAsync()
        {
            try
            {
                _installStarted = true;
                bool removeData = HasArg(_args, "/RemoveData");
                bool removeModels = HasArg(_args, "/RemoveModels");
                if (!HasArg(_args, "/S") && !HasArg(_args, "/silent") && !HasArg(_args, "--silent"))
                {
                    using (UninstallOptionsForm options = new UninstallOptionsForm(_version))
                    {
                        if (options.ShowDialog(this) != DialogResult.OK)
                        {
                            Close();
                            return;
                        }
                        removeModels = options.RemoveModels;
                        removeData = options.RemoveData;
                    }
                }

                SetScopeControlsVisible(false);
                _barHost.Visible = true;
                SetProgress(10, true);
                SetStatus("Closing DFlash Console…");
                await Task.Run(() => KillOtherConsoleProcesses());
                await Task.Delay(400);

                SetProgress(40, true);
                SetStatus(BuildUninstallStatus(removeModels, removeData));
                await Task.Run(() =>
                {
                    RemoveShortcuts();
                    UnregisterUninstall();
                    if (removeData)
                    {
                        RemoveConsoleUserData(_destRoot);
                    }
                    else if (removeModels)
                    {
                        RemoveModelFiles(_destRoot);
                    }
                    if (Directory.Exists(_destRoot))
                    {
                        try { Directory.Delete(_destRoot, true); }
                        catch
                        {
                            ProcessStartInfo psi = new ProcessStartInfo
                            {
                                FileName = "cmd.exe",
                                Arguments = "/c ping 127.0.0.1 -n 3 > nul & rmdir /s /q \"" + _destRoot + "\"",
                                UseShellExecute = false,
                                CreateNoWindow = true
                            };
                            Process.Start(psi);
                        }
                    }
                });

                _ok = true;
                SetProgress(100, false);
                SetStatus(BuildUninstallCompleteMessage(removeModels, removeData));
                _finish.Text = "Close";
                ApplyFinishReadyStyle();
            }
            catch (Exception ex)
            {
                _ok = false;
                SetErrorStatus("Uninstall failed:\n" + ex.Message);
                _finish.Text = "Close";
                ApplyFinishReadyStyle();
            }
        }

        private static string BuildUninstallStatus(bool removeModels, bool removeData)
        {
            if (removeData)
            {
                return "Removing shortcuts, program files, and Console data…";
            }
            if (removeModels)
            {
                return "Removing shortcuts, program files, and downloaded model files…";
            }
            return "Removing shortcuts and program files…";
        }

        private static string BuildUninstallCompleteMessage(bool removeModels, bool removeData)
        {
            if (removeData)
            {
                return "DFlash Console and its saved settings were removed.";
            }
            if (removeModels)
            {
                return "DFlash Console was removed.\nDownloaded model files were deleted.\nYour settings were kept.";
            }
            return "DFlash Console was removed.\nYour settings and model folders were kept.";
        }

        private static bool HasAutoInstallArg(string[] args)
        {
            if (args == null) return false;
            foreach (string raw in args)
            {
                string a = (raw ?? "").Trim();
                if (string.Equals(a, "/AutoInstall", StringComparison.OrdinalIgnoreCase))
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

        private bool LaunchInstalledApp(bool firstRun)
        {
            if (_appLaunchStarted) return true;
            string destExe = Path.Combine(_destRoot, "DFlash Console.exe");
            if (!File.Exists(destExe)) return false;
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = destExe,
                    WorkingDirectory = _destRoot,
                    Arguments = firstRun ? "--dflash-post-install" : "--dflash-post-update",
                    UseShellExecute = true
                });
                _appLaunchStarted = true;
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static bool ConsoleConfigExisted()
        {
            string dataRoot = ResolveConsoleDataRoot("");
            return File.Exists(Path.Combine(dataRoot, "config.json"));
        }

        private static string ResolveConsoleDataRoot(string installRoot)
        {
            string appData = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "DFlash Console");
            try
            {
                string pointer = Path.Combine(appData, "console-root.json");
                if (File.Exists(pointer))
                {
                    string json = File.ReadAllText(pointer);
                    int idx = json.IndexOf("\"root\"", StringComparison.OrdinalIgnoreCase);
                    if (idx >= 0)
                    {
                        int start = json.IndexOf('"', idx + 6);
                        int end = json.IndexOf('"', start + 1);
                        if (start >= 0 && end > start)
                        {
                            string root = json.Substring(start + 1, end - start - 1).Trim();
                            if (!string.IsNullOrEmpty(root)) return root;
                        }
                    }
                }
            }
            catch { }

            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                "DFlash Console");
        }

        private static void RemoveConsoleUserData(string installRoot)
        {
            string dataRoot = ResolveConsoleDataRoot(installRoot);
            try
            {
                if (Directory.Exists(dataRoot))
                {
                    Directory.Delete(dataRoot, true);
                }
            }
            catch { }

            string appData = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "DFlash Console");
            try
            {
                if (Directory.Exists(appData))
                {
                    Directory.Delete(appData, true);
                }
            }
            catch { }
        }

        private static void RemoveModelFiles(string installRoot)
        {
            foreach (string path in CollectModelLibraryPaths(installRoot))
            {
                try
                {
                    if (Directory.Exists(path))
                    {
                        Directory.Delete(path, true);
                    }
                    else if (File.Exists(path))
                    {
                        File.Delete(path);
                    }
                }
                catch { }
            }
        }

        private static string[] CollectModelLibraryPaths(string installRoot)
        {
            string dataRoot = ResolveConsoleDataRoot(installRoot);
            System.Collections.Generic.List<string> paths = new System.Collections.Generic.List<string>();
            System.Collections.Generic.HashSet<string> seen = new System.Collections.Generic.HashSet<string>(
                StringComparer.OrdinalIgnoreCase);

            string configPath = Path.Combine(dataRoot, "config.json");
            if (!File.Exists(configPath))
            {
                AddModelPath(paths, seen, Path.Combine(dataRoot, "models"));
                return paths.ToArray();
            }

            string json = File.ReadAllText(configPath);
            string dflashRoot = ExtractJsonStringValue(json, "dflash_root");
            if (string.IsNullOrWhiteSpace(dflashRoot))
            {
                dflashRoot = dataRoot;
            }
            else
            {
                dflashRoot = ResolveConfigPath(dflashRoot, dataRoot);
            }

            string modelsRoot = ExtractJsonStringValue(json, "models_root");
            if (!string.IsNullOrWhiteSpace(modelsRoot))
            {
                AddModelPath(paths, seen, ResolveConfigPath(modelsRoot, dflashRoot));
            }
            else
            {
                AddModelPath(paths, seen, Path.Combine(dflashRoot, "models"));
            }

            foreach (string libraryPath in ExtractModelLibraryPaths(json))
            {
                AddModelPath(paths, seen, ResolveConfigPath(libraryPath, dflashRoot));
            }

            return paths.ToArray();
        }

        private static void AddModelPath(
            System.Collections.Generic.List<string> paths,
            System.Collections.Generic.HashSet<string> seen,
            string path)
        {
            if (string.IsNullOrWhiteSpace(path)) return;
            try
            {
                string full = Path.GetFullPath(path.Trim().Trim('"'));
                if (seen.Add(full))
                {
                    paths.Add(full);
                }
            }
            catch { }
        }

        private static string ResolveConfigPath(string rawPath, string baseRoot)
        {
            string path = (rawPath ?? "").Trim().Trim('"');
            if (string.IsNullOrWhiteSpace(path)) return baseRoot;
            if (Path.IsPathRooted(path)) return path;
            return Path.GetFullPath(Path.Combine(baseRoot, path));
        }

        private static string ExtractJsonStringValue(string json, string key)
        {
            if (string.IsNullOrEmpty(json) || string.IsNullOrEmpty(key)) return "";
            string token = "\"" + key + "\"";
            int idx = json.IndexOf(token, StringComparison.OrdinalIgnoreCase);
            if (idx < 0) return "";
            int colon = json.IndexOf(':', idx + token.Length);
            if (colon < 0) return "";
            int start = json.IndexOf('"', colon + 1);
            if (start < 0) return "";
            int end = json.IndexOf('"', start + 1);
            if (end <= start) return "";
            return json.Substring(start + 1, end - start - 1).Trim();
        }

        private static string[] ExtractModelLibraryPaths(string json)
        {
            System.Collections.Generic.List<string> paths = new System.Collections.Generic.List<string>();
            if (string.IsNullOrEmpty(json)) return paths.ToArray();

            int section = json.IndexOf("\"model_libraries\"", StringComparison.OrdinalIgnoreCase);
            if (section < 0) return paths.ToArray();
            int arrayStart = json.IndexOf('[', section);
            if (arrayStart < 0) return paths.ToArray();
            int arrayEnd = json.IndexOf(']', arrayStart);
            if (arrayEnd < arrayStart) return paths.ToArray();

            string libraries = json.Substring(arrayStart, arrayEnd - arrayStart + 1);
            int search = 0;
            while (true)
            {
                int pathKey = libraries.IndexOf("\"path\"", search, StringComparison.OrdinalIgnoreCase);
                if (pathKey < 0) break;
                int colon = libraries.IndexOf(':', pathKey + 6);
                if (colon < 0) break;
                int quoteStart = libraries.IndexOf('"', colon + 1);
                if (quoteStart < 0) break;
                int quoteEnd = libraries.IndexOf('"', quoteStart + 1);
                if (quoteEnd <= quoteStart) break;
                string value = libraries.Substring(quoteStart + 1, quoteEnd - quoteStart - 1).Trim();
                if (!string.IsNullOrEmpty(value))
                {
                    paths.Add(value);
                }
                search = quoteEnd + 1;
            }

            return paths.ToArray();
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

        private static string ResolvePowerShellExe()
        {
            string[] candidates = new string[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "PowerShell", "7", "pwsh.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "PowerShell", "7", "pwsh.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe")
            };
            foreach (string path in candidates)
            {
                if (!string.IsNullOrEmpty(path) && File.Exists(path)) return path;
            }
            throw new Exception("PowerShell was not found. Install PowerShell 7 or Windows PowerShell and try again.");
        }

        private static string BootstrapDataRoot(string programRoot)
        {
            string script = Path.Combine(programRoot, "resources", "console-runtime", "scripts", "bootstrap-installed-data-root.ps1");
            if (!File.Exists(script))
            {
                script = Path.Combine(programRoot, "scripts", "bootstrap-installed-data-root.ps1");
            }
            if (!File.Exists(script))
            {
                throw new Exception("Installer bootstrap script is missing (bootstrap-installed-data-root.ps1).");
            }

            string shell = ResolvePowerShellExe();
            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = shell,
                Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -ProgramRoot \"" + programRoot + "\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            using (Process p = Process.Start(psi))
            {
                if (p == null) throw new Exception("Could not start Console data bootstrap.");
                string stdout = "";
                string stderr = "";
                try { stdout = p.StandardOutput.ReadToEnd(); } catch { }
                try { stderr = p.StandardError.ReadToEnd(); } catch { }
                p.WaitForExit();
                if (p.ExitCode != 0)
                {
                    string detail = (stderr + " " + stdout).Trim();
                    if (detail.Length > 220) detail = detail.Substring(0, 220) + "…";
                    throw new Exception(
                        "Could not prepare the Console data folder (code " + p.ExitCode + ")."
                        + (string.IsNullOrEmpty(detail) ? "" : "\n" + detail));
                }
                string[] lines = stdout.Trim().Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
                string dataRoot = lines.Length > 0 ? lines[lines.Length - 1] : "";
                if (string.IsNullOrWhiteSpace(dataRoot))
                {
                    dataRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "DFlash Console");
                }
                return dataRoot.Trim();
            }
        }

        private static void InstallTransformersRuntime(string dataRoot)
        {
            string bundleSrc = Path.Combine(dataRoot, "runtime-bundles", "transformers", "server.py");
            if (!File.Exists(bundleSrc))
            {
                throw new Exception("Transformers runtime bundle is missing from the installed Console data folder.");
            }

            string manifestPath = Path.Combine(dataRoot, "runtimes", "transformers", "manifest.json");
            if (File.Exists(manifestPath))
            {
                return;
            }

            string script = Path.Combine(dataRoot, "scripts", "install-transformers-runtime.ps1");
            if (!File.Exists(script))
            {
                throw new Exception("Transformers install script is missing from the Console data folder.");
            }

            string shell = ResolvePowerShellExe();
            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = shell,
                Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -Root \"" + dataRoot + "\" -TorchVariant auto",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            using (Process p = Process.Start(psi))
            {
                if (p == null) throw new Exception("Could not start Transformers installation.");
                string stdout = "";
                string stderr = "";
                try { stdout = p.StandardOutput.ReadToEnd(); } catch { }
                try { stderr = p.StandardError.ReadToEnd(); } catch { }
                p.WaitForExit();
                if (p.ExitCode != 0 || !File.Exists(manifestPath))
                {
                    string detail = (stderr + " " + stdout).Trim();
                    if (detail.Length > 220) detail = detail.Substring(0, 220) + "…";
                    throw new Exception(
                        "Transformers installation failed (code " + p.ExitCode + ")."
                        + (string.IsNullOrEmpty(detail) ? "" : "\n" + detail));
                }
            }
        }

        private async void Finish_Click(object sender, EventArgs e)
        {
            if (_uninstall)
            {
                if (_finishReady || _ok) Close();
                return;
            }
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
                if (!LaunchInstalledApp(_firstRunLaunch))
                {
                    MessageBox.Show(this, "DFlash Console was installed, but could not be started.\nYou can launch it from the Start menu.", Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
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
