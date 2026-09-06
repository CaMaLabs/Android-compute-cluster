using System.Diagnostics;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;

namespace ComputeSwarmInstaller;

internal static class Program
{
    private const string ResourceName = "ComputeSwarmInstaller.install-windows-auto.ps1";

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint GetConsoleProcessList([Out] uint[] processList, uint processCount);

    private static bool LikelyDoubleClicked()
    {
        if (!OperatingSystem.IsWindows()) return false;
        try
        {
            var processes = new uint[4];
            return GetConsoleProcessList(processes, (uint)processes.Length) <= 1;
        }
        catch
        {
            return false;
        }
    }

    private static void PauseIfNeeded(bool likelyDoubleClicked)
    {
        if (!likelyDoubleClicked || Console.IsInputRedirected) return;
        Console.WriteLine();
        Console.Write("Press Enter to close...");
        Console.ReadLine();
    }

    public static int Main(string[] args)
    {
        var doubleClicked = LikelyDoubleClicked();
        Console.Title = "Compute Swarm Worker Installer";
        Console.WriteLine("Compute Swarm Worker Installer");
        Console.WriteLine("CaMaLabs Android-compute-cluster");
        Console.WriteLine();

        if (!OperatingSystem.IsWindows())
        {
            Console.Error.WriteLine("This installer only runs on Windows.");
            PauseIfNeeded(doubleClicked);
            return 1;
        }

        if (args.Any(a => string.Equals(a, "/?", StringComparison.OrdinalIgnoreCase) ||
                          string.Equals(a, "-h", StringComparison.OrdinalIgnoreCase) ||
                          string.Equals(a, "--help", StringComparison.OrdinalIgnoreCase)))
        {
            Console.WriteLine("Double-click with no arguments for the configured controller:");
            Console.WriteLine("  http://45.50.0.74:8765");
            Console.WriteLine();
            Console.WriteLine("Advanced PowerShell installer parameters can be forwarded, for example:");
            Console.WriteLine("  ComputeSwarmWorkerInstaller.exe -ControllerUrl https://example.com:8765");
            Console.WriteLine("  ComputeSwarmWorkerInstaller.exe -PairingTimeoutMinutes 30");
            PauseIfNeeded(doubleClicked);
            return 0;
        }

        var tempDir = Path.Combine(Path.GetTempPath(), "ComputeSwarmInstaller", Guid.NewGuid().ToString("N"));
        var scriptPath = Path.Combine(tempDir, "install-windows-auto.ps1");

        try
        {
            Directory.CreateDirectory(tempDir);
            using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(ResourceName)
                ?? throw new InvalidOperationException($"Embedded installer resource '{ResourceName}' was not found.");
            using var reader = new StreamReader(stream, Encoding.UTF8, detectEncodingFromByteOrderMarks: true);
            var script = reader.ReadToEnd();
            File.WriteAllText(scriptPath, script, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));

            var startInfo = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                UseShellExecute = false,
                WorkingDirectory = tempDir,
            };
            startInfo.ArgumentList.Add("-NoLogo");
            startInfo.ArgumentList.Add("-NoProfile");
            startInfo.ArgumentList.Add("-ExecutionPolicy");
            startInfo.ArgumentList.Add("Bypass");
            startInfo.ArgumentList.Add("-File");
            startInfo.ArgumentList.Add(scriptPath);
            foreach (var arg in args)
                startInfo.ArgumentList.Add(arg);

            using var process = Process.Start(startInfo)
                ?? throw new InvalidOperationException("Failed to start Windows PowerShell.");
            process.WaitForExit();

            if (process.ExitCode == 0)
            {
                Console.WriteLine();
                Console.WriteLine("Compute Swarm worker installation completed successfully.");
            }
            else
            {
                Console.Error.WriteLine();
                Console.Error.WriteLine($"Installer exited with code {process.ExitCode}.");
            }

            PauseIfNeeded(doubleClicked);
            return process.ExitCode;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine();
            Console.Error.WriteLine("Installation failed:");
            Console.Error.WriteLine(ex.Message);
            PauseIfNeeded(doubleClicked);
            return 1;
        }
        finally
        {
            try
            {
                if (Directory.Exists(tempDir)) Directory.Delete(tempDir, recursive: true);
            }
            catch
            {
                // Best-effort cleanup only.
            }
        }
    }
}
