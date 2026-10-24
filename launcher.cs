using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

class YukiLauncher {
    [STAThread]
    static void Main() {
        string dir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        string exe = Path.Combine(dir, "runtime", "electron", "electron.exe");
        if (!File.Exists(exe)) {
            MessageBox.Show("??? runtime\\electron\\electron.exe????????????", "yuki", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        Environment.SetEnvironmentVariable("ELECTRON_RUN_AS_NODE", null);
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = exe;
        psi.Arguments = "--no-sandbox \"" + dir + "\"";
        psi.WorkingDirectory = dir;
        psi.UseShellExecute = false;
        try { Process.Start(psi); }
        catch (Exception ex) { MessageBox.Show("?????" + ex.Message, "yuki", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }
}
