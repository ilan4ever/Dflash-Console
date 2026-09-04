using System;
using System.Drawing;
using System.Windows.Forms;

namespace DFlashConsoleSetup
{
    internal sealed class UninstallOptionsForm : Form
    {
        private readonly CheckBox _removeModels;
        private readonly CheckBox _removeData;
        private readonly Button _confirm;
        private readonly Button _cancel;

        public bool RemoveModels
        {
            get { return _removeModels.Checked; }
        }

        public bool RemoveData
        {
            get { return _removeData.Checked; }
        }

        public UninstallOptionsForm(string version)
        {
            Text = "Uninstall DFlash Console " + version;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent;
            ClientSize = new Size(460, 280);
            Font = new Font("Segoe UI", 9.5F);
            BackColor = Color.FromArgb(11, 16, 32);
            ForeColor = Color.FromArgb(229, 231, 235);

            Label intro = new Label
            {
                AutoSize = false,
                Location = new Point(20, 16),
                Size = new Size(420, 40),
                ForeColor = Color.FromArgb(148, 163, 184),
                BackColor = Color.Transparent,
                Text = "Remove DFlash Console from this PC. Choose what else to delete:"
            };

            _removeModels = new CheckBox
            {
                AutoSize = false,
                Location = new Point(20, 62),
                Size = new Size(420, 20),
                ForeColor = Color.FromArgb(229, 231, 235),
                BackColor = Color.Transparent,
                Text = "Delete downloaded LLM model files"
            };

            Label modelsHelp = new Label
            {
                AutoSize = false,
                Location = new Point(40, 84),
                Size = new Size(400, 52),
                ForeColor = Color.FromArgb(148, 163, 184),
                BackColor = Color.Transparent,
                Text = "Removes GGUF and other model weights in your Console model folders. "
                    + "Models stored by LM Studio, Ollama, or other apps are not affected."
            };

            _removeData = new CheckBox
            {
                AutoSize = false,
                Location = new Point(20, 144),
                Size = new Size(420, 20),
                ForeColor = Color.FromArgb(229, 231, 235),
                BackColor = Color.Transparent,
                Text = "Delete Console settings and data"
            };

            Label dataHelp = new Label
            {
                AutoSize = false,
                Location = new Point(40, 166),
                Size = new Size(400, 44),
                ForeColor = Color.FromArgb(148, 163, 184),
                BackColor = Color.Transparent,
                Text = "Removes config, logs, engine presets, and runtime installs. "
                    + "Reinstalling will run first-time setup again."
            };

            _confirm = new Button
            {
                Text = "Uninstall",
                DialogResult = DialogResult.OK,
                Location = new Point(248, 232),
                Size = new Size(96, 32),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(220, 38, 38),
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 9.5F, FontStyle.Bold)
            };
            _confirm.FlatAppearance.BorderSize = 0;

            _cancel = new Button
            {
                Text = "Cancel",
                DialogResult = DialogResult.Cancel,
                Location = new Point(352, 232),
                Size = new Size(88, 32),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(30, 41, 59),
                ForeColor = Color.FromArgb(229, 231, 235)
            };
            _cancel.FlatAppearance.BorderColor = Color.FromArgb(51, 65, 85);

            Controls.Add(intro);
            Controls.Add(_removeModels);
            Controls.Add(modelsHelp);
            Controls.Add(_removeData);
            Controls.Add(dataHelp);
            Controls.Add(_confirm);
            Controls.Add(_cancel);
            AcceptButton = _confirm;
            CancelButton = _cancel;
        }
    }
}
